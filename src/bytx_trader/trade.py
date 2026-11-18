"""Steps that lead to an order: size, risk, broker. The gates live here, not in any SKILL.md.

An order is sent only by confirm(), and only when: the risk check passed this exact ticket today,
execute() issued a one-time token for it, and the user's own reply is a plain yes.
"""
from __future__ import annotations

import hashlib
import json
import re
import secrets
from datetime import datetime, timezone
from typing import Any, Optional

from . import core, integrity

TOKEN_TTL_MS = 300_000
SENT = ('placed', 'unknown', 'reconciled_filled', 'reconciled_pending')  # an order that may be on the exchange
FINGERPRINT_KEYS = ('kind', 'symbol', 'side', 'risk_usdt', 'stop', 'take_profit', 'leverage', 'mode', 'size')


def fingerprint(t: dict) -> str:
    return hashlib.sha256(json.dumps({k: t.get(k) for k in FINGERPRINT_KEYS}, sort_keys=True).encode()).hexdigest()[:16]


def client_id(exchange: str, ticket_id: str) -> str:
    """One ticket → one client_id, in the exchange's format, so a timeout is reconciled instead of resent."""
    h = hashlib.sha256(ticket_id.encode()).hexdigest()
    if exchange == 'hyperliquid':
        return '0x' + h[:32]
    if exchange == 'htx':
        return str(10 ** 17 + int(h, 16) % (9 * 10 ** 17))  # USDT-M wants an integer
    return ('t-' if exchange == 'gate' else '') + 'bt' + ticket_id[:14]


def _today(ms: Optional[int] = None) -> str:
    return datetime.fromtimestamp((ms or core.now_ms()) / 1000, timezone.utc).strftime('%Y-%m-%d')


# ─── 6. size ─────────────────────────────────────────────────────────────

async def size(risk_usdt: Optional[float] = None, leverage: Optional[float] = None) -> dict:
    """Size from risk with bytx.risk_size on a paper account (no keys, no orders); writes crypto/orders.json."""
    cfg = core.load_config()
    plan = core.read('plan.json')
    if not plan:
        raise ValueError('no trade plan yet: run exit_plan for a symbol first')
    risk_usdt = float(risk_usdt or cfg['risk']['max_loss_per_trade'])
    leverage = float(leverage or cfg['default_leverage'])
    # sized on the account it will trade (paper or demo book); real mode sizes on a paper copy of the live book
    async with core.open_exchange(cfg if cfg['mode'] in ('paper', 'demo') else {**cfg, 'mode': 'paper'}, 'trade') as ex:
        rp = await ex.risk_size(plan['symbol'], plan['side'], risk_usdt=risk_usdt, stop=plan['stop'],
                                take_profit=plan['take_profit'], leverage=leverage)
    async with core.open_exchange(cfg, 'data') as ex:
        fr = await ex.funding_rate(plan['symbol'])
    t = {'kind': 'open', 'symbol': rp.symbol, 'side': plan['side'], 'risk_usdt': risk_usdt, 'stop': rp.stop_price,
         'take_profit': rp.take_profit, 'leverage': leverage, 'mode': cfg['mode'],
         'entry_est': rp.entry_price, 'size_est': rp.size, 'notional_est': rp.notional, 'margin_est': rp.margin,
         'liquidation_est': rp.liquidation_price, 'loss_at_stop_est': rp.risk, 'fees_est': rp.fees,
         'funding_rate': fr.rate, 'funding_per_period_est': None if fr.rate is None else rp.notional * fr.rate,
         'flat_by': plan.get('flat_by'), 'created': core.now_ms()}
    t['id'] = fingerprint(t)
    core.write('orders.json', [t])
    t['text'] = ticket_text(t)
    return t


