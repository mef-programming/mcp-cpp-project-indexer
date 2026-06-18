# P47 Management UI Function Graph Cache

Date: 2026-06-18
Status: Completed

## Implemented Phase

Management UI integration for Function Graph cache stats and dry-run-first pruning.

## Changed Files

- `src/server/server_ui/index.html`
- `src/server/server_ui/app.js`
- `src/server/server_ui/styles.css`
- `docs/function-graph-tools.md`
- `docs/work/P47-management-ui-function-graph-cache.md`
- `docs/work/README.md`

## Runtime Owner

`src/server/server_ui` owns display and operator actions only. Cache stats and pruning still run through the existing management API and server-side storage owner.

## What Changed

- Added a Function Graph Cache panel to the Management UI.
- The panel loads cache stats with parser/resolver version breakdowns, oldest/newest timestamps, and graph edge counts.
- Added dry-run prune and commit prune buttons.
- `keepCurrent=true` is enabled by default.
- Commit stays disabled until a dry-run succeeds and uses the exact dry-run payload with `dryRun=false`.
- Unsupported or older server responses show a panel-local error without breaking status/log polling.

## Tests

- `node --check src/server/server_ui/app.js`
- Browser smoke against `http://127.0.0.1:8766/server/ui/index.html`:
  - panel rendered
  - stats loaded
  - dry-run prune returned `AST 0, graphs 0, edges 0`
  - commit prune reused the preview and returned `AST 0, graphs 0, edges 0`
  - no console errors
- Existing backend tests remain required before PR.

## Non-Goals Respected

- No new public MCP tool.
- No UI authority over runtime flow beyond existing management commands.
- No new framework, build step, or broad UI refactor.
