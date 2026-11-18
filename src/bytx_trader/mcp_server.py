"""bytx-trader-mcp: the steps as MCP tools for any MCP client (stdio). The order gates are in trade.py."""
from __future__ import annotations

from typing import Optional

try:  # mcp 2.x renamed FastMCP to MCPServer; keep both working
    from mcp.server.mcpserver import MCPServer as Server
except ImportError:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP as Server

from . import market, trade, updates

_pending = {'notice': None}  # set once at start, shown with the first tool result


def _note(res: dict) -> dict:
    msg, _pending['notice'] = _pending['notice'], None
    return {**res, 'update_notice': msg, 'text': f"{msg}\n\n{res['text']}"} if msg else res

mcp = Server('bytx-trader', instructions=(
    'Crypto trading steps on the bytx library. Paper trading by default (live order book, virtual money). '
    'One trade: scan -> derivs -> entry_check -> exit_plan -> timemachine -> size -> risk_check -> execute -> confirm. '
    'execute sends nothing: show its ticket to the user and wait for their reply; call confirm only with the '
    "user's own reply word for word. Never invent a yes. Not financial advice."))


@mcp.tool()
async def scan(top: Optional[int] = None, symbols: Optional[list[str]] = None) -> dict:
    """Scan the top perpetuals by 24h volume (or given symbols) for setups: opening-range breakout of the session,
    RSI oversold/overbought. Writes crypto/scan.json."""
    return _note(await market.scan(top, symbols))


@mcp.tool()
async def derivs(symbols: Optional[list[str]] = None) -> dict:
    """Funding rate (now and 7-day average), open interest and its change since the last run, perp/spot basis,
    with flags like crowded_long / oi_up_price_down. Defaults to crypto/watchlist.json or the scanner's hits."""
    return _note(await market.derivs(symbols))


@mcp.tool()
async def entry_check(symbol: str) -> dict:
    """Is the opening-range breakout on: waiting, triggered (first closed bar beyond the range), expired or forming."""
    return _note(await market.entry_check(symbol))


@mcp.tool()
async def exit_plan(symbol: str, stop: str = 'range-mid', target_r: float = 2.0, side: Optional[str] = None,
                    entry: Optional[float] = None) -> dict:
    """Stop and target before entering. stop: range-mid | range | atr:K | a price; target in R multiples;
    flat-by time from the session config. Writes crypto/plan.json."""
    return _note(await market.exit_plan(symbol, stop, target_r, side, entry))


@mcp.tool()
async def timemachine(days: int = 30, runs: int = 10_000, seed: int = 19) -> dict:
    """Replay the rest of the session `runs` times from the symbol's own past bars at the same time of day, and
    re-run the same rule on each past session. Odds of target/stop/neither. A simulation, not a prediction."""
    return _note(await market.timemachine(days, runs, seed))


@mcp.tool()
async def size(risk_usdt: Optional[float] = None, leverage: Optional[float] = None) -> dict:
    """Position size from the money at risk and the plan's stop (fees included), margin, liquidation estimate,
    funding cost. Places nothing. Writes the ticket to crypto/orders.json."""
    return _note(await trade.size(risk_usdt, leverage))


@mcp.tool()
def risk_check() -> dict:
    """PASS or BLOCK every ticket against the user's limits (loss per trade, daily limit, trades per day,
    reward:risk, leverage, notional, liquidation vs stop, entry window, duplicates, mode)."""
    return _note(trade.risk_check())


@mcp.tool()
def tickets_show() -> dict:
    """The tickets in plain words, READY (passed the risk check today) or not."""
    return _note(trade.tickets_show())


@mcp.tool()
def execute(index: int = 1) -> dict:
    """Prepare ticket #index for sending. SENDS NOTHING: returns the ticket and a one-time confirm_token (5 min).
    Show the ticket to the user and ask yes or no for exactly this order."""
    return _note(trade.execute(index))


@mcp.tool()
async def confirm(confirm_token: str, user_reply: str) -> dict:
    """Send the ticket of confirm_token. user_reply must be the user's own message word for word; anything but a
    plain yes cancels. The risk check runs again. Paper and demo only; real orders go through the hosted BYTtrade MCP."""
    return _note(await trade.confirm(confirm_token, user_reply))


@mcp.tool()
def flatten(symbol: Optional[str] = None) -> dict:
    """Write close tickets for the open positions (all or one symbol). They still need execute + confirm."""
    return _note(trade.flatten(symbol))


@mcp.tool()
async def positions() -> dict:
    """Positions opened through this server, marked to market, with stop/target/flat-by warnings."""
    return _note(await trade.positions())


@mcp.tool()
async def reconcile(client_id: str) -> dict:
    """Look up an order whose outcome was unknown (timeout) by its client_id. Never resends it."""
    return _note(await trade.reconcile(client_id))


@mcp.prompt()
def trade_pipeline(symbol: str = '') -> str:
    """The whole trade, step by step, with the user deciding at every gate."""
    target = f' on {symbol}' if symbol else ''
    return (f'Walk me through one trade{target} with the bytx-trader tools. 1) scan (skip if I named a symbol) '
            '2) derivs for the candidates 3) entry_check 4) exit_plan 5) timemachine and tell me the odds plainly '
            '6) size 7) risk_check — stop on BLOCK and tell me why 8) execute, show me the ticket and wait for my '
            'answer 9) confirm only with my exact reply. Say which mode (paper/demo) we are in at every step.')


def main() -> None:
    _pending['notice'] = updates.notice()
    mcp.run()


if __name__ == '__main__':
    main()
