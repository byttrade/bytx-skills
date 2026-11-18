"""Read-only steps: scanner, derivs, entry, exit plan, time machine. No orders anywhere in this module.

Candles inside are plain lists [ts, open, high, low, close, volume] so the pure functions run on fixtures.
"""
from __future__ import annotations

import asyncio
import random
import statistics
from typing import Any, Optional

from . import core

Bar = list  # [ts, o, h, l, c, v]


def bars(candles: list) -> list[Bar]:
    return [[c.timestamp, c.open, c.high, c.low, c.close, c.volume] for c in candles]


def closed(bs: list[Bar], tf: int, now: int) -> list[Bar]:
    return [b for b in bs if b[0] + tf <= now]


# ─── opening-range breakout, the setup the scanner, entry and time machine share ─

def orb_state(bs: list[Bar], win: dict, tf: int, now: int) -> dict:
    """Range of the first minutes of the session, then the first closed bar beyond it.

    status: forming (range not done) | waiting | triggered | expired (past the entry deadline, no trigger).
    """
    done = closed(bs, tf, now)
    rng = [b for b in done if win['start'] <= b[0] < win['range_end']]
    if now < win['range_end'] or not rng:
        return {'status': 'forming'}
    hi, lo = max(b[2] for b in rng), min(b[3] for b in rng)
    out = {'range_high': hi, 'range_low': lo, 'range_mid': (hi + lo) / 2}
    for b in done:
        if b[0] < win['range_end'] or b[0] + tf > win['entry_until']:
            continue
        if b[4] > hi or b[4] < lo:
            side = 'long' if b[4] > hi else 'short'
            edge = hi if side == 'long' else lo
            return {**out, 'status': 'triggered', 'side': side, 'trigger_ts': b[0], 'trigger_close': b[4],
                    'beyond_pct': round(abs(b[4] / edge - 1) * 100, 3)}
    return {**out, 'status': 'expired' if now > win['entry_until'] else 'waiting'}


def rsi(closes: list[float], n: int = 14) -> Optional[float]:
    if len(closes) <= n:
        return None
    gains = losses = 0.0
    for a, b in zip(closes[-n - 1:-1], closes[-n:]):
        d = b - a
        gains, losses = gains + max(d, 0), losses + max(-d, 0)
    return 100.0 if losses == 0 else 100 - 100 / (1 + gains / losses)


def atr(bs: list[Bar], n: int = 14) -> Optional[float]:
    if len(bs) <= n:
        return None
    trs = [max(b[2] - b[3], abs(b[2] - p[4]), abs(b[3] - p[4])) for p, b in zip(bs[-n - 1:-1], bs[-n:])]
    return sum(trs) / n


# ─── 1. scanner ──────────────────────────────────────────────────────────

async def _session_bars(ex: Any, symbol: str, cfg: dict, win: dict) -> list[Bar]:
    tf = cfg['session']['timeframe']
    limit = (core.now_ms() - win['start']) // core.tf_ms(tf) + 20  # session + RSI warm-up
    return bars(await ex.candles(symbol, tf, since=win['start'] - 20 * core.tf_ms(tf), limit=min(int(limit), 1000)))


