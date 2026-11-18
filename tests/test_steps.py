"""The whole pipeline offline, on BTC 5m candles from Binance (2026-10-19 … 2026-11-18), clock 2026-11-18 13:00 UTC."""
from __future__ import annotations

import pytest
from bytx.errors import OrderStatusUnknown

from bytx_trader import core, market, trade
from conftest import NOW, SYM, load_bars


# ─── pure parts ──────────────────────────────────────────────────────────

def test_session_window_wraps_past_midnight():
    cfg = core.load_config()
    cfg['session'].update(start='20:00', flat_by='04:00')
    w = core.session_window(cfg, NOW)  # 13:00 → the session that began yesterday 20:00
    assert core.iso(w['start']) == '2026-11-17 20:00 UTC'
    assert core.iso(w['flat_by']) == '2026-11-18 04:00 UTC'


def test_orb_on_real_day():
    cfg = core.load_config()
    w = core.session_window(cfg, NOW)
    st = market.orb_state(load_bars(), w, 300_000, NOW)
    assert st['status'] == 'triggered'
    assert st['range_low'] < st['range_high']
    assert st['trigger_ts'] >= w['range_end']
    edge = st['range_high'] if st['side'] == 'long' else st['range_low']
    assert (st['trigger_close'] > edge) == (st['side'] == 'long')


def test_orb_forming_and_waiting():
    w = {'start': 0, 'range_end': 600_000, 'entry_until': 3_000_000, 'flat_by': 3_600_000}
    bs = [[i * 300_000, 100, 101, 99, 100, 1] for i in range(6)]
    assert market.orb_state(bs, w, 300_000, 500_000)['status'] == 'forming'
    assert market.orb_state(bs, w, 300_000, 1_800_000)['status'] == 'waiting'
    assert market.orb_state(bs, w, 300_000, 3_100_000)['status'] == 'expired'
    bs.append([1_800_000, 100, 103, 100, 102.5, 1])
    st = market.orb_state(bs, w, 300_000, 2_200_000)
    assert (st['status'], st['side'], st['trigger_close']) == ('triggered', 'long', 102.5)


def test_rsi_and_atr():
    up = [[i, i, i + 1, i - 1, i + 0.5, 1] for i in range(1, 30)]
    assert market.rsi([b[4] for b in up]) == 100.0
    assert market.atr(up) == pytest.approx(2.0)


def test_monte_carlo_is_repeatable_and_adds_up():
    bars = load_bars()
    start = core.session_window(core.load_config(), NOW)['start']
    days = market.by_day([b for b in bars if b[0] < start], start)
    a = market.monte_carlo(days, 300_000, 156, 287, 'long', 64000, 63700, 64600, n=2000, seed=19)
    b = market.monte_carlo(days, 300_000, 156, 287, 'long', 64000, 63700, 64600, n=2000, seed=19)
    assert a == b
    assert a['target'] + a['stop'] + a['open'] == pytest.approx(100, abs=0.2)
    assert a['sessions'] >= 30


def test_yes_words():
    for y in ('yes', 'Yes!', 'да', 'ok', 'confirm'):
        assert trade.is_yes(y), y
    for n in ('no', 'yes, but not now', 'да, но не сейчас', 'maybe', '', 'stop', 'wait, yes'):
        assert not trade.is_yes(n), n


# ─── the pipeline and its gates ──────────────────────────────────────────

async def _to_ticket(**size_kw):
    await market.scan(symbols=[SYM])
    await market.derivs([SYM])
    await market.entry_check(SYM)
    await market.exit_plan(SYM, stop='atr:2', target_r=2, side='long')
    tm = await market.timemachine(days=30, runs=1000)
    assert tm['monte_carlo']['runs'] == 1000
    return await trade.size(**size_kw)


