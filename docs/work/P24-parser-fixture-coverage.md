# P24 Parser Fixture Coverage

Implemented phase: Function Graph Parser and Ranking Slice P24.

## Changed

- Lightweight parser version moved to v0.2.
- Template calls, chained result member calls, templated local declarations, and macro-like invocations have focused coverage.
- Macro-like all-caps invocations are skipped as call edges.

## Verification

- `tests/test_cpp_function_graph_extract.py`

## Non-Goals Respected

Tree-sitter remains optional and gated.
