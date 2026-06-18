# P36 Tree-sitter Parser Selection Gate

Date: 2026-06-18
Workplan: `docs/workplans/tree-sitter-function-graph-adapter.md`
Status: Implemented

## Implemented

- Kept `FunctionGraphSourceService` default parser unchanged as Lightweight.
- Tree-sitter remains explicit through parser injection or tests.
- Existing parser status/cache guardrails keep Tree-sitter and Lightweight cache entries separate.

## Owner

- `src/indexer/cpp_function_graph_service.py`
- `src/indexer/cpp_function_graph_tree_sitter.py`

## Verification

- Service tests continue to prove default Lightweight path and parser-status cache separation.

## Non-Goals

- No environment-driven automatic parser selection.
- No new MCP tool or schema.