async def test_pipeline_paper_open_and_close(env):
    t = await _to_ticket()
    assert t['loss_at_stop_est'] <= 10
    assert 'crowded_long' in core.read('derivs.json')['rows'][0]['flags']  # fixture funding 0.06 %
    with pytest.raises(PermissionError):           # no PASS yet
        trade.execute(1)
    assert trade.risk_check()['rows'][0]['verdict'] == 'PASS'
    tok = trade.execute(1)['confirm_token']
    assert env.sent == []                          # execute sends nothing
    res = await trade.confirm(tok, 'yes')
    assert res['status'] == 'placed' and len(env.sent) == 1
    assert core.read('state/broker.json')['positions'][SYM]['side'] == 'long'
    with pytest.raises(PermissionError):           # one token, one order
        await trade.confirm(tok, 'yes')
    assert 'already sent' in trade.risk_check()['text']   # same ticket again is blocked

    trade.flatten()
    tok = trade.execute(1)['confirm_token']
    assert (await trade.confirm(tok, 'да'))['status'] == 'placed'
    assert core.read('state/broker.json')['positions'] == {}
    assert trade.risk_check()['rows'][0]['verdict'] == 'BLOCK'  # nothing left to close


async def test_no_is_no(env):
    await _to_ticket()
    trade.risk_check()
    res = await trade.confirm(trade.execute(1)['confirm_token'], 'no, wait')
    assert res['status'] == 'declined' and env.sent == []


async def test_unknown_outcome_is_never_resent(env, monkeypatch):
    await _to_ticket()
    trade.risk_check()
    calls = []

    def boom(cfg, kind):
        class Ex:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return None

            async def open_position(self, *a, **kw):
                calls.append(kw['client_id'])
                raise OrderStatusUnknown('timeout')
        return Ex()

    monkeypatch.setattr(core, 'open_exchange', boom)
    res = await trade.confirm(trade.execute(1)['confirm_token'], 'yes')
    assert res['status'] == 'unknown' and len(calls) == 1
    assert 'reconcile first' in trade.risk_check()['text']
    with pytest.raises(PermissionError):
        trade.execute(1)


async def test_risk_blocks(env):
    t = await _to_ticket(risk_usdt=10, leverage=5)
    cfg, log = core.load_config(), []
    assert trade.check_ticket(t, cfg, log, NOW) == []
    cases = {
        'leverage': {**t, 'leverage': 20},
        'max loss per trade': {**t, 'risk_usdt': 50},
        'liquidation': {**t, 'liquidation_est': t['stop'] + 1},
        'no stop': {**t, 'stop': None, 'entry_est': t['entry_est']},
        'reward:risk': {**t, 'take_profit': t['entry_est'] + 0.5 * abs(t['entry_est'] - t['stop'])},
        'real mode is off': {**t, 'mode': 'real'},
        'blocked': t,
    }
    cfg['risk']['blocked_symbols'] = ['BTC/USDT']
    for needle, ticket in cases.items():
        if needle == 'no stop':
            ticket = {**ticket, 'stop': 0}
        why = trade.check_ticket(ticket, cfg if needle == 'blocked' else core.load_config(), log, NOW)
        assert any(needle in w for w in why), (needle, why)
    full = [{'date': '2026-11-18', 'kind': 'open', 'status': 'placed', 'risk_usdt': 10, 'id': str(i)} for i in range(3)]
    why = trade.check_ticket(t, core.load_config(), full, NOW)
    assert any('daily loss limit' in w for w in why) and any('trades today' in w for w in why)


async def test_confirm_refuses_real(env):
    t = await _to_ticket()
    core.write('config.json', {'account_usdt': 5000, 'mode': 'real', 'allow_real': True})
    core.write('orders.json', [{**t, 'mode': 'real', 'id': trade.fingerprint({**t, 'mode': 'real'})}])
    trade.risk_check()
    with pytest.raises(PermissionError, match='hosted BYTtrade MCP'):
        await trade.confirm(trade.execute(1)['confirm_token'], 'yes')
    assert env.sent == []


async def test_mcp_server_lists_steps(env):
    from mcp.client.client import Client
    from bytx_trader.mcp_server import mcp
    async with Client(mcp) as c:
        names = {t.name for t in (await c.list_tools()).tools}
        assert {'scan', 'size', 'risk_check', 'execute', 'confirm'} <= names
        assert 'trade_pipeline' in {p.name for p in (await c.list_prompts()).prompts}
        r = await c.call_tool('tickets_show', {})
        assert not r.is_error and 'no tickets' in r.content[0].text


