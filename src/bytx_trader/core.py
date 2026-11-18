"""Shared plumbing: the crypto/ folder, config, JSON state, exchanges and the session clock."""
from __future__ import annotations

import copy
import json
import os
import re
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
    'margin_mode': 'isolated',     # a loss can't exceed the position's margin; the liquidation check is per position
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
    'scanner': {'top': 30, 'fresh_bars': 6},  # a breakout older than 6 bars is not a setup any more
}


def home() -> Path:
    """The folder every step reads from and writes to: BYTX_TRADER_HOME, else ./crypto if it exists (a project),
    else ~/bytx-trader (MCP clients like Claude Desktop start the server in a folder it can't write to)."""
    env = os.environ.get('BYTX_TRADER_HOME')
    if env:
        return Path(env).expanduser()
    local = Path.cwd() / 'crypto'
    return local if local.is_dir() else Path.home() / 'bytx-trader'


def norm_symbol(cfg: dict, s: str) -> str:
    """'BTC', 'btc', 'BTCUSDT', 'BTC-USDT', 'BTC/USDT' -> 'BTC/USDT' (USDC on Hyperliquid); bytx finds the perp."""
    s = s.strip().upper().replace('-', '/').replace('_', '/')
    if '/' in s:
        return s
    for quote in ('USDT', 'USDC'):
        if s.endswith(quote) and len(s) > len(quote):
            return f'{s[:-len(quote)]}/{quote}'
    return f"{s}/{'USDC' if cfg['exchange'] == 'hyperliquid' else 'USDT'}"


MODES = ('paper', 'demo')
EXCHANGES = ('binance', 'bybit', 'okx', 'bingx', 'bitget', 'kucoin', 'gate', 'mexc', 'htx', 'hyperliquid', 'blofin')


def _check(path: str, old: Any, new: Any) -> Any:
    if path == 'mode':
        if new not in MODES:
            raise ValueError(f"mode: {' or '.join(MODES)} (real orders go through the hosted BYTtrade MCP, not here)")
    elif path == 'exchange':
        if new not in EXCHANGES:
            raise ValueError(f"exchange: one of {', '.join(EXCHANGES)}")
    elif path == 'margin_mode':
        if new not in ('isolated', 'cross'):
            raise ValueError('margin_mode: isolated or cross')
    elif path == 'allow_real':
        raise ValueError('allow_real is not changed from a chat')
    elif isinstance(old, bool) or isinstance(old, (int, float)):
        if isinstance(new, bool) or not isinstance(new, (int, float)) or new < 0:
            raise ValueError(f'{path}: a number >= 0')
    elif isinstance(old, list):
        if not isinstance(new, list):
            raise ValueError(f'{path}: a list')
    elif path == 'session.timeframe':
        if new not in ('1m', '3m', '5m', '15m', '30m', '1h'):
            raise ValueError(f'{path}: 1m, 3m, 5m, 15m, 30m or 1h')
    elif old is None or isinstance(old, str):  # session times
        if new is not None and not (isinstance(new, str) and re.fullmatch(r'([01]\d|2[0-3]):[0-5]\d', new)):
            raise ValueError(f'{path}: a UTC time "HH:MM" like "13:30"' + (' or null' if path == 'session.entry_until' else ''))
    return new


def update_config(changes: dict) -> dict:
    """Merge changes into config.json, only known keys with valid values. Returns the new config."""
    cur = load_config()
    saved = read('config.json', {}) or {}

    def walk(base: dict, default: dict, ch: dict, out: dict, prefix: str) -> None:
        for k, v in ch.items():
            path = f'{prefix}{k}'
            if k not in default:
                raise ValueError(f"unknown setting {path!r}; known: {', '.join(prefix + x for x in default)}")
            if isinstance(default[k], dict):
                if not isinstance(v, dict):
                    raise ValueError(f'{path}: an object like {{"{next(iter(default[k]))}": …}}')
                walk(base[k], default[k], v, out.setdefault(k, {}), path + '.')
            else:
                out[k] = _check(path, base[k], v)
    walk(cur, DEFAULT_CONFIG, changes, saved, '')
    write('config.json', saved)
    return load_config()


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

KEY_NAMES = ('api_key', 'secret', 'password', 'wallet_address', 'private_key')  # read from BYTX_<NAME>


def _create(name: str, **kw: Any) -> Any:
    from bytx import create_exchange
    return create_exchange(name, **kw)


# Tests replace this with a bytx.testing emulator, so callers use core.open_exchange(...), not a from-import.
def open_exchange(cfg: dict, kind: str) -> Any:
    """kind: 'data' — public data, no keys; 'trade' — the account of cfg['mode']; 'spot' — public spot data."""
    name = cfg['exchange']
    demo = cfg['mode'] == 'demo'  # testnets (Hyperliquid…) have their own prices: plan on the market you trade
    if kind == 'data':
        return _create(name, demo=demo)
    if kind == 'spot':
        return _create(name, market='spot', demo=demo)
    mode = cfg['mode']
    if mode == 'paper':
        from bytx.paper import PaperConfig
        return _create(name, paper=True, margin_mode=cfg['margin_mode'],
                       paper_config=PaperConfig(balance={'USDT': float(cfg['account_usdt'])}))
    if mode == 'demo':
        keys = {k: os.environ.get(f'BYTX_{k.upper()}') for k in KEY_NAMES}
        if not (keys['api_key'] and keys['secret']) and not (keys['wallet_address'] and keys['private_key']):
            raise RuntimeError('demo mode needs BYTX_API_KEY and BYTX_SECRET (+ BYTX_PASSWORD for OKX/KuCoin/Bitget), '
                               'or BYTX_WALLET_ADDRESS and BYTX_PRIVATE_KEY for Hyperliquid, in the environment. '
                               'Never paste keys into the chat.')
        return _create(name, demo=True, margin_mode=cfg['margin_mode'], **{k: v for k, v in keys.items() if v})
    raise RuntimeError(f"mode {mode!r} is not executed by bytx-trader; real orders go through the hosted BYTtrade MCP")
