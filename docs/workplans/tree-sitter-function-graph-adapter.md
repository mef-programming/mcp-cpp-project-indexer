# Tree-sitter Function Graph Adapter

Date: 2026-06-18
Status: Active

## Summary

Integrate Tree-sitter as a real optional C++ AST adapter for Function Graph extraction. Lightweight remains the default parser and fallback. Tree-sitter is used only through explicit parser injection or tests, not automatically and not through a new public MCP tool.

## Slices

1. **P33: Integration Preflight**
   - Merge PR #8, synchronize local `main`, and start this work from a fresh branch.
   - Close the previous runtime-quality workplan as completed.

2. **P34: Optional Dependency Packaging**
   - Document optional `tree-sitter` and `tree-sitter-cpp` packages without changing normal install behavior.
   - Keep dependency status lazy and import-side-effect free.

3. **P35: Real AST Extractor**
   - Replace the parse-probe-plus-lightweight path with Tree-sitter AST traversal.
   - Extract calls, qualified calls, member calls, field/member access, local declarations, and control-flow markers into `FunctionAstExtract`.

4. **P36: Explicit Parser Selection Gate**
   - Keep `FunctionGraphSourceService` defaulting to `LightweightFunctionBodyParser`.
   - Use Tree-sitter only through explicit parser injection/test configuration.
   - Preserve cache separation through parser id/version/status fingerprints.

5. **P37: Parity And Accuracy Tests**
   - Skip Tree-sitter tests when optional packages are absent.
   - Run active parity/accuracy tests when packages are present.
   - Keep public Function Graph API stable and structural-only.

## Interfaces

- No new public MCP tools.
- No change to `get_function_body_graph`, xrefs, or neighborhood schemas.
- Internal parser output remains `FunctionAstExtract`.
- Function Graph output remains `claimStrength=source_structure_allowed` and `behaviorClaimsAllowed=false`.

## Tests

- `python -m py_compile` for touched Python files.
- Targeted parser and service tests.
- `python -m unittest discover -s tests -p "test_*.py"`.
- `git diff --check`.

## Non-Goals

- No hard Tree-sitter dependency.
- No automatic Tree-sitter activation.
- No second parser loop.
- No behavior claims.
