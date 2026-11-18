---
name: crypto-risk
description: >-
  Risk Manager: PASS or BLOCK for every ticket against the user's limits, before anything can be sent: stop
  required, loss per trade, daily loss limit, trades per day, reward:risk, max leverage, max notional, stop
  beyond the liquidation estimate, margin vs account, entry window, blocked symbols, duplicates, real mode off.
  Getting out is never blocked by a day limit. Use when the user says "check my order", "is this safe", "risk
  check", "can I take this trade", and always before the broker.
---

# crypto-risk

A BLOCK is final for that ticket: do not argue it away or offer to send it anyway; change the plan or the size
and check again. Limits are the `risk` part of `settings`; change them only when the user asks.

## How to run it

If the `bytx-trader` MCP server is connected, call its tool `risk_check`. Otherwise run the CLI
(`pip install -U bytx-trader`, or prefix each command with `uvx --from bytx-trader@latest` to always run the newest):

```bash
bytx-trader risk
```

State lives in `~/bytx-trader` (the project's `crypto/` if it has one; `BYTX_TRADER_HOME` overrides). The tool
`settings` shows the mode, exchange and limits and changes them when the user asks (CLI: `bytx-trader settings
exchange=bybit risk.max_loss_per_trade=5`). The mode is `paper` unless the user changed it: say so.

Not financial advice. Most people who trade actively lose money.