def ticket_text(t: dict) -> str:
    if t['kind'] == 'close':
        return f"[{t['mode'].upper()}] CLOSE {t['side']} {t['symbol']} {t['size']} at market (reduce-only)"
    pay = t.get('funding_per_period_est')
    pay_s = '' if pay is None else f", funding ≈ {abs(pay):.2f} USDT per period ({'you pay' if (pay > 0) == (t['side'] == 'long') else 'you receive'})"
    return (f"[{t['mode'].upper()}] OPEN {t['side'].upper()} {t['symbol']} ≈{t['size_est']} at ≈{t['entry_est']} "
            f"(≈{t['notional_est']:.2f} USDT, {t['leverage']:g}x, margin ≈{t['margin_est']:.2f}); "
            f"stop {t['stop']}, take-profit {t['take_profit']}; loss at the stop ≈{t['loss_at_stop_est']:.2f} USDT "
            f"incl. fees (cap {t['risk_usdt']}); liquidation ≈{_num(t['liquidation_est'])}{pay_s}")


def _num(x: Optional[float]) -> str:
    return 'n/a' if x is None else f'{x:.6g}'


# ─── 7. risk ─────────────────────────────────────────────────────────────

def _log() -> list[dict]:
    return (core.read('state/broker.json') or {}).get('log', [])


def check_ticket(t: dict, cfg: dict, log: list[dict], now: int) -> list[str]:
    """Reasons to block; empty = PASS. Getting out (kind=close) is never blocked by limits."""
    if t['kind'] == 'close':
        pos = (core.read('state/broker.json') or {}).get('positions', {})
        return [] if t['symbol'] in pos else ['no open position recorded for this symbol (already closed?)']
    r, why = cfg['risk'], []
    last = {e['id']: e for e in log if e['date'] == _today(now) and e['kind'] == 'open'}  # latest word per ticket
    today = [e for e in last.values() if e['status'] in SENT]
    if t['mode'] == 'real' and not cfg.get('allow_real'):
        why.append('real mode is off (allow_real=false)')
    if t['mode'] != cfg['mode']:
        why.append(f"ticket was sized for {t['mode']}, config is now {cfg['mode']}: size it again")
    if not t.get('stop'):
        why.append('no stop')
    if t['symbol'] in r['blocked_symbols'] or t['symbol'].split(':')[0] in r['blocked_symbols']:
        why.append(f"{t['symbol']} is blocked")
    if t['risk_usdt'] > r['max_loss_per_trade']:
        why.append(f"risk {t['risk_usdt']} > max loss per trade {r['max_loss_per_trade']}")
    used = sum(e['risk_usdt'] for e in today)
    if used + t['risk_usdt'] > r['daily_loss_limit']:
        why.append(f"today's risk {used} + {t['risk_usdt']} > daily loss limit {r['daily_loss_limit']}")
    if len(today) >= r['max_trades_per_day']:
        why.append(f"{len(today)} trades today, max {r['max_trades_per_day']}")
    if any(e['id'] == t['id'] for e in today):
        why.append('this exact order was already sent today (or its outcome is unknown: reconcile first)')
    if t['leverage'] > r['max_leverage']:
        why.append(f"leverage {t['leverage']:g}x > max {r['max_leverage']}x")
    if t['notional_est'] > r['max_notional']:
        why.append(f"notional {t['notional_est']:.2f} > max {r['max_notional']}")
    if t['margin_est'] and t['margin_est'] > cfg['account_usdt']:
        why.append(f"margin {t['margin_est']:.2f} > account {cfg['account_usdt']}")
    risk_dist = abs(t['entry_est'] - t['stop'])
    if t.get('take_profit') is None:
        if r['min_rr']:
            why.append('no take-profit, reward:risk unknown')
    elif risk_dist and abs(t['take_profit'] - t['entry_est']) / risk_dist < r['min_rr']:
        why.append(f"reward:risk {abs(t['take_profit'] - t['entry_est']) / risk_dist:.2f} < min {r['min_rr']}")
    liq = t.get('liquidation_est')
    if liq and ((t['side'] == 'long' and t['stop'] <= liq) or (t['side'] == 'short' and t['stop'] >= liq)):
        why.append(f"stop {t['stop']} is beyond the liquidation estimate {liq}: lower the leverage")
    win = core.session_window(cfg, now)
    if now > win['entry_until']:
        why.append(f"outside the entry window (until {core.iso(win['entry_until'])})")
    if t.get('flat_by') and now >= t['flat_by']:
        why.append('past the flat-by time of the plan')
    return why


