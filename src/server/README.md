# Server

Purpose: MCP stdio/HTTP server, management API, tool dispatch, and embedded web management UI.

Use this folder when the question is about:

- `/mcp` JSON-RPC request handling
- MCP tool listing and dispatch
- HTTP transport, authentication, TLS, and CORS behavior
- management status, command, and log endpoints
- embedded web management UI assets

Do not use this folder first when the question is about:

- C++ parsing or index file generation
- Textual TUI behavior
- terminal control-center command rendering
- public README or performance documentation

## Map

```text
code_index_mcp_server.py  MCP server, management API, and tool dispatch
server_ui/                embedded browser management UI assets
```

## Start Here

- MCP/management server behavior: `code_index_mcp_server.py`
- Browser management page: `server_ui/`

## Boundaries

This folder owns serving and management surfaces. It uses indexer code from
`src/indexer` and UI helper code from `src/ui`, but should not own scanner
semantics or TUI layout.