async def test_readme_example(env, monkeypatch):
    """Every number of the README walkthrough: BTC on Binance, 2026-11-18 01:05 UTC."""
    from conftest import FakeData
    now = 1794963900000
    monkeypatch.setenv('BYTX_TRADER_NOW_MS', str(now))
    bars = load_bars()
    monkeypatch.setattr(core, 'open_exchange', lambda cfg, kind: FakeData(bars, now))
    e = await market.entry_check(SYM)
    assert (e['status'], e['side'], e['trigger_close'], e['range_low'], e['range_high']) == \
        ('triggered', 'short', 64588.5, 64673.5, 64976.3)
    p = await market.exit_plan(SYM, stop='range-mid', target_r=2)
    assert (p['entry'], round(p['stop'], 1), round(p['take_profit'], 1)) == (64588.5, 64824.9, 64115.7)
    tm = await market.timemachine()
    assert {k: tm['monte_carlo'][k] for k in ('target', 'stop', 'open')} == {'target': 37.6, 'stop': 62.4, 'open': 0.0}
    assert tm['history'] == {'fired': 30, 'target': 9, 'stop': 21, 'open': 0}
    assert tm['expectancy_r'] == {'monte_carlo': 0.13, 'history': -0.1}


def test_update_notice(tmp_path, monkeypatch):
    from importlib.metadata import version
    from bytx_trader import updates
    monkeypatch.setattr(updates, 'CACHE', tmp_path / 'update.json')
    mine, lib = version('bytx-trader'), version('bytx')
    major, minor = (int(x) for x in lib.split('.')[:2])
    rel = {'bytx-trader': [mine, '99.0.0', '100.0.0rc1'], 'bytx': [lib, f'{major}.{minor}.999', f'{major}.{minor + 1}.0']}
    msg = updates.notice(lambda name: rel[name], now=1000)
    assert 'bytx-trader 99.0.0' in msg and f'bytx {major}.{minor}.999' in msg
    assert 'rc1' not in msg and f'{major}.{minor + 1}.0' not in msg   # no pre-releases, no new series
    assert updates.notice(lambda name: [], now=2000) == msg            # cached for a day
    monkeypatch.setattr(updates, 'CACHE', tmp_path / 'other.json')
    assert updates.notice(lambda name: {'bytx-trader': [mine], 'bytx': [lib]}[name], now=1000) is None

    def offline(name):
        raise OSError('no network')
    monkeypatch.setattr(updates, 'CACHE', tmp_path / 'third.json')
    assert updates.notice(offline, now=1000) is None


async def test_mcp_shows_notice_once(env, monkeypatch):
    from mcp.client.client import Client
    from bytx_trader import mcp_server
    monkeypatch.setitem(mcp_server._pending, 'notice', 'Update available: bytx 0.2.9')
    async with Client(mcp_server.mcp) as c:
        first = (await c.call_tool('tickets_show', {})).content[0].text
        second = (await c.call_tool('tickets_show', {})).content[0].text
    assert 'Update available' in first and 'Update available' not in second


# ─── 0.1.1: fresh setups, demo positions from the exchange, reconcile, keys ─

async def test_scanner_lists_only_fresh_breakouts(env, monkeypatch):
    from conftest import FakeData
    stale = await market.scan(symbols=[SYM])            # 13:00, the breakout was at 01:00
    assert stale['hits'] == []
    now = 1794963900000                                  # 01:05, the 01:00 bar just closed
    monkeypatch.setenv('BYTX_TRADER_NOW_MS', str(now))
    monkeypatch.setattr(core, 'open_exchange', lambda cfg, kind: FakeData(load_bars(), now))
    assert (await market.scan(symbols=[SYM]))['hits'][0]['setups'] == ['orb_short']


