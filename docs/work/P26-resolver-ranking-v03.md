# P26 Resolver Ranking v0.3

Implemented phase: Function Graph Parser and Ranking Slice P26.

## Changed

- Resolver version moved to `cpp-function-graph-resolver-v0.3`.
- Ranking weights now more clearly prefer same-class and same-namespace candidates before lower-confidence fallback groups.
- Tests cover same-namespace precedence over using/fallback/module candidates.

## Verification

- `tests/test_cpp_function_graph_resolver.py`

## Non-Goals Respected

Ambiguous candidates remain ambiguous; no fake exact behavior claims were added.
