# P44 Real Project Resolver Cache Smoke

Date: 2026-06-18
Status: Completed

## Implemented Phase

Fixture-backed resolver/cache smoke coverage with SmartFTP follow-up target.

## Changed Files

- `tests/test_cpp_function_graph_extract.py`
- `tests/test_cpp_function_graph_resolver.py`
- `tests/test_cpp_function_graph_visibility.py`
- `tests/test_cpp_function_graph_mcp_schema.py`

## Runtime Owner

`src/indexer` owns Function Graph runtime behavior. `src/server` owns management dispatch.

## What Changed

- Added targeted fixture coverage for candidate quality improvements.
- Kept SmartFTP real-project validation as a manual/available-server smoke target.

## Verification Scope

Validated:

- template call normalization
- overload ambiguity preservation
- auto initializer member hints
- nested/base candidate context
- operator structural call edges
- management-only cache stats/prune command routing

Live environment note:

- `http://127.0.0.1:8766/management/status` was reachable and reported SmartFTP indexed at `F:/Projects/smartftp`.
- The running server process was not restarted onto this branch during the slice, so new resolver v0.4 behavior was validated through fixtures rather than live HTTP calls.

## Non-Goals Respected

- No behavior claims.
- No public MCP tool expansion.
- No unrelated local files touched.
