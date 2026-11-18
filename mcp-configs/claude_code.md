# Claude Code

```bash
pip install bytx-trader
claude mcp add bytx-trader -e BYTX_TRADER_HOME="$PWD/crypto" -- bytx-trader-mcp
```

Demo mode (exchange demo/testnet) additionally needs the keys in the environment of the server, never in the chat:
`-e BYTX_API_KEY=… -e BYTX_SECRET=…` (`BYTX_PASSWORD` for OKX, KuCoin, Bitget) and `"mode": "demo"` in `crypto/config.json`.

Keep the `confirm` tool on "ask every time" (do not add it to allowed tools).
