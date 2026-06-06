# Indexer

Purpose: C++ index build, update, scanner, SQLite, watcher, orientation, and export implementation.

Use this folder when the question is about:

- full project index builds
- incremental source updates and watcher-triggered updates
- C++ structural, module, include, data, symbol, type-alias, and comment scanning
- SQLite lookup index writing and reading
- orientation document indexing
- JSONL export and module-map helpers

Do not use this folder first when the question is about:

- HTTP MCP request handling
- management API routes or web UI assets
- Textual TUI layout and command buttons
- public README wording or MCP client configuration

## Map

```text
build_project_index.py   full project index build CLI implementation
update_project_index.py  incremental update CLI implementation
watch_project_index.py   polling watcher and update trigger
cpp_project_index.py     loaded index model and source-range tool logic
cpp_file_index.py        per-file C++ scan/index builder
cpp_structural_scan.py   structural C++ symbol/event scanner
cpp_module_scan.py       C++20 module/import scanner
cpp_data_emit.py         data/member declaration emitter
cpp_symbol_emit.py       symbol emitter
cpp_type_alias_emit.py   type alias/typedef emitter
cpp_index_sqlite.py      SQLite lookup index writer/reader
cpp_orientation_index.py README/AGENTS/topology orientation parser
cpp_change_tracking.py   git/worktree change and hunk routing helpers
```

## Start Here

- Full index build flow: `build_project_index.py`
- Incremental update flow: `update_project_index.py`
- Watcher update flow: `watch_project_index.py`
- Source/range lookup behavior: `cpp_project_index.py`
- Per-file extraction: `cpp_file_index.py`
- SQLite storage: `cpp_index_sqlite.py`
- Orientation parsing: `cpp_orientation_index.py`

## Boundaries

This folder owns index data production and index-backed source/range lookup.
It does not own HTTP transport, management UI rendering, or TUI layout.

