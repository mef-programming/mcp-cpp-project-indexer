# Function Graph Runtime Quality / Parser Adapter vNext

Date: 2026-06-18
Status: Active

## Summary

Improve Function Graph runtime quality, parser reliability, and optional adapter readiness without adding public MCP tools, behavior claims, or a hard Tree-sitter dependency.

Runtime ownership stays in `src/indexer`. MCP exposure stays in `src/server/code_index_mcp_server.py` and remains transport/dispatch only.

## Slices

1. **P28: Workplan Setup + Accuracy Baseline**
   - Define realistic C++ body coverage for templates, lambdas, nested calls, macro noise, constructors, overload-looking calls, member access, and data access.
   - Record expected structural outcomes before widening runtime behavior.

2. **P29: Parser Adapter Contract vNext**
   - Add parser status/capability metadata to the internal `FunctionBodyParser` contract.
   - Keep exactly one parser selected per graph compute.
   - Keep Lightweight as the default parser.

3. **P30: Tree-sitter Optional Adapter Parity Spike**
   - Keep Tree-sitter optional and gated.
   - If dependencies are absent, expose deterministic unavailable behavior.
   - If dependencies are present, prove a parse tree can be created and normalize extraction through the existing function-graph contract.

4. **P31: Runtime Accuracy Smoke Matrix**
   - Expand the real-index smoke fixture with multiple files, namespaces, `using` visibility, static helpers, external calls, cache-only reads, xrefs, and neighborhood reads.
   - Prove xrefs only over persisted computed graphs.

5. **P32: Resolver/Cache Guardrails**
   - Tie cache compatibility to parser status/capabilities as well as parser/resolver versions and graph options.
   - Keep resolver improvements bounded to stable structural signals.

## Interfaces

- No new public MCP tools.
- Existing Function Graph API response remains stable.
- Internal cache compatibility includes parser status/capability fingerprints.
- Output remains structural-only with `claimStrength=source_structure_allowed` and `behaviorClaimsAllowed=false`.

## Tests

- `python -m py_compile` for touched Python files.
- Targeted Function Graph unit tests for parser, source/service cache, storage, and MCP smoke.
- Full `python -m unittest discover -s tests -p "test_*.py"` before PR.
- `git diff --check`.

## Non-Goals

- No hard Tree-sitter install requirement.
- No second parser loop in the service.
- No new MCP tools.
- No external API semantics or behavior claims.
