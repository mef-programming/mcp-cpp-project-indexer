# P22 Cache Storage Maintenance

Implemented phase: Function Graph Accuracy and Maintenance Slice P22.

## Changed

- `src/indexer/cpp_function_graph_storage.py` records lightweight function graph storage metadata.
- Added cache stats for AST extracts, graph results, and graph edges.
- Added parser/resolver version pruning for old cache rows and associated persisted edges.

## Runtime Ownership

Function graph cache/storage remains in the existing index SQLite database.

## Verification

- `tests/test_cpp_function_graph_storage.py` covers stats and version pruning.

## Non-Goals Respected

No new database, no sidecar, and no MCP API expansion.