def risk_check() -> dict:
    cfg, now = core.load_config(), core.now_ms()
    tickets = core.read('orders.json') or []
    if not tickets:
        raise ValueError('no ticket yet: run size first (after exit_plan)')
    state = core.read('state/risk.json') or {}
    rows = []
    for i, t in enumerate(tickets, 1):
        why = check_ticket(t, cfg, _log(), now)
        state[t['id']] = {'verdict': 'BLOCK' if why else 'PASS', 'reasons': why, 'date': _today(now), 'at': now}
        rows.append({'index': i, 'id': t['id'], 'verdict': state[t['id']]['verdict'], 'reasons': why})
    core.write('state/risk.json', state)
    return {'rows': rows, 'text': '\n'.join(
        f"#{r['index']} {r['verdict']}" + (': ' + '; '.join(r['reasons']) if r['reasons'] else '') for r in rows)}


# ─── 8. broker ───────────────────────────────────────────────────────────

YES = re.compile(r"^\s*(yes|y|yep|confirm(ed)?|go|do it|place it|да|ага|подтверждаю|давай|ок|ok)\b[\s.!]*", re.I)
NO = re.compile(r"\b(no|not|don'?t|cancel|stop|wait|нет|не|стоп|отмена|подожди)\b", re.I)


def is_yes(reply: str) -> bool:
    return bool(YES.match(reply or '')) and not NO.search(reply or '')


def _ready(t: dict, now: int) -> bool:
    v = (core.read('state/risk.json') or {}).get(t['id'])
    return bool(v) and v['verdict'] == 'PASS' and v['date'] == _today(now)


def tickets_show() -> dict:
    now = core.now_ms()
    tickets = core.read('orders.json') or []
    rows = [{'index': i, 'id': t['id'], 'ready': _ready(t, now), 'ticket': ticket_text(t)} for i, t in enumerate(tickets, 1)]
    return {'rows': rows, 'text': '\n'.join(
        f"#{r['index']} {'READY' if r['ready'] else 'NOT READY (run the risk check)'}: {r['ticket']}" for r in rows) or 'no tickets'}


def execute(index: int = 1) -> dict:
    """Step one of two: nothing is sent. Returns the ticket and a one-time token for confirm()."""
    now = core.now_ms()
    tickets = core.read('orders.json') or []
    if not 1 <= index <= len(tickets):
        raise ValueError(f'no ticket #{index}: run size first (tickets_show lists them)')
    t = tickets[index - 1]
    if not _ready(t, now):
        raise PermissionError('this ticket has no PASS from the risk check today: run risk_check first')
    tok = secrets.token_urlsafe(12)
    toks = {k: v for k, v in (core.read('state/tokens.json') or {}).items() if v['expires'] > now}
    toks[tok] = {'id': t['id'], 'index': index, 'expires': now + TOKEN_TTL_MS}
    core.write('state/tokens.json', toks)
    return {'confirm_token': tok, 'expires_in_s': TOKEN_TTL_MS // 1000, 'ticket': ticket_text(t),
            'text': f"{ticket_text(t)}\nNothing is sent yet. Ask the user to answer yes or no for exactly this order, "
                    f"then call confirm with this token and the user's reply word for word."}


def _record(entry: dict) -> None:
    st = core.read('state/broker.json') or {'log': [], 'positions': {}}
    st['log'].append(entry)
    core.write('state/broker.json', st)