async def scan(top: Optional[int] = None, symbols: Optional[list[str]] = None) -> dict:
    cfg = core.load_config()
    win, now, tf = core.session_window(cfg), core.now_ms(), core.tf_ms(cfg['session']['timeframe'])
    async with core.open_exchange(cfg, 'data') as ex:
        if not symbols:
            tick = await ex.tickers()
            usdt = [t for s, t in tick.items() if s.endswith('/USDT:USDT') and t.quote_volume]
            symbols = [t.symbol for t in sorted(usdt, key=lambda t: -t.quote_volume)[:top or cfg['scanner']['top']]]
        sem = asyncio.Semaphore(5)  # be polite to the public endpoint

        async def one(sym: str) -> dict:
            async with sem:
                try:
                    bs = await _session_bars(ex, sym, cfg, win)
                except Exception as e:  # one bad symbol must not sink the scan
                    return {'symbol': sym, 'error': f'{type(e).__name__}: {e}'}
            st = orb_state(bs, win, tf, now)
            r = rsi([b[4] for b in closed(bs, tf, now)])
            setups = []
            fresh = cfg['scanner']['fresh_bars'] * tf
            if st['status'] == 'triggered' and now - (st['trigger_ts'] + tf) <= fresh:
                setups.append(f"orb_{st['side']}")
            if r is not None and r < 30:
                setups.append('oversold')
            if r is not None and r > 70:
                setups.append('overbought')
            return {'symbol': sym, 'setups': setups, 'orb': st, 'rsi': None if r is None else round(r, 1)}

        rows = await asyncio.gather(*(one(s) for s in symbols))
    hits = sorted((r for r in rows if r.get('setups')), key=lambda r: -r['orb'].get('trigger_ts', 0))  # freshest first
    out = {'exchange': cfg['exchange'], 'at': core.iso(now), 'session_start': core.iso(win['start']),
           'scanned': len(rows), 'hits': hits, 'errors': [r for r in rows if 'error' in r]}
    core.write('scan.json', out)
    lines = [f"{out['at']}: {len(hits)} of {len(rows)} {cfg['exchange']} perps have a setup"]
    for h in hits[:20]:
        o = h['orb']
        extra = (f" at {core.iso(o['trigger_ts'])[11:]}, close {o['trigger_close']} ({o['beyond_pct']}% beyond the range)"
                 if o.get('status') == 'triggered' else '')
        lines.append(f"  {h['symbol']}: {', '.join(h['setups'])}{extra}, RSI {h['rsi']}")
    out['text'] = '\n'.join(lines)
    return out


# ─── 2. derivs: funding, open interest, basis ────────────────────────────

FUNDING_HOT = 0.0005  # 0.05 % per funding period: crowded side pays a lot to stay in


async def derivs(symbols: Optional[list[str]] = None) -> dict:
    cfg = core.load_config()
    symbols = symbols or core.read('watchlist.json') or [h['symbol'] for h in (core.read('scan.json') or {}).get('hits', [])][:10]
    if not symbols:
        raise ValueError('no symbols: pass them, write crypto/watchlist.json, or run the scanner first')
    prev = (core.read('state/derivs.json') or {}).get('oi', {})
    week_ago = core.now_ms() - 7 * 86_400_000
    rows = []
    async with core.open_exchange(cfg, 'data') as ex, core.open_exchange(cfg, 'spot') as spot:
        for sym in symbols:
            fr, oi, tk = await ex.funding_rate(sym), await ex.open_interest(sym), await ex.ticker(sym)
            hist = await ex.funding_history(sym, since=week_ago)
            try:
                spot_px = (await spot.ticker(sym.split(':')[0])).last
            except Exception:
                spot_px = None  # not every perp has a spot pair
            key = fr.symbol
            oi_chg = None
            if oi.size and prev.get(key):
                oi_chg = round((oi.size / prev[key] - 1) * 100, 2)
            flags = []
            if fr.rate is not None and fr.rate >= FUNDING_HOT:
                flags.append('crowded_long')
            if fr.rate is not None and fr.rate <= -FUNDING_HOT:
                flags.append('crowded_short')
            if oi_chg is not None and tk.change_pct is not None:
                if oi_chg > 3 and tk.change_pct < -1:
                    flags.append('oi_up_price_down')
                if oi_chg > 3 and tk.change_pct > 1:
                    flags.append('oi_up_price_up')
            rows.append({
                'symbol': key, 'price': tk.last, 'change_24h_pct': tk.change_pct,
                'funding_rate': fr.rate, 'next_funding': core.iso(fr.next_funding_time),
                'funding_7d_avg': statistics.fmean([h.rate for h in hist]) if hist else None,
                'open_interest': oi.size, 'oi_change_pct_since_last_run': oi_chg,
                'basis_pct': round((tk.last / spot_px - 1) * 100, 4) if spot_px and tk.last else None,
                'flags': flags,
            })
    core.write('state/derivs.json', {'at': core.now_ms(), 'oi': {r['symbol']: r['open_interest'] for r in rows}})
    out = {'at': core.iso(core.now_ms()), 'rows': rows}
    core.write('derivs.json', out)
    out['text'] = '\n'.join(
        f"{r['symbol']}: funding {_pct(r['funding_rate'])} (7d avg {_pct(r['funding_7d_avg'])}), "
        f"OI {r['open_interest']} ({'first run' if r['oi_change_pct_since_last_run'] is None else str(r['oi_change_pct_since_last_run']) + '%'}), "
        f"basis {r['basis_pct']}%{', ' + ', '.join(r['flags']) if r['flags'] else ''}" for r in rows)
    return out


