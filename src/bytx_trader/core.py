"""Shared plumbing: the crypto/ folder, config, JSON state, exchanges and the session clock."""
from __future__ import annotations

import copy
import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

DEFAULT_CONFIG: dict = {
    'mode': 'paper',               # paper | demo | real (real is not executed by this package, see broker)
    'allow_real': False,
    'exchange': 'binance',
    'account_usdt': 1000,          # paper balance and the cash check
    'default_leverage': 5,
    'session': {                   # all times UTC; crypto trades 24/7, so the "session" is yours to pick
        'start': '00:00',
        'range_minutes': 60,
        'timeframe': '5m',
        'entry_until': None,       # "HH:MM" to stop new entries after it
        'flat_by': '23:55',
    },
    'risk': {
        'max_loss_per_trade': 10,
        'daily_loss_limit': 30,
        'max_trades_per_day': 3,
        'min_rr': 1.5,
        'max_leverage': 5,
        'max_notional': 5000,
        'blocked_symbols': [],
    },
    'scanner': {'top': 30},
}


def home() -> Path:
    """The crypto/ folder every step reads from and writes to."""
    return Path(os.environ.get('BYTX_TRADER_HOME') or Path.cwd() / 'crypto')


def _merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in over.items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def load_config() -> dict:
    p = home() / 'config.json'
    return _merge(DEFAULT_CONFIG, json.loads(p.read_text())) if p.exists() else copy.deepcopy(DEFAULT_CONFIG)


def read(name: str, default: Any = None) -> Any:
    p = home() / name
    return json.loads(p.read_text()) if p.exists() else default


def write(name: str, data: Any) -> Path:
    p = home() / name
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + '.tmp')
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str))
    tmp.replace(p)  # atomic: a crash never leaves half a state file
    return p


# ─── time ────────────────────────────────────────────────────────────────

def now_ms() -> int:
    return int(os.environ.get('BYTX_TRADER_NOW_MS') or time.time() * 1000)  # env pins the clock for tests/replays


def tf_ms(timeframe: str) -> int:
    units = {'m': 60_000, 'h': 3_600_000, 'd': 86_400_000}
    return int(timeframe[:-1]) * units[timeframe[-1]]


def _hhmm(s: str) -> timedelta:
    h, m = s.split(':')
    return timedelta(hours=int(h), minutes=int(m))


def session_window(cfg: dict, at_ms: Optional[int] = None) -> dict:
    """Current session in ms: start, range end, entry deadline, flat-by. A session lasts at most 24h."""
    s = cfg['session']
    at = datetime.fromtimestamp((at_ms or now_ms()) / 1000, timezone.utc)
    start = at.replace(hour=0, minute=0, second=0, microsecond=0) + _hhmm(s['start'])
    if start > at:
        start -= timedelta(days=1)

    def after_start(hhmm: str) -> int:
        t = start.replace(hour=0, minute=0) + _hhmm(hhmm)
        while t <= start:
            t += timedelta(days=1)
        return int(t.timestamp() * 1000)

    start_ms = int(start.timestamp() * 1000)
    flat = after_start(s['flat_by'])
    return {
        'start': start_ms,
        'range_end': start_ms + s['range_minutes'] * 60_000,
        'entry_until': after_start(s['entry_until']) if s.get('entry_until') else flat,
        'flat_by': flat,
    }


def iso(ms: Optional[int]) -> Optional[str]:
    return None if ms is None else datetime.fromtimestamp(ms / 1000, timezone.utc).strftime('%Y-%m-%d %H:%M UTC')


# ─── exchanges ───────────────────────────────────────────────────────────

def _create(name: str, **kw: Any) -> Any:
    from bytx import create_exchange
    return create_exchange(name, **kw)


# Tests replace this with a bytx.testing emulator, so callers use core.open_exchange(...), not a from-import.
def open_exchange(cfg: dict, kind: str) -> Any:
    """kind: 'data' — public data, no keys; 'trade' — the account of cfg['mode']; 'spot' — public spot data."""
    name = cfg['exchange']
    if kind == 'data':
        return _create(name)
    if kind == 'spot':
        return _create(name, market='spot')
    mode = cfg['mode']
    if mode == 'paper':
        from bytx.paper import PaperConfig
        return _create(name, paper=True, paper_config=PaperConfig(balance={'USDT': float(cfg['account_usdt'])}))
    if mode == 'demo':
        keys = {k: os.environ.get(f'BYTX_{k.upper()}') for k in ('api_key', 'secret', 'password')}
        if not keys['api_key'] or not keys['secret']:
            raise RuntimeError('demo mode needs BYTX_API_KEY and BYTX_SECRET (and BYTX_PASSWORD for OKX/KuCoin/Bitget) '
                               'in the environment. Never paste keys into the chat.')
        return _create(name, demo=True, **{k: v for k, v in keys.items() if v})
    raise RuntimeError(f"mode {mode!r} is not executed by bytx-trader; real orders go through the hosted BYTtrade MCP")
