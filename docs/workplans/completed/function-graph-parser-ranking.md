# Workplan: Function Graph Parser and Ranking

Date: 2026-06-18
Status: completed

## Summary

This slice set merged PR #6, then improved the existing Function Graph implementation on a fresh branch. It adds parser fixture coverage, broader real-index smoke coverage, resolver ranking v0.3, and cache maintenance documentation without adding public MCP tools.

## Completed Slices

1. P23 PR #6 ready, merge, and local `main` sync.
2. P24 parser fixture coverage for templates, lambdas, chained calls, and macro noise.
3. P25 multi-file real-project smoke expansion.
4. P26 resolver ranking v0.3.
5. P27 cache maintenance UX documentation.

## Ownership

Parser, resolver, cache, and storage remain under `src/indexer`.
MCP exposure remains unchanged under `src/server`.

## Non-Goals

No new public MCP tools.
No hard Tree-sitter dependency.
No behavior claims or semantic execution claims.
