<div align="center">

# OmniMCP Router

### The Universal MCP Gateway — One Entry Point to Rule All Your AI Tools

[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue?style=for-the-badge&logo=python&logoColor=white)](https://www.python.org/downloads/)
[![MCP Protocol](https://img.shields.io/badge/MCP-1.2%2B-blueviolet?style=for-the-badge)](https://modelcontextprotocol.io)
[![License: MIT](https://img.shields.io/badge/License-MIT-green?style=for-the-badge)](LICENSE)
[![Tests](https://img.shields.io/github/actions/workflow/status/Simonc44/OmniMCP/ci.yml?branch=main&style=for-the-badge&label=tests)](https://github.com/Simonc44/OmniMCP/actions)
[![Code style: black](https://img.shields.io/badge/code%20style-black-000000?style=for-the-badge)](https://github.com/psf/black)

*Plug any MCP server. Claude sees them all as one.*

[Features](#features) • [Quick Start](#quick-start) • [Configuration](#configuration) • [Tests](#tests) • [Client Setup](#client-setup) • [Contributing](#contributing)

</div>

---

## The Problem

You have 10 MCP servers: GitHub, Reddit, Notion, Stripe, a custom scraper…  
Your Claude Desktop config is a mess. Each client can only talk to one server at a time.  
Every crash brings everything down. There's no observability. No resilience.

**OmniMCP fixes all of that.**

---

## Features

| Feature | Description |
|---|---|
| **Zero Hard-Coded Tools** | Dynamically discovers tools from every sub-server at startup |
| **Async Non-Blocking Routing** | Parallel requests routed concurrently via `anyio` — no bottleneck |
| **Auto-Healing** | Exponential backoff reconnection when a sub-server crashes |
| **Hot-Reload** | Detects `mcp_router_config.json` changes live — no restart needed |
| **Hook System** | Mutate, intercept, and validate requests/responses in middleware pipelines |
| **Performance Monitoring** | Real-time profiling with `PERF_WARNING` for tools exceeding 5s |
| **Isolated Lifecycle** | Each sub-server has its own `AsyncExitStack` — one crash ≠ global failure |
| **Safe Namespacing** | Tools exposed as `{server}__{tool}`, sanitized to MCP spec (`[a-zA-Z0-9_-]{1,64}`) |
| **Persistent Logging** | All logs written to `mcp_router.log` + `stderr` (captured by Claude) |
| **JSON Schema Validation** | Strict input validation before forwarding any tool call |
| **Response Truncation Hook** | Auto-truncates responses >50k chars to protect context windows |
| **Windows + Linux** | Signal handling for both platforms |

---

## Quick Start

```bash
# 1. Clone the repo
git clone https://github.com/Simonc44/OmniMCP.git
cd OmniMCP

# 2. Install dependencies (Python 3.10+ required)
pip install -r requirements.txt

# 3. Edit your config
notepad mcp_router_config.json   # Windows
# or: nano mcp_router_config.json

# 4. Run it
python router.py --config mcp_router_config.json
```

> **That's it.** OmniMCP starts, connects to all your sub-servers, and exposes a single unified MCP stdio interface.

---

## Project Structure

```
OmniMCP/
├── router.py                  #  Core gateway — routing, healing, hot-reload, hooks
├── mock_server.py             #  Lightweight mock MCP server for testing
├── run_integration_test.py    #  Full integration test suite (async, healing, hot-reload)
├── mcp_router_config.json     #   Production config — your real MCP servers go here
├── test_config.json           #  Test config — uses mock_server.py instances
├── requirements.txt           #  Dependencies: mcp, pydantic, jsonschema, anyio
├── docs/                      #  Architecture diagrams and assets
├── .github/
│   ├── workflows/ci.yml       #  GitHub Actions CI pipeline
│   └── ISSUE_TEMPLATE/        #  Bug report & feature request templates
├── CHANGELOG.md               #  Version history
├── CONTRIBUTING.md            #  Contribution guide
└── LICENSE                    #   MIT License
```

---

## Configuration

The config file follows the exact same syntax as `claude_desktop_config.json` — so you can **copy-paste** your existing Claude Desktop config directly.

```json
{
  "mcpServers": {
    "github": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-github"],
      "env": { "GITHUB_PERSONAL_ACCESS_TOKEN": "ghp_xxxx" }
    },
    "reddit": {
      "command": "python",
      "args": ["C:/path/to/reddit/server.py"],
      "env": {
        "REDDIT_CLIENT_ID": "your_client_id",
        "REDDIT_CLIENT_SECRET": "your_secret",
        "REDDIT_USER_AGENT": "OmniMCP/1.0"
      }
    },
    "trend-mining": {
      "command": "python",
      "args": ["-m", "trend_mining.server"],
      "env": { "PLAYWRIGHT_HEADLESS": "true" }
    }
  }
}
```

Tools are exposed as `{server_name}__{tool_name}` — e.g. `github__create_issue`, `reddit__search_posts`.

### Hot-Reload

OmniMCP watches your config file every 2 seconds. Add, remove, or modify a server — it reconnects live and sends `notifications/tools/list_changed` to your client. **No restart needed.**

---

## Tests

The integration test suite validates the full feature set end-to-end:

```bash
python run_integration_test.py
```

| # | Test | What it validates |
|---|---|---|
| 1 | **Aggregation** | All tools from all sub-servers are discovered and exposed |
| 2 | **Async Parallelism** | Two 2s calls finish in ~2s total, not 4s |
| 3 | **Perf Monitoring** | A 6s call triggers `PERF_WARNING` in logs |
| 4 | **Auto-Healing** | Server crash → automatic reconnect → tool works again |
| 5 | **Hot-Reload** | Config change → `list_changed` notification → updated tool list |

---

## Client Setup

### Claude Desktop

Replace your entire `claude_desktop_config.json` with just OmniMCP:

```json
{
  "mcpServers": {
    "omni-mcp": {
      "command": "python",
      "args": [
        "C:/path/to/OmniMCP/router.py",
        "--config",
        "C:/path/to/OmniMCP/mcp_router_config.json"
      ]
    }
  }
}
```

### Cursor

In Cursor MCP settings, add a stdio server:
- **Name**: `OmniMCP`
- **Command**: `python C:/path/to/OmniMCP/router.py --config C:/path/to/OmniMCP/mcp_router_config.json`

---

## Hook System

OmniMCP ships with a middleware pipeline for request/response mutation:

```python
# Register a custom request hook (e.g. inject auth)
@gateway.hook_system.register_request_hook
async def inject_auth(server_name: str, tool_name: str, arguments: dict) -> dict:
    if server_name == "my-api":
        arguments["api_key"] = os.environ["MY_SECRET_KEY"]
    return arguments

# Register a custom response hook (e.g. redact PII)
@gateway.hook_system.register_response_hook
async def redact_pii(server_name, tool_name, result):
    # process result.content here
    return result
```

Built-in hooks:
- **Response Truncation** — auto-truncates responses >50,000 chars with a clear notice

---

## Resilience Architecture

```
┌──────────────────────────────────────────────────────┐
│              Claude Desktop / Cursor                  │
└──────────────────────┬───────────────────────────────┘
                       │ stdio (single MCP connection)
┌──────────────────────▼───────────────────────────────┐
│                  OmniMCP Router                       │
│  ┌─────────────────────────────────────────────────┐ │
│  │  Hook System │  Schema Validator  │   Profiler   │ │
│  └─────────────────────────────────────────────────┘ │
│  ┌──────────────┐ ┌──────────────┐ ┌────────────┐   │
│  │SubServer A   │ │SubServer B   │ │SubServer C │   │
│  │ connected  │ │ reconnecting│ │ connected│   │
│  │Auto-Healing  │ │Backoff: 4s   │ │            │   │
│  └──────────────┘ └──────────────┘ └────────────┘   │
└──────────────────────────────────────────────────────┘
```

If Sub-Server B crashes:
- Its tools are hidden from the tool list
- A reconnect loop starts with exponential backoff (1s → 2s → 4s → 8s → 16s)
- On success: tools reappear, client gets `notifications/tools/list_changed`
- After 5 failed attempts: marked `failed`, loop stops
- **Sub-Servers A and C are completely unaffected**

---

## Contributing

PRs are welcome! See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

1. Fork the repo
2. Create your branch: `git checkout -b feat/my-feature`
3. Run tests: `python run_integration_test.py`
4. Open a PR against `main`

---

## Changelog

See [CHANGELOG.md](CHANGELOG.md).

---

## License

MIT — see [LICENSE](LICENSE).

---

<div align="center">

Made for the MCP ecosystem
*If this saved you hours, drop a star*

</div>
