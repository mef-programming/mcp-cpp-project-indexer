# P31 Runtime Accuracy Smoke Matrix

Date: 2026-06-18
Workplan: `docs/workplans/function-graph-runtime-quality-parser-adapter-vnext.md`
Status: Implemented

## Implemented

- Expanded the real-index MCP smoke fixture with multiple files, namespaces, using-visible helpers, static helper candidates, member calls, external APIs, cache-only reads, xrefs, and neighborhood output.
- Preserved the rule that xrefs and neighborhood read only persisted computed graph edges.

## Owner

- `tests/test_cpp_function_graph_mcp_smoke.py`
- Runtime path remains `src/indexer` through existing service/storage code.

## Verification

- Targeted MCP smoke test validates compute, cache-only, xrefs, neighborhood, external marking, data edges, and control-flow filtering.

## Non-Goals

- No new MCP endpoint.
- No xref completeness claim for graphs that were not computed first.