class _Ex:
    """A demo account stand-in: just what positions() and reconcile() call."""

    def __init__(self, positions=(), order=None, stops=()):
        self._p, self._o, self._s = list(positions), order, list(stops)
        self.canceled = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return None

    async def positions(self, symbols=None):
        return self._p

    async def order(self, symbol, client_id=None):
        if self._o is None:
            from bytx.errors import OrderNotFound
            raise OrderNotFound('not found')
        return self._o

    async def tp_sl_orders(self, symbol):
        return self._s

    async def cancel_tp_sl(self, symbol):
        self.canceled.append(symbol)
        self._s = []

    async def sell(self, symbol, size=None, reduce_only=False):
        from bytx.errors import PositionAlreadyClosed
        assert reduce_only
        raise PositionAlreadyClosed('flat')


def _demo_with(monkeypatch, ex):
    core.write('config.json', {'account_usdt': 5000, 'mode': 'demo'})
    monkeypatch.setattr(core, 'open_exchange', lambda cfg, kind: ex)


async def test_demo_positions_follow_the_exchange(env, monkeypatch):
    from types import SimpleNamespace as NS
    core.write('state/broker.json', {'log': [], 'positions': {SYM: {'side': 'long', 'size': 0.03, 'entry': 64000,
                                                                     'stop': 63700, 'take_profit': 64600, 'mode': 'demo'}}})
    ex = _Ex(stops=[NS(kind='take_profit', state='open')])   # the TP outlived a stop-out
    _demo_with(monkeypatch, ex)
    out = await trade.positions()
    assert 'closed on the exchange' in out['text'] and core.read('state/broker.json')['positions'] == {}
    assert ex.canceled == [SYM] and '1 leftover' in out['text']
    eth = NS(symbol='ETH/USDT:USDT', side='short', size=1.0, entry_price=1800, mark_price=1790,
             unrealized_pnl=10.0, liquidation_price=2100)
    _demo_with(monkeypatch, _Ex(positions=[eth]))
    assert 'not opened by bytx-trader' in (await trade.positions())['text']


def _unknown_entry(t):
    return {'id': t['id'], 'kind': 'open', 'symbol': t['symbol'], 'side': t['side'], 'mode': 'demo', 'risk_usdt': 10,
            'date': '2026-11-18', 'at': NOW, 'status': 'unknown', 'client_id': trade.client_id('binance', t['id']), 'ticket': t}


async def test_reconcile_filled_records_position_and_warns_without_stop(env, monkeypatch):
    from types import SimpleNamespace as NS
    t = await _to_ticket()
    t = {**t, 'mode': 'demo'}
    core.write('state/broker.json', {'log': [_unknown_entry(t)], 'positions': {}})
    _demo_with(monkeypatch, _Ex(order=NS(status='closed', filled=0.03, average=64010.0)))
    out = await trade.reconcile(trade.client_id('binance', t['id']))
    assert out['status'] == 'filled' and 'no stop-loss' in out['text']
    assert core.read('state/broker.json')['positions'][SYM]['size'] == 0.03
    assert any('already sent' in w for w in trade.check_ticket(t, core.load_config(), trade._log(), NOW))


async def test_reconcile_not_found_frees_the_ticket(env, monkeypatch):
    t = {**(await _to_ticket()), 'mode': 'demo'}
    core.write('state/broker.json', {'log': [_unknown_entry(t)], 'positions': {}})
    _demo_with(monkeypatch, _Ex(order=None))
    assert (await trade.reconcile(trade.client_id('binance', t['id'])))['status'] == 'not_on_exchange'
    assert not any('already sent' in w for w in trade.check_ticket(t, core.load_config(), trade._log(), NOW))


def test_demo_keys_from_env(monkeypatch):
    real = core  # no env fixture here, so open_exchange is the real factory
    seen = {}
    monkeypatch.setattr(real, '_create', lambda name, **kw: seen.update(name=name, **kw))
    for k in real.KEY_NAMES:
        monkeypatch.delenv(f'BYTX_{k.upper()}', raising=False)
    cfg = {**real.DEFAULT_CONFIG, 'mode': 'demo', 'exchange': 'hyperliquid'}
    with pytest.raises(RuntimeError, match='BYTX_WALLET_ADDRESS'):
        real.open_exchange(cfg, 'trade')
    monkeypatch.setenv('BYTX_WALLET_ADDRESS', '0xabc')
    monkeypatch.setenv('BYTX_PRIVATE_KEY', '0xdef')
    real.open_exchange(cfg, 'trade')
    assert seen == {'name': 'hyperliquid', 'demo': True, 'margin_mode': 'isolated',
                    'wallet_address': '0xabc', 'private_key': '0xdef'}


