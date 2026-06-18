# P17 Function Graph Operator Docs

Implemented phase: Function Graph Hardening Slice P17.

## Changed

- Added `docs/function-graph-tools.md` with compact usage guidance for existing function graph MCP tools.
- Documented cache modes, xref/neighborhood behavior, and the no-behavior-claims contract.

## Runtime Ownership

Documentation only. Server tool ownership remains in `src/server/code_index_mcp_server.py`.

## Verification

Final verification for this slice is covered by `git diff --check`.

## Non-Goals Respected

No new public MCP endpoint. No tool-list expansion.
