# P08 Xrefs and Neighborhood

Date: 2026-06-17
Workplan: `docs/workplans/on-demand-cpp-function-body-relation-graph.md`
Status: complete

## Implemented Phase

Step 8: Xrefs and Neighborhood.

This phase exposes persisted function graph edges in both directions and adds a compact symbol neighborhood view. The tools read stored edges only; they do not compute missing graphs.

## Changed Files

```text
src/indexer/cpp_function_graph_storage.py
src/indexer/cpp_function_graph_service.py
src/server/code_index_mcp_server.py
tests/test_cpp_function_graph_source.py
tests/test_cpp_function_graph_storage.py
tests/test_cpp_function_graph_mcp_schema.py
docs/work/README.md
docs/work/P08-xrefs-neighborhood.md
docs/workplans/on-demand-cpp-function-body-relation-graph.md
```

## Runtime Owner / Module

Persisted edge lookup:

```text
src/indexer/cpp_function_graph_storage.py
```

Xref and neighborhood service API:

```text
src/indexer/cpp_function_graph_service.py
```

MCP schema, validation, and dispatch:

```text
src/server/code_index_mcp_server.py
```

## Runtime Path

```text
get_call_xrefs_from(symbolId)
  -> read persisted function_graph_edges by from_symbol_id

get_call_xrefs_to(symbolId)
  -> read persisted function_graph_edges by to_symbol_id

get_symbol_neighborhood(symbolId)
  -> read persisted incoming and outgoing edges
  -> return target, callers, callees, and compact edge sets
```

## Public API

New MCP tools:

```text
get_call_xrefs_from
get_call_xrefs_to
get_symbol_neighborhood
```

All three tools return structural navigation data only:

```text
claimStrength=source_structure_allowed
behaviorClaimsAllowed=false
```

## Artifacts / Storage Evidence

The tools read from the Step 6/7 SQLite storage:

```text
function_graph_edges
```

When a new graph result is stored for a function, outgoing rows for that function are replaced so xref tools do not surface stale outgoing edges from older graph fingerprints.

## Decision Authority / Governance

No provider loop, tool loop, or governance path is introduced.

The tools expose persisted structural relationships only. They do not parse source, resolve external APIs semantically, or support behavior claims.

## Tests Run

```text
python -m unittest tests.test_cpp_function_graph_source tests.test_cpp_function_graph_extract tests.test_cpp_function_graph_visibility tests.test_cpp_function_graph_resolver tests.test_cpp_function_graph_storage tests.test_cpp_function_graph_mcp_schema
python -m py_compile src/indexer/cpp_function_graph_model.py src/indexer/cpp_function_graph_service.py src/indexer/cpp_function_graph_parser.py src/indexer/cpp_function_graph_extract.py src/indexer/cpp_function_graph_tree_sitter.py src/indexer/cpp_function_graph_visibility.py src/indexer/cpp_function_graph_resolver.py src/indexer/cpp_function_graph_cache.py src/indexer/cpp_function_graph_storage.py src/server/code_index_mcp_server.py tests/test_cpp_function_graph_source.py tests/test_cpp_function_graph_extract.py tests/test_cpp_function_graph_visibility.py tests/test_cpp_function_graph_resolver.py tests/test_cpp_function_graph_storage.py tests/test_cpp_function_graph_mcp_schema.py
git diff --check
```

Result:

```text
Ran 20 tests - OK
py_compile - OK
git diff --check - OK
```

## Non-Goals Respected

```text
No missing graph computation in xref tools.
No recursive graph expansion.
No behavior claims from graph data.
No external API semantic resolution.
No second provider loop.
No second tool loop.
No broad helper library or new top-level package.
```

## Follow-Up Work

Next slice: Step 9 Resolution Improvements.