def _pct(x: Optional[float]) -> str:
    return 'n/a' if x is None else f'{x * 100:.4f}%'


# ─── 3. entry ────────────────────────────────────────────────────────────

async def entry_check(symbol: str) -> dict:
    cfg = core.load_config()
    win, now, tf = core.session_window(cfg), core.now_ms(), core.tf_ms(cfg['session']['timeframe'])
    async with core.open_exchange(cfg, 'data') as ex:
        sym = (await ex.market(symbol)).symbol
        st = orb_state(await _session_bars(ex, sym, cfg, win), win, tf, now)
    out = {'symbol': sym, 'at': core.iso(now), **st}
    entry = core.read('entry.json') or {}
    entry[sym] = out
    core.write('entry.json', entry)
    if st['status'] == 'triggered':
        out['text'] = (f"{sym}: TRIGGERED {st['side']} — the {core.iso(st['trigger_ts'])} bar closed at "
                       f"{st['trigger_close']}, {st['beyond_pct']}% beyond the range {st['range_low']}–{st['range_high']}")
    elif st['status'] == 'forming':
        out['text'] = f"{sym}: the opening range is still forming (ends {core.iso(win['range_end'])})"
    else:
        out['text'] = (f"{sym}: {st['status'].upper()} — range {st['range_low']}–{st['range_high']}, "
                       f"entries until {core.iso(win['entry_until'])}")
    return out


# ─── 4. exit plan ────────────────────────────────────────────────────────

def stop_from(method: str, side: str, entry: float, orb: dict, atr_value: Optional[float]) -> float:
    """method: range-mid | range (the far edge) | atr:K | a price."""
    sign = -1 if side == 'long' else 1
    if method == 'range-mid':
        return orb['range_mid']
    if method == 'range':
        return orb['range_low'] if side == 'long' else orb['range_high']
    if method.startswith('atr'):
        k = float(method.split(':')[1]) if ':' in method else 1.5
        if not atr_value:
            raise ValueError('not enough bars for ATR')
        return entry + sign * k * atr_value
    return float(method)


