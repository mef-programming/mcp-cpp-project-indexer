# Function Graph Resolver Precision + Cache Maintenance UX

Date: 2026-06-18
Status: Active

## Summary

Improve Function Graph resolver precision for difficult C++ structures and make cache maintenance practical through the existing management surface. All output remains structural-only: no behavior claims, no compiler-level overload resolution, and no dynamic dispatch certainty.

## Slices

1. **P38: Preflight + Workplan Hygiene**
   - Close the Tree-sitter Function Graph Adapter workplan as completed after P33-P37 and PR #9.
   - Create this active workplan.
   - Record baseline owners, test matrix, and SmartFTP real-project sample expectations.

2. **P39: Overload + Template Candidate Ranking**
   - Bump resolver version to `cpp-function-graph-resolver-v0.4`.
   - Normalize template call names such as `Make<T>` to `Make` for candidate lookup.
   - Keep arity as a ranking hint only; preserve ambiguity when multiple plausible candidates remain.

3. **P40: Auto-Type + Local Initializer Hints**
   - Preserve local initializer call metadata from parser extracts.
   - Use project-local factory return signatures as type hints for simple `auto` locals.
   - Never infer external API types semantically.

4. **P41: Nested / Inherited Member Candidate Context**
   - Add compact nested type and simple base type candidates to `FunctionVisibilityContext` from existing index symbols.
   - Rank member candidates against current class, nested type context, and indexed base type context.
   - Do not claim C++ inheritance, overload, or virtual dispatch certainty.

5. **P42: Operator Call Structural Edges**
   - Record structural operator call syntax for `operator()` and `operator[]`.
   - Resolve only to project-local operator candidates when visible; otherwise keep unresolved/external status behavior.
   - Do not make side-effect or runtime-dispatch claims.

6. **P43: Cache Maintenance Admin UX**
   - Expose existing `cache_stats()` and `prune_cache_versions()` through management commands:
     - `function_graph_cache_stats`
     - `function_graph_cache_prune_versions`
   - Do not add new standard MCP tools.
   - Reject prune requests without explicit parser or resolver keep versions.

7. **P44: Real-Project Resolver/Cache Smoke**
   - Verify fixture coverage and, when available, run SmartFTP real-project samples against the existing indexed server.
   - Compare candidate quality and cache maintenance flow only, not behavior.

8. **P45: Cache Maintenance Stats Breakdown**
   - Expand `function_graph_cache_stats` with parser/resolver version breakdowns, cache timestamps, and edge counts per graph result.
   - Keep the data source in existing SQLite cache tables.

9. **P46: Cache Maintenance Dry-Run Prune**
   - Make `function_graph_cache_prune_versions` dry-run by default.
   - Add `keepCurrent=true` to keep the server's current parser cache version and current resolver-version family.
   - Require explicit keep versions or `keepCurrent=true` before any prune can run.

## Interfaces

- No new public standard MCP tools.
- Existing Function Graph tool schemas remain stable.
- Resolver version: `cpp-function-graph-resolver-v0.4`.
- Cache maintenance is visible through existing management/admin command handling only.
- Output remains `claimStrength=source_structure_allowed` and `behaviorClaimsAllowed=false`.

## Test Plan

- Unit tests:
  - overload/template ambiguity and ranking
  - auto initializer type hints
  - nested/base member candidate context
  - operator structural call edges
  - management command behavior for cache stats/prune
  - cache stats version breakdowns and dry-run prune behavior
- Smoke tests:
  - fixture index compute + cache-only + xrefs/neighborhood
  - SmartFTP real-project sample when the indexed server is available
- Required checks:
  - `python -m py_compile` on touched Python files
  - `python -m unittest discover -s tests -p "test_*.py"`
  - `git diff --check`

## Non-Goals

- No new public MCP tool surface.
- No hard Tree-sitter dependency.
- No compiler-level overload resolution.
- No dynamic dispatch certainty.
- No behavior claims.
- No unrelated UI or local helper file changes.
