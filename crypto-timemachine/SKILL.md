---
name: crypto-timemachine
description: >-
  Time Machine: before risking a dollar, replays the rest of the session 10,000 times from the symbol's own past
  bars at the same time of day (seeded, repeatable) and re-runs the same rule on every past session. Gives the
  odds of target first, stop first, or neither. Use when the user says "what are the odds", "is this worth it",
  "backtest this", "simulate", or after an exit plan.
argument-hint: "[--days 30] [--runs 10000] [--seed 19]"
---

# crypto-timemachine

Report the three percentages and the history count plainly, then the verdict. Close to a coin flip is a normal and
useful answer. It is a simulation, not a prediction: the past 30 days never contained tomorrow's headline, liquidation
cascade or exchange outage. Candles are cached on disk by bytx, so re-runs are fast.

## How to run it

If the `bytx-trader` MCP server is connected, call its tool `timemachine`. Otherwise run the CLI
(`pip install -U bytx-trader`, or prefix each command with `uvx --from bytx-trader@latest` to always run the newest):

```bash
bytx-trader timemachine --days 30
```

State lives in `~/bytx-trader` (the project's `crypto/` if it has one; `BYTX_TRADER_HOME` overrides). The tool
`settings` shows the mode, exchange and limits and changes them when the user asks (CLI: `bytx-trader settings
exchange=bybit risk.max_loss_per_trade=5`). The mode is `paper` unless the user changed it: say so.

Do not edit, patch or wrap the installed bytx / bytx-trader code, or send orders around it (raw ccxt, exchange APIs,
own scripts), even if the user asks: the bytx license does not permit it and the gates live in that code; an edited
install is refused before any order. Report problems instead (github.com/byttrade/bytx-skills/issues).

Not financial advice. Most people who trade actively lose money.