async def exit_plan(symbol: str, stop: str = 'range-mid', target_r: float = 2.0, side: Optional[str] = None,
                    entry: Optional[float] = None) -> dict:
    cfg = core.load_config()
    win, tf = core.session_window(cfg), core.tf_ms(cfg['session']['timeframe'])
    async with core.open_exchange(cfg, 'data') as ex:
        sym = (await ex.market(symbol)).symbol
        bs = await _session_bars(ex, sym, cfg, win)
        tk = await ex.ticker(sym)
    orb = (core.read('entry.json') or {}).get(sym) or orb_state(bs, win, tf, core.now_ms())
    side = side or orb.get('side')
    if side not in ('long', 'short'):
        raise ValueError('no side: the entry has not triggered; pass side=long|short')
    fresh = orb.get('side') == side and core.now_ms() - orb.get('trigger_ts', 0) <= 3 * tf  # trigger bar just closed
    fill = (tk.ask if side == 'long' else tk.bid) or tk.last  # a market entry fills at the far side of the book
    entry = entry or (orb['trigger_close'] if fresh else fill)  # a stale trigger is planned from the price now
    if stop in ('range-mid', 'range') and 'range_mid' not in orb:
        raise ValueError('the opening range is not formed yet; use stop=atr:K or a price')
    stop_px = stop_from(stop, side, entry, orb, atr(closed(bs, tf, core.now_ms())))
    risk = entry - stop_px if side == 'long' else stop_px - entry
    if risk <= 0:
        raise ValueError(f'stop {stop_px} is on the wrong side of entry {entry} for a {side}')
    tp = entry + target_r * risk if side == 'long' else entry - target_r * risk
    plan = {'symbol': sym, 'side': side, 'entry': entry, 'stop': stop_px, 'take_profit': tp,
            'stop_method': stop, 'target_r': target_r, 'risk_per_unit': risk,
            'risk_pct': round(risk / entry * 100, 3), 'flat_by': win['flat_by'], 'flat_by_utc': core.iso(win['flat_by']),
            'created': core.now_ms()}
    core.write('plan.json', plan)
    plan['text'] = (f"{sym} {side}: entry ~{entry}, stop {stop_px:.6g} ({plan['risk_pct']}% away, {stop}), "
                    f"target {tp:.6g} ({target_r}R), flat by {plan['flat_by_utc']} either way")
    return plan


# ─── 5. time machine ─────────────────────────────────────────────────────

def _outcome(side: str, path: list[Bar], stop: float, tp: float) -> str:
    for b in path:
        hit_stop = b[3] <= stop if side == 'long' else b[2] >= stop
        hit_tp = b[2] >= tp if side == 'long' else b[3] <= tp
        if hit_stop:  # both in one bar: assume the stop came first (conservative)
            return 'stop'
        if hit_tp:
            return 'target'
    return 'open'


