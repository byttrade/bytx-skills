---
name: crypto-derivs
description: >-
  Derivatives Monitor: funding rate (now and 7-day average), open interest and its change since the last run,
  perp/spot basis, with flags crowded_long, crowded_short, oi_up_price_down, oi_up_price_up. The crypto stand-in
  for a news monitor: positioning is what moves perps. Use when the user says "check funding", "is it crowded",
  "open interest", "basis", "who is paying", or before taking a trade.
argument-hint: "[symbols...]"
---

# crypto-derivs

Explain in plain words: positive funding means longs pay shorts every funding period; 0.05 % or more per period
is hot (flag crowded_long), and holding a long through it costs money. Open interest change needs a previous run to
compare with (first run says so). Basis is perp vs spot price. Symbols default to `crypto/watchlist.json`
(a JSON list), then to the scanner's hits.

## How to run it

If the `bytx-trader` MCP server is connected, call its tool `derivs`. Otherwise run the CLI
(`pip install -U bytx-trader`, or prefix each command with `uvx --from bytx-trader@latest` to always run the newest):

```bash
bytx-trader derivs BTC/USDT ETH/USDT
```

State lives in `~/bytx-trader` (the project's `crypto/` if it has one; `BYTX_TRADER_HOME` overrides). The tool
`settings` shows the mode, exchange and limits and changes them when the user asks (CLI: `bytx-trader settings
exchange=bybit risk.max_loss_per_trade=5`). The mode is `paper` unless the user changed it: say so.

Not financial advice. Most people who trade actively lose money.
