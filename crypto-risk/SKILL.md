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
and check again. Limits live in `crypto/config.json` under `risk`; only the user changes them.

## How to run it

If the `bytx-trader` MCP server is connected, call its tool `risk_check`. Otherwise run the CLI
(`pip install bytx-trader` once):

```bash
bytx-trader risk
```

State lives in `crypto/` in the project (override with `BYTX_TRADER_HOME`); settings in `crypto/config.json`
(see `config.example.json` in the pack). The mode is `paper` unless the user changed it: say so.

Not financial advice. Most people who trade actively lose money.
