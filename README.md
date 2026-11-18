# bytx-skills — crypto trading skills for AI assistants

**English** | [Русский](docs/README.ru.md)

Eight steps for one crypto trade, from "what's moving" to the order: scan the perps, read funding and open interest,
time the entry, plan the exit, replay the rest of the session 10,000 times, size the position from the money you
are ready to lose, check it against your limits, and send it — paper by default, never without your yes.

Works with **any AI assistant that supports MCP — for example, Claude**. The logic is a Python package with an MCP
server and a CLI on the [bytx](https://pypi.org/project/bytx/) exchange library; for Claude there are also skills
(`/crypto-scanner` …) on top.

Paper trading runs on the exchange's live order book with virtual money: no keys, no signup.

## Install

You need [uv](https://docs.astral.sh/uv/) (`brew install uv` or `curl -LsSf https://astral.sh/uv/install.sh | sh`).

**Any MCP client** (Claude Desktop, Claude Code, Cursor, …): add the server to the client's MCP config —
templates in [`mcp-configs/`](mcp-configs/):

```json
{ "mcpServers": { "bytx-trader": {
    "command": "uvx",
    "args": ["--refresh-package", "bytx", "--refresh-package", "bytx-trader",
             "--from", "bytx-trader@latest", "bytx-trader-mcp"],
    "env": { "BYTX_TRADER_HOME": "/absolute/path/to/your/project/crypto" } } } }
```

Then ask "scan the crypto market" or start from the server's `trade_pipeline` prompt. Set the `confirm` tool to ask
you every time. Without uv: `pip install bytx-trader` and `"command": "bytx-trader-mcp"`.

**Claude only, on top of that:**

```
/plugin marketplace add <owner>/bytx-skills
/plugin install bytx-trader@bytx-skills
```

or copy the folders: `cp -r bytx-skills/crypto-* ~/.claude/skills/` (or your project's `.claude/skills/`).

| | any MCP client | Claude only |
|---|---|---|
| all eight steps, paper and demo | MCP tools | |
| risk gate and one-time confirm token | in the server | |
| the trade as a script | `trade_pipeline` prompt | skills |
| `/crypto-*` commands, triggers by phrase, plugin install | | ✓ |

## The steps

| step | MCP tool / CLI | what it does |
|---|---|---|
| `crypto-scanner` | `scan` | Top perps by 24h volume: breakout of the session's opening range, RSI extremes. |
| `crypto-derivs` | `derivs` | Funding now and 7-day average, open interest change, perp/spot basis, crowded-side flags. |
| `crypto-entry` | `entry_check` | Waiting / triggered / expired, on closed bars only. |
| `crypto-exit` | `exit_plan` | Stop (range-mid, range, ATR, price), target in R, flat-by time. |
| `crypto-timemachine` | `timemachine` | 10,000 replays of the rest of the session from the coin's own bars + the same rule on every past session. |
| `crypto-size` | `size` | Size from risk with fees, margin, liquidation estimate, funding cost. Places nothing. |
| `crypto-risk` | `risk_check` | PASS / BLOCK against your limits. |
| `crypto-broker` | `execute`, `confirm`, `flatten`, `positions`, `reconcile` | The only step that sends anything. |

Crypto trades 24/7, so the "session" is yours: `crypto/config.json` → `session.start` (00:00 UTC by default),
`range_minutes`, `entry_until`, `flat_by`. Everything lands in `crypto/` and each step reads what the last one wrote.

## An example: BTC on Binance, November 18 2026, real candles

1. **scan** at 01:05 UTC: BTC broke its first-hour range (64,673.5–64,976.3) to the downside; the 01:00 bar closed at
   64,588.5, 0.131 % below.
2. **entry_check**: triggered, short.
3. **exit_plan** `--stop range-mid --target-r 2`: stop 64,824.9 (0.366 % away), target 64,115.7, flat by 23:55 UTC.
4. **timemachine**: 10,000 replays from the last 30 sessions (seed 19): target first 37.6 %, stop first 62.4 %. The same
   rule on those 30 sessions: fired 30 times, 9 targets, 21 stops. Expectancy before fees +0.13R simulated, −0.10R
   historical. Verdict: close to a coin flip — that is the point of looking before you risk a dollar.
5. **size** with 10 USDT at risk, 5x: ≈0.033 BTC, ≈2,131 USDT notional, margin ≈426, loss at the stop ≈9.94 USDT with fees.
6. **risk_check**: PASS.
7. **execute** shows the ticket and returns a one-time token; you answer yes; **confirm** sends it to the paper account.

`tests/` reproduce these numbers offline from the candles in `tests/fixtures/`.

## Modes and the gates

| mode | how | keys |
|---|---|---|
| **paper** (default) | live order book, virtual balance `account_usdt`, real taker fees | none |
| **demo** | `"mode": "demo"`: the exchange's demo/testnet | `BYTX_API_KEY`, `BYTX_SECRET` (`BYTX_PASSWORD` for OKX, KuCoin, Bitget) in the server's environment |
| **real** | not sent by this package: real orders go through the hosted [BYTtrade](https://byttrade.com) MCP | kept by BYTtrade |

Every order, in every mode, passes three gates, enforced in the code:

1. the risk check passed **this exact ticket** today (fingerprint);
2. `execute` sends nothing — it returns the ticket and a one-time token (5 min); `confirm` takes the token and **your
   reply word for word**, and anything but a plain yes cancels; the risk check runs again;
3. your client's own approval prompt for `confirm`.

The client order id comes from the ticket, so if the exchange times out the order is found by that id with
`reconcile` — it is never sent twice. Getting out (`flatten`) is never blocked by daily limits.

## Updates

Started with `uvx` as above, the server picks up the newest `bytx-trader` and the newest compatible `bytx` every time
your assistant starts it: fixes and new exchange features arrive with no action on your side. `bytx-trader` accepts
`bytx` within one series (`>=0.2.1,<0.3`), so a breaking `bytx` release never arrives on its own: it comes with a
`bytx-trader` release that was tested against it. Installed with `pip`, the server checks PyPI once a day and tells you
in its first answer when an update is out (`BYTX_TRADER_NO_UPDATE_CHECK=1` turns that off). Claude skills installed as
a plugin update with the marketplace (`/plugin`).

## Development

```bash
pip install -e ".[test]" && pytest
```

Tests are offline: real candles in `tests/fixtures/`, orders on `bytx.testing` paper accounts.

## Fine print

**Not financial advice.** Most people who trade actively lose money; leverage makes it faster. A stop is a plan, not
a guarantee: gaps, slippage and auto-deleveraging can fill it far away, and liquidation can come first if leverage is
high. Funding is charged every period while a position is open. The time machine is a simulation from the last few
weeks, not a prediction. Paper trading simplifies margin and liquidation and does not watch your stop between runs.
Keys live in environment variables, never in the chat.

MIT. Built on [bytx](https://pypi.org/project/bytx/) (BYTtrade Source-Available License, installed from PyPI, not
copied here). No-code bots, backtests and trade confirmations in Telegram — at [byttrade.com](https://byttrade.com).

<sub>**All risk is yours.** bytx-skills is a tool, provided as is, without any warranty. You alone decide whether and
how to use it, and you alone bear every loss, missed profit or other consequence, including those caused by errors in
the software, the data, an exchange or an AI assistant. Test it yourself — on paper, then on an exchange demo account —
and only once you are satisfied that it works for you, use it with real money, in amounts you can afford to lose.
The authors and BYTtrade are not liable for any trading results.</sub>
