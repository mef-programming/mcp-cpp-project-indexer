# P14 Tree-sitter Adapter Gate

Implemented phase: Function Graph Hardening Slice P14.

## Changed

- `src/indexer/cpp_function_graph_tree_sitter.py` now exposes a dependency status helper.
- The Tree-sitter adapter remains optional and unavailable by default.
- If optional packages are absent, callers get a structured unavailable reason.
- If optional packages are present, the adapter still refuses activation until extraction parity is implemented.

## Runtime Ownership

`src/indexer` owns parser adapter selection. `FunctionGraphSourceService` continues to use `LightweightFunctionBodyParser` by default.

## Verification

- `tests/test_cpp_function_graph_extract.py` asserts adapter isolation and dependency status reporting.

## Non-Goals Respected

No hard Tree-sitter dependency was added. No second parser loop was introduced.
