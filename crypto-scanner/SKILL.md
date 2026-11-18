---
name: crypto-scanner
description: >-
  Chart Scanner for crypto perpetuals. Scans the top perps of an exchange by 24h volume (or given symbols) for
  setups: a breakout of the session's opening range (crypto trades 24/7, so the session start is the user's,
  00:00 UTC by default), RSI oversold/overbought. Use when the user says "scan the market", "what's moving",
  "find setups", "any breakouts", "what should I look at today".
argument-hint: "[--top N] [symbols...]"
---

# crypto-scanner

Finds candidates, nothing more. A hit means a pattern fired, not that the trade is good: the next steps
(derivs, entry, exit, timemachine) decide. Freshest triggers are listed first; mention the trigger time, a breakout from
hours ago is stale. Writes `crypto/scan.json`, which `crypto-derivs` uses when no symbols are given.

## How to run it

If the `bytx-trader` MCP server is connected, call its tool `scan`. Otherwise run the CLI
(`pip install bytx-trader` once):

```bash
bytx-trader scan --top 30
bytx-trader scan BTC/USDT ETH/USDT
```

State lives in `crypto/` in the project (override with `BYTX_TRADER_HOME`); settings in `crypto/config.json`
(see `config.example.json` in the pack). The mode is `paper` unless the user changed it: say so.

Not financial advice. Most people who trade actively lose money.
