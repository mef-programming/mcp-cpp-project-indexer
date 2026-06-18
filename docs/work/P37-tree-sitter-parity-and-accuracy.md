# P37 Tree-sitter Parity And Accuracy

Date: 2026-06-18
Workplan: `docs/workplans/tree-sitter-function-graph-adapter.md`
Status: Implemented

## Implemented

- Tree-sitter tests skip cleanly when dependencies are missing.
- When dependencies are present, tests exercise AST-based call/member/local/control-flow extraction.
- Coverage includes templates, lambdas, chained calls, macro noise, member access, and control-flow markers.

## Owner

- `tests/test_cpp_function_graph_extract.py`

## Verification

- Targeted parser tests and full unit discovery.

## Non-Goals

- No claim that Tree-sitter is default.
- No runtime behavior analysis.