async def confirm(confirm_token: str, user_reply: str) -> dict:
    """Step two: checks the token, the user's reply and the risk verdict again, then sends exactly that ticket."""
    cfg, now = core.load_config(), core.now_ms()
    toks = core.read('state/tokens.json') or {}
    tk = toks.pop(confirm_token, None)
    core.write('state/tokens.json', toks)  # one use, even when it fails below
    if not tk or tk['expires'] < now:
        raise PermissionError('unknown or expired token: call execute again')
    tickets = core.read('orders.json') or []
    t = next((x for x in tickets if x['id'] == tk['id']), None)
    if t is None:
        raise PermissionError('the ticket changed since execute: show it to the user again')
    base = {'id': t['id'], 'kind': t['kind'], 'symbol': t['symbol'], 'side': t['side'], 'mode': t['mode'],
            'risk_usdt': t.get('risk_usdt', 0), 'reply': user_reply, 'date': _today(now), 'at': now}
    if not is_yes(user_reply):
        _record({**base, 'status': 'declined'})
        return {'status': 'declined', 'text': 'Not sent: the reply is not a plain yes.'}
    why = check_ticket(t, cfg, _log(), now)
    if why:
        raise PermissionError('risk check fails now: ' + '; '.join(why))
    if t['mode'] not in ('paper', 'demo'):
        raise PermissionError('real orders are not sent by bytx-trader: use the hosted BYTtrade MCP')
    integrity.check()
    cid = client_id(cfg['exchange'], t['id'])
    from bytx.errors import BytxError, OrderStatusUnknown
    async with core.open_exchange(cfg, 'trade') as ex:
        try:
            if t['kind'] == 'close':
                res = await _close(ex, t, cfg)
            else:
                r = await ex.open_position(t['symbol'], t['side'], risk_usdt=t['risk_usdt'], stop=t['stop'],
                                           take_profit=t['take_profit'], leverage=t['leverage'], client_id=cid)
                res = {'status': 'placed' if r.status == 'opened' else 'not_filled', 'filled_size': r.filled_size,
                       'average': r.average, 'risk': r.risk, 'warnings': list(r.warnings)}
        except OrderStatusUnknown as e:
            _record({**base, 'status': 'unknown', 'client_id': cid, 'ticket': t})
            return {'status': 'unknown', 'client_id': cid, 'text':
                    f'The exchange did not say whether the order went through ({e}). It is NOT resent. '
                    f'Run reconcile for client_id {cid} before doing anything else.'}
        except BytxError as e:
            _record({**base, 'status': 'failed', 'error': f'{type(e).__name__}: {e}'})
            raise
    _record({**base, **res, 'client_id': cid})
    if t['kind'] == 'open' and res['status'] == 'placed':
        _set_position(t, res)
    elif t['kind'] == 'close':
        _set_position(t, None)
    fill = (f"filled {res['filled_size']} @ {res['average']}" if res.get('average')
            else 'sent; the exchange reports the fill shortly, check positions')
    return {**res, 'client_id': cid, 'text': f"{res['status'].upper()}: {ticket_text(t)}; {fill}"}


async def _close(ex: Any, t: dict, cfg: dict) -> dict:
    if cfg['mode'] == 'paper':
        # shortcut: a paper account lives only inside one process, so the exit is a fresh opposite market order
        # on the live book (real fill price and fee); upgrade if paper state is persisted across runs.
        await ex.set_leverage(t['symbol'], t['position_leverage'] or 1)
        o = await (ex.sell if t['side'] == 'long' else ex.buy)(t['symbol'], size=t['size'])
    else:
        # close only what this pack opened: the account may also hold the user's own or a bot's position
        from bytx.errors import PositionAlreadyClosed
        try:
            o = await (ex.sell if t['side'] == 'long' else ex.buy)(t['symbol'], size=t['size'], reduce_only=True)
        except PositionAlreadyClosed:
            o = None
        if not [p for p in await ex.positions([t['symbol']]) if p.size]:
            # Binance & co. keep a closed position's separate TP/SL; left there, they would hit the next position
            await ex.cancel_tp_sl(t['symbol'])
        if o is None:
            return {'status': 'no_position', 'filled_size': None, 'average': None}  # already closed on the exchange
    return {'status': 'placed', 'filled_size': getattr(o, 'filled', None), 'average': getattr(o, 'average', None)}


def _set_position(t: dict, res: Optional[dict]) -> None:
    st = core.read('state/broker.json')
    if res is None:
        st['positions'].pop(t['symbol'], None)
    else:
        st['positions'][t['symbol']] = {'side': t['side'], 'size': res['filled_size'], 'entry': res['average'],
                                        'stop': t['stop'], 'take_profit': t['take_profit'], 'mode': t['mode'],
                                        'leverage': t['leverage'],
                                        'flat_by': t.get('flat_by')}
    core.write('state/broker.json', st)