async def test_demo_close_cancels_leftover_tp_sl_only_when_flat(env, monkeypatch):
    from types import SimpleNamespace as NS
    t = {'kind': 'close', 'symbol': SYM, 'side': 'long', 'size': 0.03}
    ex = _Ex()
    _demo_with(monkeypatch, ex)
    assert (await trade._close(ex, t, core.load_config()))['status'] == 'no_position'
    assert ex.canceled == [SYM]
    other = _Ex(positions=[NS(symbol=SYM, size=0.5)])   # someone else's position on the same symbol stays protected
    assert (await trade._close(other, t, core.load_config()))['status'] == 'no_position'
    assert other.canceled == []


def test_client_id_fits_every_exchange():
    from bytx.order_params import client_id_format
    from bytx.testing import CCXT_IDS
    for name, ccxt_id in CCXT_IDS.items():
        cid = trade.client_id(name, 'bb2ba5a8e3695a77')
        assert client_id_format(ccxt_id).fullmatch(cid), (name, cid)
        assert cid == trade.client_id(name, 'bb2ba5a8e3695a77')       # deterministic
        assert cid != trade.client_id(name, 'cc2ba5a8e3695a77')


# ─── 0.1.3: easy for any agent — settings from chat, any symbol spelling, error reasons reach the model ─

def test_settings_change_and_refuse(env):
    from bytx_trader import trade
    r = trade.settings({'exchange': 'bybit', 'risk': {'max_loss_per_trade': 5}, 'session': {'start': '13:30'}})
    assert r['config']['exchange'] == 'bybit' and r['config']['risk']['max_loss_per_trade'] == 5
    assert core.load_config()['session']['start'] == '13:30' and 'Saved' in r['text']
    for bad in ({'mode': 'real'}, {'allow_real': True}, {'exchange': 'ftx'}, {'risk': {'max_loss_per_trade': 'five'}},
                {'session': {'start': '25:00'}}, {'session': {'timeframe': '7m'}}, {'nope': 1}, {'risk': 3}):
        with pytest.raises(ValueError):
            trade.settings(bad)
    assert core.load_config()['session']['start'] == '13:30'   # a refused change writes nothing
    assert trade.settings({'session': {'entry_until': None}})['config']['session']['entry_until'] is None


def test_symbol_any_spelling():
    cfg = dict(core.DEFAULT_CONFIG)
    for s in ('BTC', 'btc', 'BTCUSDT', 'btc-usdt', 'BTC/USDT'):
        assert core.norm_symbol(cfg, s) == 'BTC/USDT'
    assert core.norm_symbol({**cfg, 'exchange': 'hyperliquid'}, 'eth') == 'ETH/USDC'
    assert core.norm_symbol(cfg, 'BTC/USDT:USDT') == 'BTC/USDT:USDT'


def test_home_default(monkeypatch, tmp_path):
    monkeypatch.delenv('BYTX_TRADER_HOME', raising=False)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('HOME', str(tmp_path / 'u'))
    assert core.home() == tmp_path / 'u' / 'bytx-trader'     # e.g. Claude Desktop: no project folder
    (tmp_path / 'crypto').mkdir()
    assert core.home() == tmp_path / 'crypto'                # a project keeps its own crypto/


async def test_mcp_error_reason_reaches_the_model(env):
    from mcp.client.client import Client
    from bytx_trader.mcp_server import mcp
    async with Client(mcp) as c:
        r = await c.call_tool('size', {})
        assert r.is_error and 'run exit_plan' in r.content[0].text
        r = await c.call_tool('settings', {'changes': {'mode': 'real'}})
        assert r.is_error and 'paper or demo' in r.content[0].text
        r = await c.call_tool('settings', {})
        assert not r.is_error and 'Mode paper' in r.content[0].text
