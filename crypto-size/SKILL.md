---
name: crypto-size
description: >-
  Position Sizer: sizes the position from the money the user is ready to lose at the stop (fees included), with
  leverage, margin, a liquidation estimate and the funding cost per period. Places nothing; writes one ticket to
  crypto/orders.json. Use when the user says "how big", "size it", "I want to risk 20 USDT", "what leverage", or
  after the exit plan.
argument-hint: "[--risk-usdt N] [--leverage N]"
---

# crypto-size

Defaults: risk = `risk.max_loss_per_trade`, leverage = `default_leverage` from the config. Leverage does not change the
loss at the stop, only the margin and how close liquidation is: say that. Perpetuals only in v0.1.

## How to run it

If the `bytx-trader` MCP server is connected, call its tool `size`. Otherwise run the CLI
(`pip install -U bytx-trader`, or prefix each command with `uvx --from bytx-trader@latest` to always run the newest):

```bash
bytx-trader size --risk-usdt 10 --leverage 5
```

State lives in `crypto/` in the project (override with `BYTX_TRADER_HOME`); settings in `crypto/config.json`
(see `config.example.json` in the pack). The mode is `paper` unless the user changed it: say so.

Not financial advice. Most people who trade actively lose money.