def flatten(symbol: Optional[str] = None) -> dict:
    """Write close tickets for open positions (all, or one symbol). They still need execute + confirm."""
    cfg = core.load_config()
    pos = (core.read('state/broker.json') or {}).get('positions', {})
    tickets = []
    for sym, p in pos.items():
        if symbol and symbol not in (sym, sym.split(':')[0]):
            continue
        t = {'kind': 'close', 'symbol': sym, 'side': p['side'], 'size': p['size'], 'mode': p['mode'],
             'risk_usdt': 0, 'stop': None, 'take_profit': None, 'leverage': None,
             'position_leverage': p.get('leverage'), 'created': core.now_ms()}
        if p['mode'] != cfg['mode']:
            raise ValueError(f"{sym} was opened in {p['mode']}, config is {cfg['mode']}")
        t['id'] = fingerprint(t)
        tickets.append(t)
    if not tickets:
        return {'rows': [], 'text': 'no open positions recorded'}
    core.write('orders.json', tickets)
    out = risk_check()  # close tickets always PASS; this records the verdict so execute accepts them
    out['text'] = 'Close tickets written:\n' + tickets_show()['text']
    return out


async def positions() -> dict:
    cfg = core.load_config()
    if cfg['mode'] != 'paper':
        return await _exchange_positions(cfg)
    pos = (core.read('state/broker.json') or {}).get('positions', {})
    rows = []
    if pos:
        async with core.open_exchange(cfg, 'data') as ex:
            for sym, p in pos.items():
                px = (await ex.ticker(sym)).last
                sign = 1 if p['side'] == 'long' else -1
                hit = ('stop' if sign * (px - p['stop']) <= 0 else
                       'target' if p['take_profit'] and sign * (px - p['take_profit']) >= 0 else None)
                rows.append({'symbol': sym, **p, 'price': px, 'pnl_est': sign * (px - p['entry']) * p['size'],
                             'level_crossed': hit, 'past_flat_by': bool(p.get('flat_by') and core.now_ms() >= p['flat_by'])})
    note = ('Paper positions are tracked here, not by the exchange: stop/target are not watched between runs; '
            'if a level is crossed or flat-by passed, flatten.' if cfg['mode'] == 'paper' else '')
    text = '\n'.join(
        f"{r['symbol']} {r['side']} {r['size']} @ {r['entry']}: now {r['price']}, PnL ≈{r['pnl_est']:.2f}"
        + (f", {r['level_crossed']} crossed" if r['level_crossed'] else '')
        + (', past flat-by' if r['past_flat_by'] else '') for r in rows) or 'no open positions recorded'
    return {'rows': rows, 'text': f'{text}\n{note}' if rows and note else text}


async def _exchange_positions(cfg: dict) -> dict:
    """Demo: the exchange is the truth. A recorded position the exchange no longer has was closed there
    (stop, take-profit, liquidation or by hand) and is dropped from the record."""
    st = core.read('state/broker.json') or {'log': [], 'positions': {}}
    rows, lines = [], []
    async with core.open_exchange(cfg, 'trade') as ex:
        live = {p.symbol: p for p in await ex.positions() if p.size}
        for sym in [s for s in st['positions'] if s not in live]:
            st['positions'].pop(sym)
            left = await ex.tp_sl_orders(sym)
            if left:  # the other half of a bracket outlives the position on some exchanges: it would hit the next one
                await ex.cancel_tp_sl(sym)
            lines.append(f'{sym}: closed on the exchange (stop, take-profit, liquidation or by hand); removed from the '
                         f'record' + (f', {len(left)} leftover TP/SL order(s) canceled' if left else ''))
    for sym, p in live.items():
        mine = sym in st['positions']
        rows.append({'symbol': sym, 'side': p.side, 'size': p.size, 'entry': p.entry_price, 'mark': p.mark_price,
                     'unrealized_pnl': p.unrealized_pnl, 'liquidation': p.liquidation_price, 'opened_here': mine})
        lines.append(f"{sym} {p.side} {p.size} @ {p.entry_price}: mark {p.mark_price}, PnL {p.unrealized_pnl}, "
                     f"liquidation {p.liquidation_price}" + ('' if mine else ' (not opened by bytx-trader)')
                     + (f" (bytx-trader opened {st['positions'][sym]['size']} of it)"
                        if mine and abs(st['positions'][sym]['size'] - p.size) > 1e-12 else ''))
    core.write('state/broker.json', st)
    return {'rows': rows, 'text': '\n'.join(lines) or 'no open positions on the exchange'}


