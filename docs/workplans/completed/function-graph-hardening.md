# Workplan: Function Graph Hardening

Date: 2026-06-17
Status: completed

## Summary

Follow-up slices after the on-demand function graph merge. The work hardens the existing function graph path without adding MCP tools: worklog hygiene, optional Tree-sitter dependency gating, indexed `using` visibility, explicit graph cache option fingerprints, and compact operator documentation.

## Completed Slices

1. P13 Workplan and worklog hygiene.
2. P14 Tree-sitter dependency decision and adapter gate.
3. P15 Indexed `using` declarations, `using namespace` directives, and namespace aliases as visibility candidates.
4. P16 Structured graph option fingerprints for cache separation.
5. P17 Operator-facing MCP function graph documentation.

## Ownership

Production function graph logic remains under `src/indexer`.
MCP exposure remains under `src/server/code_index_mcp_server.py`.
Documentation lives under `docs/workplans`, `docs/work`, and `docs/function-graph-tools.md`.

## Non-Goals

No new public MCP tools.
No hard Tree-sitter dependency.
No second parser loop, provider loop, or tool loop.
No behavior claims; function graph output remains `source_structure_allowed` with `behaviorClaimsAllowed=false`.
