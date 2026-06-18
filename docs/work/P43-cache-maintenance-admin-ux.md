# P43 Cache Maintenance Admin UX

Date: 2026-06-18
Status: Completed

## Implemented Phase

Management-only cache maintenance commands.

## Changed Files

- `src/server/code_index_mcp_server.py`
- `tests/test_cpp_function_graph_mcp_schema.py`

## Runtime Owner

`src/server` owns management command dispatch. `src/indexer/cpp_function_graph_storage.py` remains the storage owner for cache stats and pruning.

## What Changed

- Added management command `function_graph_cache_stats`.
- Added management command `function_graph_cache_prune_versions`.
- Prune rejects empty keep-version requests through the existing tool method.
- Management status advertises both Function Graph cache commands in `availableCommands`.
- No new public MCP tool schema was added.

## Evidence

Management command results include before/pruned/after cache stats and the index root.

## Tests

- Management command unit test verifies stats/prune dispatch and event breadcrumbs.
- Management status unit test verifies command discoverability.

## Non-Goals Respected

- No standard MCP tool expansion.
- No new database.
- No cache sidecar requirement.