async def reconcile(client_id: str) -> dict:
    """Find an order whose outcome was unknown, by its client_id. Never resends."""
    cfg = core.load_config()
    entry = next((e for e in reversed(_log()) if e.get('client_id') == client_id), None)
    if not entry:
        raise ValueError(f'no order with client_id {client_id} in the log')
    if cfg['mode'] == 'paper':
        return {'status': entry['status'], 'text': 'paper orders never reach an exchange; nothing to reconcile'}
    from bytx.errors import OrderNotFound
    t = entry.get('ticket') or {}
    async with core.open_exchange(cfg, 'trade') as ex:
        try:
            o = await ex.order(entry['symbol'], client_id=client_id)
        except OrderNotFound:
            o = None
        stops = [x for x in await ex.tp_sl_orders(entry['symbol'])
                 if x.kind in ('stop_loss', 'tp_sl') and x.state in ('open', None)] if o and o.filled else []
    rec = dict(entry)  # keeps the ticket, so a second reconcile still knows the plan
    if o is None or (not o.filled and o.status in ('canceled', 'rejected', 'expired')):
        _record({**rec, 'status': 'reconciled_none', 'at': core.now_ms()})
        return {'status': 'not_on_exchange', 'text': f'{client_id}: the order is not on the exchange and nothing '
                                                     'filled. The ticket may be checked and sent again.'}
    if not o.filled:
        _record({**rec, 'status': 'reconciled_pending', 'order_status': o.status, 'at': core.now_ms()})
        return {'status': 'pending', 'text': f'{client_id}: on the exchange, {o.status}, nothing filled yet'}
    _record({**rec, 'status': 'reconciled_filled', 'order_status': o.status, 'at': core.now_ms()})
    if entry['kind'] == 'open' and t:
        _set_position(t, {'filled_size': o.filled, 'average': o.average})
    warn = ('' if stops else ' WARNING: no stop-loss found on the exchange for this position — '
            'flatten it or set the stop on the exchange now.')
    return {'status': 'filled', 'filled': o.filled, 'average': o.average, 'stop_on_exchange': bool(stops),
            'text': f'{client_id}: {o.status}, filled {o.filled} @ {o.average}; position recorded.{warn}'}


def settings(changes: Optional[dict] = None) -> dict:
    """Show the settings, or change some of them first (only known keys, checked). Keys are never set here."""
    import os
    cfg = core.update_config(changes) if changes else core.load_config()
    keys = [f'BYTX_{k.upper()}' for k in core.KEY_NAMES if os.environ.get(f'BYTX_{k.upper()}')]
    r, s = cfg['risk'], cfg['session']
    text = (f"{'Saved. ' if changes else ''}Mode {cfg['mode']} on {cfg['exchange']}"
            f"{' (virtual money, live order book)' if cfg['mode'] == 'paper' else ' (exchange demo/testnet)'}, "
            f"account {cfg['account_usdt']} USDT, leverage {cfg['default_leverage']}x {cfg['margin_mode']}.\n"
            f"Limits: max loss per trade {r['max_loss_per_trade']}, per day {r['daily_loss_limit']}, "
            f"{r['max_trades_per_day']} trades a day, reward:risk >= {r['min_rr']}, leverage <= {r['max_leverage']}, "
            f"notional <= {r['max_notional']}{', blocked ' + ', '.join(r['blocked_symbols']) if r['blocked_symbols'] else ''}.\n"
            f"Session (UTC): starts {s['start']}, opening range {s['range_minutes']} min on {s['timeframe']} bars, "
            f"no entries after {s['entry_until'] or 'any time'}, flat by {s['flat_by']}.\n"
            f"Demo keys in the server environment: {', '.join(keys) if keys else 'none'}"
            f"{'' if cfg['mode'] != 'demo' or keys else ' — demo needs them, set them in the MCP config, never in chat'}.\n"
            f"Data folder: {core.home()}")
    return {'config': cfg, 'home': str(core.home()), 'demo_keys': keys, 'text': text}
