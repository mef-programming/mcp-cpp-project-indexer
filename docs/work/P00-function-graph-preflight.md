# P00 Function Graph Preflight

Date: 2026-06-17
Workplan: `docs/workplans/on-demand-cpp-function-body-relation-graph.md`
Status: complete

## Implemented Phase

Step 0: Repo and Contract Preflight.

This phase documents ownership, target paths, dependency stance, test strategy, and non-goals before any runtime implementation begins.

## Changed Files

```text
docs/workplans/on-demand-cpp-function-body-relation-graph.md
docs/work/README.md
docs/work/P00-function-graph-preflight.md
```

No production runtime code changed in this phase.

## Runtime Owner / Module

Indexer owner:

```text
src/indexer/cpp_project_index.py
  loaded index model, symbol lookup, read_symbol, read_range, file structure, and source-range behavior

src/indexer/cpp_index_sqlite.py
  existing SQLite index path, schema initialization, symbol/data tables, and lookup indexes
```

Server owner:

```text
src/server/code_index_mcp_server.py
  tool_definitions(...)
  CodeIndexTools methods
  McpServer.tool_handlers
```

Planned function graph runtime ownership:

```text
src/indexer/cpp_function_graph_*.py
  extraction, parser adapter, visibility context, resolver, cache, storage, and service

src/server/code_index_mcp_server.py
  MCP schema, validation, response packing, and dispatch only
```

## Input / Output Contract

Input:

```text
symbolId for an indexed callable symbol
mode: cache_only | compute_if_missing | refresh
includeControlFlow/includeDataAccess/includeExternal flags
maxEdges
```

Output:

```text
schema id
status and cache state
symbol metadata
parser/resolver metadata
function/file/index/module/graph fingerprints
structural edges
claimStrength=source_structure_allowed
behaviorClaimsAllowed=false
```

The function graph must never be behavior evidence. Behavior claims still require `read_symbol` or `read_range`.

## Dependency Decision

Current repo inspection found no existing Tree-sitter or pytest dependency.

Decision for this preflight:

```text
Do not add parser dependencies in Step 0.
Keep parser usage behind FunctionBodyParser.
Step 3 must either add a pinned Tree-sitter dependency deliberately or use a test fixture parser until dependency policy is decided.
```

## Test Strategy

Minimum checks for every implementation slice:

```text
python -m py_compile <touched Python files>
targeted unit or smoke test for the slice
git diff --check
```

Planned fixture coverage:

```text
function source extraction from indexed range
unqualified call
qualified call
member call
member assignment write
local declaration
same-class method resolution
same-file static resolution
ambiguous overload
external Win32/STL call
cache hit and refresh behavior
```

If a test tree is introduced, use a top-level `tests/` folder rather than placing tests under `src/indexer`.

## Artifacts / Storage Evidence

No runtime storage artifacts are created in Step 0.

Future storage evidence must come from SQLite graph cache tables next to the existing index database, not from a separate database root or second index lifecycle.

## Non-Goals Respected

```text
No second provider loop.
No second tool loop.
No runtime MCP tool added in preflight.
No parser dependency added in preflight.
No full C++ compiler semantics.
No external API resolution.
No behavior claims from graph data.
No new top-level source package.
```

## Follow-Up Work

Next slice: Step 1 Function Source Extraction.

Expected first implementation files:

```text
src/indexer/cpp_function_graph_model.py
src/indexer/cpp_function_graph_service.py
tests/test_cpp_function_graph_source.py
```
