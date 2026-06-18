# Workplan: Function Graph Accuracy and Maintenance

Date: 2026-06-18
Status: completed

## Summary

This follow-up keeps the existing Function Graph MCP surface stable while improving candidate precision and cache maintenance. It does not add tools, behavior claims, or required parser dependencies.

## Completed Slices

1. P18 hardening patch finalization on a dedicated branch.
2. P19 using-scope precision with scope-bounded active ranges and relative namespace expansion.
3. P20 Tree-sitter real adapter spike decision: keep optional adapter gated until dependency and extraction parity are available.
4. P21 resolver candidate quality for local type hints and qualified data candidates.
5. P22 cache/storage maintenance with stats and parser/resolver version pruning.

## Ownership

`src/indexer` owns scanning, parser adapters, visibility, resolver, cache, and storage.
`src/server` owns MCP schema and dispatch only; no server tool expansion was needed.

## Non-Goals

No new public MCP tools.
No hard Tree-sitter dependency.
No vector sidecar.
No behavior evidence; output remains `source_structure_allowed` and `behaviorClaimsAllowed=false`.
