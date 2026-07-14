# OmniMCP Architecture

## Overview

OmniMCP Router is a **stdio MCP gateway** — it acts as a single MCP server to any client (Claude Desktop, Cursor, etc.) while internally managing connections to N sub-servers.

## Component Diagram

```
┌──────────────────────────────────────────────────────────────┐
│                    MCP Client (Claude, Cursor)                │
└──────────────────────────────┬───────────────────────────────┘
                               │ JSON-RPC / stdio
┌──────────────────────────────▼───────────────────────────────┐
│                         RouterServer                          │
│  (mcp.Server subclass — exposes unified tool list)           │
│  ┌──────────────────────────────────────────────────────┐    │
│  │                   RouterGateway                       │    │
│  │                                                       │    │
│  │  ┌─────────────┐  ┌───────────────┐  ┌──────────┐   │    │
│  │  │ HookSystem  │  │ Routing Table │  │ Profiler │   │    │
│  │  │ req hooks   │  │ exposed_name  │  │ per-tool │   │    │
│  │  │ res hooks   │  │ → (mgr, tool) │  │ timing   │   │    │
│  │  └─────────────┘  └───────────────┘  └──────────┘   │    │
│  │                                                       │    │
│  │  ┌─────────────────────────────────────────────────┐ │    │
│  │  │              SubServerManager × N               │ │    │
│  │  │  status: disconnected | connecting | connected  │ │    │
│  │  │  lifecycle_loop → AsyncExitStack → session      │ │    │
│  │  │  backoff: 1s → 2s → 4s → 8s → 16s (max 5)     │ │    │
│  │  └─────────────────────────────────────────────────┘ │    │
│  └──────────────────────────────────────────────────────┘    │
└──────────────────────────────────────────────────────────────┘
         │                    │                    │
  ┌──────▼──────┐     ┌───────▼──────┐    ┌───────▼──────┐
  │ Sub-Server  │     │  Sub-Server  │    │  Sub-Server  │
  │  (stdio)    │     │   (stdio)    │    │   (stdio)    │
  │  github     │     │   reddit     │    │  trend-mine  │
  └─────────────┘     └──────────────┘    └──────────────┘
```

## Request Lifecycle

```
Client: tools/call  {name: "github__create_issue", arguments: {...}}
        │
        ▼
RouterGateway.handle_call_tool()
        │
        ├─ 1. Lookup routing table → (SubServerManager, "create_issue", orig_tool)
        │
        ├─ 2. JSON Schema validation (jsonschema.validate)
        │       └─ Error → return CallToolResult(isError=True)
        │
        ├─ 3. Apply request hooks (HookSystem.apply_request_hooks)
        │       └─ Mutation, injection, logging...
        │
        ├─ 4. SubServerManager.call_tool()
        │       ├─ If status == "connecting": wait up to 5s for connect_event
        │       ├─ If status != "connected": raise RuntimeError
        │       └─ session.call_tool(original_name, mutated_args)
        │
        ├─ 5. Profiling: elapsed_ms, PERF_WARNING if > 5000ms
        │
        └─ 6. Apply response hooks (HookSystem.apply_response_hooks)
                └─ Truncation, PII redaction...
                        │
                        ▼
        Client: CallToolResult (content, isError)
```

## Tool Naming

Sub-server `github` + tool `create_issue` → exposed as `github__create_issue`

Sanitization rules:
1. Replace invalid characters (`[^a-zA-Z0-9_-]`) with `_`
2. If total length ≤ 64 chars: use as-is
3. If > 64: truncate server name to 20 chars, tool name to 42 chars

## Hot-Reload Flow

```
watch_config_loop() polls every 2s
    → mtime changed?
        → reload_config()
            → diff current vs new mcpServers
            → stop deleted servers
            → restart modified servers  
            → start new servers
            → rebuild_routing_table()
            → router_server.notify_tool_list_changed()
                → session.send_tool_list_changed() to all active clients
```

## Auto-Healing Flow

```
SubServerManager._lifecycle_loop()
    → connection attempt
        → SUCCESS: status = "connected", connect_event.set()
            → wait (sleep loop, watching for shutdown)
            → if connection drops: exception raised → _cleanup()
        → FAILURE: attempt++
            → attempt < max_attempts (5): sleep(backoff), retry
            → attempt >= max_attempts: status = "failed", loop exits
```