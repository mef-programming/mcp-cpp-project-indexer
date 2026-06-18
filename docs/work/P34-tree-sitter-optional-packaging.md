# P34 Tree-sitter Optional Packaging

Date: 2026-06-18
Workplan: `docs/workplans/tree-sitter-function-graph-adapter.md`
Status: Implemented

## Implemented

- Added `requirements-function-graph-optional.txt` for optional Tree-sitter adapter dependencies.
- Kept normal indexer usage dependency-free.
- Preserved lazy dependency detection through `tree_sitter_cpp_dependency_status()`.

## Owner

- `src/indexer/cpp_function_graph_tree_sitter.py`
- `requirements-function-graph-optional.txt`

## Verification

- Covered by parser tests with missing-dependency skip behavior.

## Non-Goals

- No hard dependency.
- No installer or packaging migration.
