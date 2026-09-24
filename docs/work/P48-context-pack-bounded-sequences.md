# P48 Context Pack Bounded Sequences

Implemented phase: Context Pack / bounded tool-sequence v0.1.

## Summary

Added a curated `get_context_pack` MCP tool for LLM navigation. The first preset,
`function_context`, bundles a bounded path for function inspection without
creating a generic tool sequence executor.

## Changed Files

- `src/server/code_index_mcp_server.py`
  - Adds fixed context-pack preset constants and validation.
  - Adds MCP schema, capability metadata, dispatch, and `CodeIndexTools.get_context_pack`.
  - Executes primitive owner methods directly and records `stepsExecuted`.
- `tests/test_cpp_function_graph_mcp_schema.py`
  - Covers schema, capability metadata, max step rules, and no sequence recursion.
- `tests/test_cpp_function_graph_mcp_smoke.py`
  - Covers real-index `function_context` output.
- `README.md`
  - Documents the context-pack workflow.
- `docs/function-graph-tools.md`
  - Links Function Graph use to context packs.
- `docs/workplans/context-pack-bounded-sequences.md`
  - Active workplan for this slice.
- `docs/work/README.md`
  - Adds this worklog.

## Ownership

`src/server` owns this bounded MCP context assembler. `src/indexer` continues to
own parsing, indexing, source reads, Function Graph resolution, cache, and
storage behavior.

## Runtime Path

`get_context_pack(kind=function_context)` uses fixed primitive steps:

```text
find_symbol -> read_symbol -> get_function_body_graph -> get_symbol_neighborhood
```

If `symbolId` is supplied, the `find_symbol` step is skipped. Optional source,
graph, and neighborhood flags can skip those primitive steps. The implementation
does not call the MCP dispatcher and does not accept arbitrary step/tool lists.

## Contracts

- `maxPrimitiveSteps=6`
- first preset step count <= 4
- `nestedSequencesAllowed=false`
- `dispatchesThroughMcpToolLoop=false`
- `claimStrength=source_structure_allowed`
- `behaviorClaimsAllowed=false`

## Non-goals Respected

- No second provider loop.
- No second tool loop.
- No recursive or nested sequence execution.
- No model-provided tool names.
- No compiler-semantic or behavior claims.
- No changes to index build/runtime ownership.

## Verification

- `python -m py_compile src/server/code_index_mcp_server.py` passed.
- `python -m unittest tests.test_cpp_function_graph_mcp_schema` passed.
- `python -m unittest tests.test_cpp_function_graph_mcp_smoke` passed.
- `python -m unittest discover -s tests -p "test_*.py"` passed, 47 tests.
- `git diff --check` passed.

## Completion State

P48 is complete. The workplan was moved to
`docs/workplans/completed/context-pack-bounded-sequences.md`.
