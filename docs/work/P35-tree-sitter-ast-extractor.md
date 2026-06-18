# P35 Tree-sitter AST Extractor

Date: 2026-06-18
Workplan: `docs/workplans/tree-sitter-function-graph-adapter.md`
Status: Implemented

## Implemented

- Replaced Tree-sitter parse-probe-plus-lightweight extraction with Tree-sitter AST traversal.
- Extracts calls, qualified calls, member calls, member access, local declarations, and control-flow markers into `FunctionAstExtract`.
- Unsupported forms are skipped rather than guessed.

## Owner

- `src/indexer/cpp_function_graph_tree_sitter.py`

## Verification

- Active Tree-sitter extractor tests run when optional packages are installed.
- Missing optional dependencies keep the default test suite green.

## Non-Goals

- No automatic Tree-sitter activation.
- No behavior claims.
