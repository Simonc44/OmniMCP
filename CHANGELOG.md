# Changelog

All notable changes to OmniMCP Router will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [1.0.0] - 2025-07-14

### Added
- Core MCP Gateway with stdio transport
- Dynamic tool discovery from sub-servers at startup
- Async non-blocking parallel routing via `anyio`
- **Auto-Healing**: exponential backoff reconnection (1s → 2s → 4s → 8s → 16s, max 5 attempts)
- **Hot-Reload**: live config file watcher (2s polling), sends `notifications/tools/list_changed`
- **Hook System**: middleware pipeline for request and response mutation
- **Performance Monitoring**: per-tool timing with `PERF_WARNING` threshold at 5s
- JSON Schema input validation via `jsonschema`
- Safe tool namespacing: `{server}__{tool}` with MCP-spec sanitization (`^[a-zA-Z0-9_-]{1,64}$`)
- Collision detection in routing table
- Built-in response truncation hook (>50,000 chars)
- Persistent logging to `mcp_router.log` + stderr
- Graceful shutdown with signal handling (SIGINT, SIGTERM, Windows fallback)
- `SubServerManager` class with isolated `AsyncExitStack` per server
- `RouterGateway` orchestrator with `update_lock` for thread-safe routing table rebuilds
- `RouterServer` subclass capturing active sessions for notifications
- Mock server (`mock_server.py`) for testing: `greet`, `slow_add`, `exit_server` tools
- Full integration test suite covering: aggregation, parallelism, perf monitoring, auto-healing, hot-reload