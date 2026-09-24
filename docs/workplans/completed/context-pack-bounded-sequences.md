# Context Pack Bounded Sequences

Status: completed

## Summary

Add one curated context-pack MCP tool for LLM navigation. The tool returns a small
server-owned pack assembled from primitive indexer operations. It is not a
generic tool-sequence executor and must not create a second tool loop.

## Scope

- Owner: `src/server` for MCP schema, validation, fixed preset execution, and
  response packing.
- Runtime inputs: loaded project index, existing symbol/source readers, and
  existing Function Graph methods.
- Runtime output: compact structural context pack with audited executed steps.
- Enabled by: normal MCP tool listing as `get_context_pack`.
- Removed by: deleting the tool schema, handler, preset constants, docs, and
  tests.

## Hard Rules

- Maximum primitive steps per preset: 6.
- Default first preset executes at most 4 primitive steps.
- No nested sequences.
- A sequence tool must never call itself.
- A sequence tool must never call another sequence tool.
- No model-provided arbitrary tool names or step lists.
- No MCP dispatcher recursion from inside the pack implementation.
- No provider calls, behavior claims, dynamic dispatch certainty, or external API
  semantics.

## P48 Slice

Implement `get_context_pack(kind=function_context)`:

1. Locate callable symbol from `query` or accept `symbolId`.
2. Optionally read the symbol source range.
3. Optionally compute/read the function body graph.
4. Optionally read persisted symbol neighborhood.
5. Return step audit, fingerprints, source, graph, neighborhood, and claim
   contract.

## Test Plan

- Schema and capability metadata test.
- Guardrail test for no recursive/sequence steps and no model-supplied tool
  list.
- Real-index smoke test for the function context pack.
- Required checks: `python -m py_compile`, relevant unit/smoke tests,
  `git diff --check`.
