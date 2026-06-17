# P05 Project-Only Resolver

Date: 2026-06-17
Workplan: `docs/workplans/on-demand-cpp-function-body-relation-graph.md`
Status: complete

## Implemented Phase

Step 5: Project-Only Resolver v0.1.

This phase resolves raw call occurrences against the function-local visibility context. It does not persist edges or expose MCP tools.

## Changed Files

```text
src/indexer/cpp_function_graph_model.py
src/indexer/cpp_function_graph_resolver.py
tests/test_cpp_function_graph_resolver.py
docs/work/README.md
docs/work/P05-project-only-resolver.md
docs/workplans/on-demand-cpp-function-body-relation-graph.md
```

## Runtime Owner / Module

```text
src/indexer/cpp_function_graph_resolver.py
```

## Runtime Path

```text
resolve_function_graph_edges(ast_extract, visibility)
  -> iterate raw call occurrences
  -> resolve qualified calls by exact qualified name
  -> resolve this->member calls against current class
  -> resolve unqualified calls against current class, same namespace/file, and module-visible symbols
  -> return FunctionGraphEdge entries
```

## Resolution Contract

```text
single exact qualified/this match:
  resolution_status=exact
  edge_kind=calls_resolved

single unqualified project-local match:
  resolution_status=probable
  edge_kind=calls_candidate

multiple project-local matches:
  resolution_status=ambiguous
  edge_kind=calls_ambiguous
  candidates populated

member call without object type:
  resolution_status=unresolved
  edge_kind=calls_unresolved

no project candidate:
  resolution_status=external
  edge_kind=calls_external
```

## Artifacts / Storage Evidence

No durable storage is added in this phase.

Edges are returned in memory only.

## Decision Authority / Governance

No MCP tool or server authority is added.

All edges keep `claimStrength=source_structure_allowed` and `behaviorClaimsAllowed=false`.

## Tests Run

```text
python -m unittest tests.test_cpp_function_graph_source tests.test_cpp_function_graph_extract tests.test_cpp_function_graph_visibility tests.test_cpp_function_graph_resolver
python -m py_compile src/indexer/cpp_function_graph_model.py src/indexer/cpp_function_graph_service.py src/indexer/cpp_function_graph_parser.py src/indexer/cpp_function_graph_extract.py src/indexer/cpp_function_graph_tree_sitter.py src/indexer/cpp_function_graph_visibility.py src/indexer/cpp_function_graph_resolver.py tests/test_cpp_function_graph_source.py tests/test_cpp_function_graph_extract.py tests/test_cpp_function_graph_visibility.py tests/test_cpp_function_graph_resolver.py
git diff --check
```

## Non-Goals Respected

```text
No full overload resolution.
No template instantiation.
No ADL semantics.
No external API resolution.
No MCP tool exposed.
No server dispatch changed.
No cache or SQLite schema added.
No behavior claims from graph data.
```

## Follow-Up Work

Next slice: Step 6 Cache and SQLite Storage.