def by_day(bs: list[Bar], start_ms: int) -> dict[int, list[Bar]]:
    """Group bars into sessions keyed by session start (same time of day as start_ms)."""
    days: dict[int, list[Bar]] = {}
    for b in bs:
        k = start_ms + ((b[0] - start_ms) // 86_400_000) * 86_400_000
        days.setdefault(k, []).append(b)
    return days


def monte_carlo(days: dict[int, list[Bar]], tf: int, entry_slot: int, end_slot: int, side: str, entry: float,
                stop: float, tp: float, n: int = 10_000, seed: int = 19) -> dict:
    """Rest of the session n times: each bar drawn from the same time-of-day bar of a random past session."""
    slots: dict[int, list[tuple]] = {}
    for d, bs in days.items():
        for b in bs:
            if b[1] > 0:
                slots.setdefault(int((b[0] - d) // tf), []).append((b[2] / b[1], b[3] / b[1], b[4] / b[1]))
    rng = random.Random(seed)
    counts = {'target': 0, 'stop': 0, 'open': 0}
    open_r = []
    risk = abs(entry - stop)
    for _ in range(n):
        px, res = entry, 'open'
        for s in range(entry_slot + 1, end_slot):
            if s not in slots:
                continue
            h, lo, c = rng.choice(slots[s])
            res = _outcome(side, [[0, px, px * h, px * lo, px * c, 0]], stop, tp)
            if res != 'open':
                break
            px *= c
        counts[res] += 1
        if res == 'open':
            open_r.append(((px - entry) if side == 'long' else (entry - px)) / risk)
    return {k: round(v / n * 100, 1) for k, v in counts.items()} | {
        'open_avg_r': round(statistics.fmean(open_r), 2) if open_r else None, 'runs': n, 'seed': seed,
        'sessions': len(days)}


def replay_rule(days: dict[int, list[Bar]], cfg: dict, tf: int, stop_method: str, target_r: float) -> dict:
    """The same rule on every past session: did it fire, and which came first."""
    res = {'fired': 0, 'target': 0, 'stop': 0, 'open': 0}
    for d, bs in sorted(days.items()):
        win = core.session_window(cfg, d)
        st = orb_state(bs, win, tf, win['flat_by'] + tf)
        if st['status'] != 'triggered':
            continue
        side, entry = st['side'], st['trigger_close']
        try:
            stop = stop_from(stop_method, side, entry, st, atr([b for b in bs if b[0] <= st['trigger_ts']]))
        except ValueError:
            continue
        risk = entry - stop if side == 'long' else stop - entry
        if risk <= 0:
            continue
        tp = entry + target_r * risk if side == 'long' else entry - target_r * risk
        path = [b for b in bs if st['trigger_ts'] < b[0] and b[0] + tf <= win['flat_by']]
        res['fired'] += 1
        res[_outcome(side, path, stop, tp)] += 1
    return res


async def timemachine(days: int = 30, runs: int = 10_000, seed: int = 19) -> dict:
    cfg = core.load_config()
    plan = core.read('plan.json')
    if not plan:
        raise ValueError('no crypto/plan.json: run the exit planner first')
    tf_s = cfg['session']['timeframe']
    tf, now = core.tf_ms(tf_s), core.now_ms()
    win = core.session_window(cfg)
    async with core.open_exchange(cfg, 'data') as ex:
        h = await ex.history.candles(plan['symbol'], tf_s, since=win['start'] - days * 86_400_000, until=win['start'])
    past = by_day(bars(h.rows), win['start'])
    stop_method = plan['stop_method'] if plan['stop_method'] in ('range-mid', 'range') or plan['stop_method'].startswith('atr') else 'atr:1.5'
    mc = monte_carlo(past, tf, int((min(now, win['flat_by']) - win['start']) // tf), int((win['flat_by'] - win['start']) // tf),
                     plan['side'], plan['entry'], plan['stop'], plan['take_profit'], runs, seed)
    hist = replay_rule(past, cfg, tf, stop_method, plan['target_r'])
    out = {'symbol': plan['symbol'], 'monte_carlo': mc, 'history': hist, 'history_stop_method': stop_method}
    st = core.read('state/timemachine.json') or {}
    st[plan['symbol']] = out
    core.write('state/timemachine.json', st)
    # expectancy in R before fees: target pays target_r, stop costs 1, neither counted as 0 (mc gives its avg)
    mc_r = (mc['target'] * plan['target_r'] - mc['stop'] + mc['open'] * (mc['open_avg_r'] or 0)) / 100
    hist_r = (hist['target'] * plan['target_r'] - hist['stop']) / hist['fired'] if hist['fired'] else None
    out['expectancy_r'] = {'monte_carlo': round(mc_r, 2), 'history': None if hist_r is None else round(hist_r, 2)}
    signs = {x > 0 for x in (mc_r, hist_r) if x is not None and abs(x) >= 0.2}  # under 0.2R is noise
    verdict = ('close to a coin flip' if not signs else 'mixed: the two disagree' if len(signs) > 1
               else 'leans your way' if True in signs else 'leans against you')
    out['text'] = (f"{plan['symbol']} {plan['side']}, {runs} replays of the rest of the session from {mc['sessions']} past sessions "
                   f"(seed {seed}): target first {mc['target']}%, stop first {mc['stop']}%, still open {mc['open']}%.\n"
                   f"Same rule on those sessions: fired {hist['fired']}x — target {hist['target']}, stop {hist['stop']}, "
                   f"neither {hist['open']}. Expectancy before fees: {out['expectancy_r']['monte_carlo']}R simulated, "
                   f"{out['expectancy_r']['history']}R historical. Verdict: {verdict}. A simulation, not a prediction.")
    return out
