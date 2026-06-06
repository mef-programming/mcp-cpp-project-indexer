# UI

Purpose: Local terminal and Textual control surfaces for operating the C++ indexer.

Use this folder when the question is about:

- Textual TUI cockpit behavior
- terminal control-center commands
- UI settings persistence
- starting build/update/server/watcher actions from a local UI
- legacy menu behavior

Do not use this folder first when the question is about:

- MCP HTTP request handling
- embedded browser management UI assets
- scanner or SQLite index implementation
- public README wording

## Map

```text
indexer_tui.py      Textual cockpit UI
indexer_control.py  dependency-free terminal control center
indexer_menu.py     legacy menu UI
```

## Start Here

- Textual cockpit: `indexer_tui.py`
- Terminal fallback: `indexer_control.py`
- Legacy menu: `indexer_menu.py`

## Boundaries

This folder owns local operator interfaces. It starts root-level wrapper scripts
for compatibility and does not own index generation or MCP request semantics.

