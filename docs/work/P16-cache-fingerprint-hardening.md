# P16 Cache Fingerprint Hardening

Implemented phase: Function Graph Hardening Slice P16.

## Changed

- `src/indexer/cpp_function_graph_cache.py` adds structured graph cache options and stable option fingerprints.
- `src/indexer/cpp_function_graph_service.py` uses the option fingerprint in the resolver cache version.
- Graph fingerprints record the normalized graph options and their fingerprint.

## Runtime Ownership

Function graph cache ownership stays in `src/indexer`.
SQLite storage schema stays unchanged.

## Verification

- `tests/test_cpp_function_graph_source.py` asserts cache miss when graph options differ.

## Non-Goals Respected

No API shape change. No second storage root. No broad cache migration.
