# P19 Using Scope Precision

Implemented phase: Function Graph Accuracy and Maintenance Slice P19.

## Changed

- `src/indexer/cpp_structural_scan.py` now applies scope interval end lines to `using` declarations, `using namespace` directives, and namespace aliases.
- Relative project namespaces are expanded from the current scope, for example `using namespace Theme;` inside `App` becomes `App::Theme`.
- Known external roots such as `std` are not project-prefixed.

## Runtime Ownership

The structural scanner owns extraction. Function graph visibility consumes the persisted file-index candidates.

## Verification

- `tests/test_cpp_function_graph_visibility.py` verifies relative namespace expansion and active range end lines.

## Non-Goals Respected

No compiler lookup guarantee. Resolver output remains candidate-based.
