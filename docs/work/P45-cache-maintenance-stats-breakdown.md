# P45 Cache Maintenance Stats Breakdown

Date: 2026-06-18
Status: Completed

## Implemented Phase

Expanded Function Graph cache stats for management operators.

## Changed Files

- `src/indexer/cpp_function_graph_storage.py`
- `src/server/code_index_mcp_server.py`
- `tests/test_cpp_function_graph_storage.py`
- `docs/function-graph-tools.md`

## Runtime Owner

`src/indexer/cpp_function_graph_storage.py` owns the cache tables and stats queries. `src/server/code_index_mcp_server.py` only dispatches the existing management command.

## What Changed

- `cache_stats()` now includes parser version breakdowns, resolver version breakdowns, oldest/newest cache timestamps, and edge counts per stored graph.
- Parser version stats include AST-only cache entries.
- `function_graph_cache_stats` returns the richer stats payload without adding a new MCP tool.

## Tests

- Storage unit tests cover version breakdowns, timestamps, edge counts, and AST-only parser versions.

## Non-Goals Respected

- No graph recompute.
- No new database or sidecar file.
- No public MCP tool expansion.
