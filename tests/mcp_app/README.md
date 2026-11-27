# The app of `qa mcp`, in a real MCP Apps host

`qaboard/mcp_app.html` is the view hosts (Claude, Claude Desktop, VS Code, Open WebUI...) show next to the answers of `compare`,
`metric_history` and `look_at_output`. It talks to the host with JSON-RPC over postMessage, without an SDK. This check
runs it in a page that uses the official host bridge (`AppBridge` from `@modelcontextprotocol/ext-apps`), with the
tools of `qa mcp` against the fake server of the Python tests, and fails when the protocol breaks (e.g. a field the
host's schema requires). `package.json` pins the bridge's version that Open WebUI uses (2.0.4 in its 0.13 releases):
bump it with theirs.

```bash
uv sync --extra dev --extra mcp          # at the root
cd tests/mcp_app
npm install && npm run build
../../.venv/bin/python harness.py 5199 &  # the host, the app and the tools
npm run check                            # Playwright; screenshots in screenshots/
```
Not in CI yet. Run it when you change `mcp_app.html` or upgrade `mcp`.
