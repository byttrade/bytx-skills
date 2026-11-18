---
name: crypto-broker
description: >-
  Order Executor: the only step that can lead to an order, paper by default, demo with exchange keys from the
  environment, never without the user's yes. Shows the ticket in plain words, gets a one-time token, sends only
  after the user's own yes, records fills, closes positions (flatten), reconciles an order of unknown outcome by
  client_id without resending. Use when the user says "place it", "buy", "execute", "close my position",
  "flatten", "get me out", "what's open", or after a PASS.
argument-hint: "[ticket number]"
---

# crypto-broker

Three gates, every order, enforced by the code, not by this text:

1. the ticket passed `crypto-risk` today (matched by fingerprint);
2. `execute` returns the ticket and a token; show the ticket and ask the user yes or no for exactly this order;
   pass their reply **word for word** to `confirm`. Never write a yes for them, never reuse a yes from an earlier order;
3. the client's own approval card: tell the user to keep the `confirm` tool on "Needs approval", never "Always allow".

Demo needs `BYTX_API_KEY`, `BYTX_SECRET` (and `BYTX_PASSWORD` for OKX/KuCoin/Bitget; for Hyperliquid
`BYTX_WALLET_ADDRESS`, `BYTX_PRIVATE_KEY`) in the environment: never ask for keys in the chat. In demo, `positions`
reads the exchange: a position closed there by its stop or take-profit drops out of the record. Real orders are not sent by this pack: they go through the hosted BYTtrade MCP. If an outcome is
`unknown`, it is not resent: run `reconcile` with the client_id first. Paper positions are tracked in
`crypto/state/broker.json`; stop and target are not watched between runs, so check `positions` and flatten.

## How to run it

If the `bytx-trader` MCP server is connected, call its tools `tickets_show`, `execute`, `confirm`, `positions`, `flatten`, `reconcile`. Otherwise run the CLI
(`pip install -U bytx-trader`, or prefix each command with `uvx --from bytx-trader@latest` to always run the newest):

```bash
bytx-trader show                 # tickets, READY or not
bytx-trader execute 1            # ticket + one-time token, sends nothing
bytx-trader confirm <token> "<the user's reply, word for word>"
bytx-trader positions
bytx-trader flatten [symbol]     # close tickets, then execute + confirm again
bytx-trader reconcile <client_id>
```

State lives in `~/bytx-trader` (the project's `crypto/` if it has one; `BYTX_TRADER_HOME` overrides). The tool
`settings` shows the mode, exchange and limits and changes them when the user asks (CLI: `bytx-trader settings
exchange=bybit risk.max_loss_per_trade=5`). The mode is `paper` unless the user changed it: say so.

Not financial advice. Most people who trade actively lose money.
