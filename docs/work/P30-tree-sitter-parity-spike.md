# P30 Tree-sitter Parity Spike

Date: 2026-06-18
Workplan: `docs/workplans/function-graph-runtime-quality-parser-adapter-vnext.md`
Status: Implemented

## Implemented

- Kept Tree-sitter optional and dependency-gated.
- Missing dependencies still produce deterministic unavailable behavior.
- When dependencies are present, the adapter probes Tree-sitter parse-tree creation and returns normalized Function Graph extraction through the existing parser protocol.

## Owner

- `src/indexer/cpp_function_graph_tree_sitter.py`

## Verification

- Adapter tests cover unavailable behavior when dependencies are missing.
- Adapter tests exercise parse/projection behavior when dependencies are present.

## Non-Goals

- No hard dependency in normal indexer usage.
- No public MCP setting or tool was added.
