"""bytx-trader <step> — the same functions the MCP server exposes, for scripts, cron and manual checks."""
from __future__ import annotations

import argparse
import asyncio
import inspect
import json
import sys

from bytx.errors import BytxError

from . import market, trade


def _changes(pairs: list[str]) -> dict:
    """['risk.max_loss_per_trade=5', 'exchange=bybit'] -> {'risk': {'max_loss_per_trade': 5}, 'exchange': 'bybit'}"""
    out: dict = {}
    for pair in pairs:
        key, sep, raw = pair.partition('=')
        if not sep:
            raise ValueError(f'{pair!r}: use key=value, e.g. exchange=bybit')
        try:
            value = json.loads(raw)
        except ValueError:
            value = raw
        *path, last = key.split('.')
        node = out
        for k in path:
            node = node.setdefault(k, {})
        node[last] = value
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog='bytx-trader', description='Crypto trading steps on bytx. Paper by default.')
    p.add_argument('--json', action='store_true', help='print the full JSON result')
    sub = p.add_subparsers(dest='cmd', required=True)
    s = sub.add_parser('scan', help='setups across the top perps')
    s.add_argument('--top', type=int)
    s.add_argument('symbols', nargs='*')
    sub.add_parser('derivs', help='funding, open interest, basis').add_argument('symbols', nargs='*')
    sub.add_parser('entry', help='waiting / triggered / expired').add_argument('symbol')
    s = sub.add_parser('exit', help='stop and target before you enter -> plan.json')
    s.add_argument('symbol')
    s.add_argument('--stop', default='range-mid', help='range-mid | range | atr:K | a price')
    s.add_argument('--target-r', type=float, default=2.0)
    s.add_argument('--side', choices=['long', 'short'])
    s.add_argument('--entry', type=float)
    s = sub.add_parser('timemachine', help='replay the rest of the session many times')
    s.add_argument('--days', type=int, default=30)
    s.add_argument('--runs', type=int, default=10_000)
    s.add_argument('--seed', type=int, default=19)
    s = sub.add_parser('size', help='size from risk -> orders.json')
    s.add_argument('--risk-usdt', type=float)
    s.add_argument('--leverage', type=float)
    sub.add_parser('risk', help='PASS / BLOCK per ticket')
    sub.add_parser('show', help='tickets, READY or not')
    sub.add_parser('execute', help='get a one-time confirm token (sends nothing)').add_argument('index', type=int, nargs='?', default=1)
    s = sub.add_parser('confirm', help="send the ticket after the user's yes")
    s.add_argument('token')
    s.add_argument('reply', help="the user's reply, word for word")
    sub.add_parser('flatten', help='close tickets for open positions').add_argument('symbol', nargs='?')
    sub.add_parser('positions', help='recorded positions, marked to market')
    sub.add_parser('reconcile', help='find an order of unknown outcome by client_id').add_argument('client_id')
    sub.add_parser('settings', help='show settings, or change: settings exchange=bybit risk.max_loss_per_trade=5'
                   ).add_argument('changes', nargs='*')
    a = p.parse_args(argv)

    calls = {
        'scan': lambda: market.scan(a.top, a.symbols or None),
        'derivs': lambda: market.derivs(a.symbols or None),
        'entry': lambda: market.entry_check(a.symbol),
        'exit': lambda: market.exit_plan(a.symbol, a.stop, a.target_r, a.side, a.entry),
        'timemachine': lambda: market.timemachine(a.days, a.runs, a.seed),
        'size': lambda: trade.size(a.risk_usdt, a.leverage),
        'risk': trade.risk_check,
        'show': trade.tickets_show,
        'execute': lambda: trade.execute(a.index),
        'confirm': lambda: trade.confirm(a.token, a.reply),
        'flatten': lambda: trade.flatten(a.symbol),
        'positions': trade.positions,
        'reconcile': lambda: trade.reconcile(a.client_id),
        'settings': lambda: trade.settings(_changes(a.changes)),
    }
    try:
        res = calls[a.cmd]()
        if inspect.isawaitable(res):
            res = asyncio.run(res)
    except (ValueError, PermissionError, RuntimeError, BytxError) as e:
        print(f'{type(e).__name__}: {e}', file=sys.stderr)
        return 2
    print(json.dumps(res, indent=2, ensure_ascii=False, default=str) if a.json else res['text'])
    return 0


if __name__ == '__main__':
    sys.exit(main())
