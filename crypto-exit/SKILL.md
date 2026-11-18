---
name: crypto-exit
description: >-
  Exit Planner: sets the stop, the target (in R multiples of the risk) and the flat-by time before the entry, so
  the user knows when to get out. Stops: range-mid, range (far edge), atr:K, or a price. Use when the user says
  "where's my stop", "set a target", "plan the exit", "how much am I risking", or right after an entry triggers.
argument-hint: "<symbol> [--stop range-mid|range|atr:K|price] [--target-r 2] [--side long|short]"
---

# crypto-exit

Side and entry come from the triggered entry; pass them to plan anything else. Writes `crypto/plan.json`, which the
time machine and the sizer read. A stop is a plan, not a guarantee: in a fast market it can fill past its price.

## How to run it

If the `bytx-trader` MCP server is connected, call its tool `exit_plan`. Otherwise run the CLI
(`pip install bytx-trader` once):

```bash
bytx-trader exit BTC/USDT --stop range-mid --target-r 2
bytx-trader exit ETH/USDT --side short --stop atr:2
```

State lives in `crypto/` in the project (override with `BYTX_TRADER_HOME`); settings in `crypto/config.json`
(see `config.example.json` in the pack). The mode is `paper` unless the user changed it: say so.

Not financial advice. Most people who trade actively lose money.
