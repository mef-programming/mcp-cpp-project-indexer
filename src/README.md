# Source

Purpose: Source tree for the C++ project indexer implementation, MCP server, and user interfaces.

Use this folder when the question is about:

- implementation source layout
- where indexer, server, and UI implementation files live
- source-tree orientation before reading Python files
- separating public root documentation from agent navigation

Do not use this folder first when the question is about:

- public project pitch or quickstart wording
- generated project indexes
- downstream MCP client configuration
- repository maintenance checklists

## Map

```text
indexer/  C++ index build, update, scanner, SQLite, watcher, and export logic
server/   MCP HTTP/stdio server, management API, and embedded web assets
ui/       Textual TUI, terminal control center, and legacy menu UI
```

## Start Here

- Index build/update/scanner logic: `indexer/README.md`
- MCP server and management API: `server/README.md`
- TUI and command control: `ui/README.md`

## Boundaries

This folder owns implementation source. Root-level wrapper scripts exist only
for backward-compatible command lines and should stay thin.
