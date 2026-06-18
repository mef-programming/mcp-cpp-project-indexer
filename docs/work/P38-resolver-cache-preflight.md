# P38 Resolver Cache Preflight

Date: 2026-06-18
Status: Completed

## Implemented Phase

Preflight and workplan hygiene for `docs/workplans/function-graph-resolver-precision-cache-maintenance.md`.

## Changed Files

- `docs/workplans/completed/tree-sitter-function-graph-adapter.md`
- `docs/workplans/function-graph-resolver-precision-cache-maintenance.md`
- `docs/workplans/README.md`
- `docs/work/P38-resolver-cache-preflight.md`
- `docs/work/README.md`

## Runtime Owner

No runtime behavior changed in this slice. Follow-up runtime owners are:

- `src/indexer`: parser extracts, visibility context, resolver, cache/storage.
- `src/server`: existing management command dispatch only.

## Baseline

- PR #9 is merged into `origin/main`.
- Local `main` has an unrelated ahead commit, so implementation branch starts from `origin/main` to keep this patch focused.
- Tree-sitter remains optional and explicit; Lightweight remains default.
- SmartFTP real-project sample remains a smoke target when the indexed server is available.

## Test Matrix

- Parser fixture tests for local initializers and operator syntax.
- Resolver fixture tests for template normalization, overload ranking, auto initializer hints, nested/base candidates, and operator candidates.
- Management command tests for cache stats/prune dispatch.
- Full unittest discovery before PR.

## Non-Goals Respected

- No new public MCP tools.
- No behavior claims.
- No second parser loop.
- No unrelated local files staged or edited.
