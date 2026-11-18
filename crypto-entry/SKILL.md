---
name: crypto-entry
description: >-
  Entry Timer: checks one symbol's opening-range breakout bar by bar and says waiting, triggered (the first
  closed bar beyond the range, with side and close), expired (past the entry deadline) or forming (range not
  done). Use when the user says "is it time", "did it break out", "should I enter now", "watch BTC for an
  entry".
argument-hint: "<symbol>"
---

# crypto-entry

Only closed bars count: a bar still forming never triggers. To watch, call it again after the next bar closes
(every 5 minutes on the default timeframe) rather than looping. Writes `crypto/entry.json`, used by `crypto-exit`.

## How to run it

If the `bytx-trader` MCP server is connected, call its tool `entry_check`. Otherwise run the CLI
(`pip install bytx-trader` once):

```bash
bytx-trader entry BTC/USDT
```

State lives in `crypto/` in the project (override with `BYTX_TRADER_HOME`); settings in `crypto/config.json`
(see `config.example.json` in the pack). The mode is `paper` unless the user changed it: say so.

Not financial advice. Most people who trade actively lose money.
