# P46 Cache Maintenance Dry-Run Prune

Date: 2026-06-18
Status: Completed

## Implemented Phase

Dry-run-first Function Graph cache pruning through the management command surface.

## Changed Files

- `src/indexer/cpp_function_graph_storage.py`
- `src/server/code_index_mcp_server.py`
- `tests/test_cpp_function_graph_storage.py`
- `tests/test_cpp_function_graph_mcp_schema.py`
- `docs/function-graph-tools.md`

## Runtime Owner

`src/indexer/cpp_function_graph_storage.py` owns dry-run prune calculation and deletion. `src/server/code_index_mcp_server.py` owns management command validation and dispatch.

## What Changed

- `function_graph_cache_prune_versions` defaults to `dryRun=true`.
- `dryRun=false` is required for actual deletion.
- `keepCurrent=true` adds the current default parser cache version and all stored resolver versions matching the current resolver prefix.
- Empty prune requests without keep versions or `keepCurrent=true` remain invalid.

## Tests

- Storage tests prove dry-run returns the same prune counts without changing cache tables.
- Management tests verify default dry-run behavior, explicit commit behavior, and empty-input rejection.

## Non-Goals Respected

- No public MCP tool expansion.
- No cache recompute.
- No behavior claims.
