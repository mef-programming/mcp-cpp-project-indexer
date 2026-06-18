# P28 Runtime Quality Baseline

Date: 2026-06-18
Workplan: `docs/workplans/function-graph-runtime-quality-parser-adapter-vnext.md`
Status: Implemented

## Implemented

- Created the active Function Graph runtime quality workplan.
- Established the next parser accuracy baseline around templates, lambdas, chained/nested calls, macro noise, using-visible helpers, member access, data access, and external APIs.
- Kept this slice as a planning/baseline artifact plus targeted fixture expectations, with no public API changes.

## Owner

- Runtime owner: `src/indexer`.
- Work tracking owner: `docs/workplans` and `docs/work`.

## Verification

- Covered by the targeted parser and MCP smoke tests in later slices of this patch.

## Non-Goals

- No new MCP tool.
- No hard Tree-sitter dependency.
- No behavior claims.
