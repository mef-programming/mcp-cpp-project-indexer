# Function Graph Runtime Quality / Parser Adapter vNext

Date: 2026-06-18
Status: Completed

## Summary

Completed runtime quality guardrails for parser adapter readiness before the real Tree-sitter integration. Runtime ownership stayed in `src/indexer`; MCP exposure stayed stable.

## Completed Slices

- P28: Workplan setup and accuracy baseline.
- P29: Parser status/capability contract.
- P30: Tree-sitter optional parse-probe spike.
- P31: Runtime accuracy smoke matrix.
- P32: Resolver/cache guardrails with parser status fingerprints.

## Verification

- `python -m py_compile ...`
- `python -m unittest tests.test_cpp_function_graph_extract tests.test_cpp_function_graph_source tests.test_cpp_function_graph_mcp_smoke`
- `python -m unittest discover -s tests -p "test_*.py"`
- `git diff --check`
- `git diff --cached --check`

## Follow-Up

The active follow-up is `docs/workplans/tree-sitter-function-graph-adapter.md`.
