# Claude Code

Needs [uv](https://docs.astral.sh/uv/) (`curl -LsSf https://astral.sh/uv/install.sh | sh` or `brew install uv`).

```bash
claude mcp add bytx-trader -e BYTX_TRADER_HOME="$PWD/crypto" -- \
  uvx --refresh-package bytx --refresh-package bytx-trader --from bytx-trader@latest bytx-trader-mcp
```

Every start runs the newest released `bytx-trader` with the newest compatible `bytx`: nothing to update by hand.

Demo mode (exchange demo/testnet) additionally needs the keys in the environment of the server, never in the chat:
`-e BYTX_API_KEY=… -e BYTX_SECRET=…` (`BYTX_PASSWORD` for OKX, KuCoin, Bitget) and `"mode": "demo"` in `crypto/config.json`.

Keep the `confirm` tool on "ask every time" (do not add it to allowed tools).
