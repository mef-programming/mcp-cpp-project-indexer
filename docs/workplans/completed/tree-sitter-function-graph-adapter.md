# Tree-sitter Function Graph Adapter

Date: 2026-06-18
Status: Completed

## Summary

Tree-sitter is integrated as a real optional C++ AST adapter for Function Graph extraction. Lightweight remains the default parser and fallback. Tree-sitter is used only through explicit parser injection or tests, not automatically and not through a new public MCP tool.

## Completed Slices

1. **P33: Integration Preflight**
   - PR #8 was merged, local baseline was synchronized, and this Tree-sitter workplan became the active follow-up.

2. **P34: Optional Dependency Packaging**
   - Optional `tree-sitter` and `tree-sitter-cpp` packages are documented without changing normal install behavior.
   - Dependency status remains lazy and import-side-effect free.

3. **P35: Real AST Extractor**
   - Tree-sitter AST traversal extracts calls, qualified calls, member calls, field/member access, local declarations, and control-flow markers into `FunctionAstExtract`.

4. **P36: Explicit Parser Selection Gate**
   - `FunctionGraphSourceService` still defaults to `LightweightFunctionBodyParser`.
   - Tree-sitter is selected only through explicit parser injection/test configuration.
   - Cache separation uses parser id, parser version, and parser status fingerprints.

5. **P37: Parity And Accuracy Tests**
   - Tree-sitter tests skip cleanly when optional packages are absent.
   - Active parity/accuracy tests run when packages are present.
   - Public Function Graph API remains stable and structural-only.

## Interfaces

- No new public MCP tools.
- No change to `get_function_body_graph`, xrefs, or neighborhood schemas.
- Internal parser output remains `FunctionAstExtract`.
- Function Graph output remains `claimStrength=source_structure_allowed` and `behaviorClaimsAllowed=false`.

## Evidence

- Work logs: `docs/work/P33-tree-sitter-integration-preflight.md` through `docs/work/P37-tree-sitter-parity-and-accuracy.md`.
- Merge evidence: PR #9 is merged into `origin/main`.

## Non-Goals Respected

- No hard Tree-sitter dependency.
- No automatic Tree-sitter activation.
- No second parser loop.
- No behavior claims.
