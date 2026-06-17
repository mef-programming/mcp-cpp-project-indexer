# P11 Data and Control-Flow Edges

Date: 2026-06-17
Workplan: `docs/workplans/completed/function-graph-edge-expansion.md`
Status: complete

## Implemented Phase

Function Graph Edge Expansion Step 1: Data and Control-Flow Edges.

This slice exposes already-extracted raw member/data accesses and control-flow markers as structural graph edges.

## Changed Files

```text
src/indexer/cpp_function_graph_resolver.py
src/indexer/cpp_function_graph_service.py
tests/test_cpp_function_graph_resolver.py
docs/work/README.md
docs/work/P11-data-control-flow-edges.md
docs/workplans/README.md
docs/workplans/function-graph-edge-expansion.md
```

## Runtime Owner / Module

Resolver:

```text
src/indexer/cpp_function_graph_resolver.py
```

Service/cache option wiring:

```text
src/indexer/cpp_function_graph_service.py
```

## Runtime Path

```text
get_function_body_graph
  -> FunctionGraphSourceService
  -> resolve_function_graph_edges(
       includeDataAccess,
       includeControlFlow
     )
  -> calls + optional data access edges + optional control-flow marker edges
```

## Edge Contract

Data access edges:

```text
reads_data_candidate
writes_data_candidate
```

Control-flow edges:

```text
control_flow_marker
```

All emitted edges retain:

```text
claimStrength=source_structure_allowed
behaviorClaimsAllowed=false
```

## Cache Contract

Graph cache lookup now includes the relevant graph options in the resolver cache-version string:

```text
includeControlFlow
includeDataAccess
includeExternal
maxEdges
```

This prevents cache reuse across incompatible graph-output shapes without changing the SQLite schema.

## Tests Run

```text
python -m unittest discover -s tests -p "test_*.py"
python -m py_compile src/indexer/cpp_function_graph_model.py src/indexer/cpp_function_graph_service.py src/indexer/cpp_function_graph_parser.py src/indexer/cpp_function_graph_extract.py src/indexer/cpp_function_graph_tree_sitter.py src/indexer/cpp_function_graph_visibility.py src/indexer/cpp_function_graph_resolver.py src/indexer/cpp_function_graph_cache.py src/indexer/cpp_function_graph_storage.py src/server/code_index_mcp_server.py tests/test_cpp_function_graph_source.py tests/test_cpp_function_graph_extract.py tests/test_cpp_function_graph_visibility.py tests/test_cpp_function_graph_resolver.py tests/test_cpp_function_graph_storage.py tests/test_cpp_function_graph_mcp_schema.py
temporary real-index smoke with CodeIndexTools.get_function_body_graph(includeDataAccess/includeControlFlow)
```

Result:

```text
Ran 26 tests - OK
py_compile - OK
real-index smoke - OK
```

## Non-Goals Respected

```text
No Tree-sitter dependency change.
No new MCP tool.
No vector sidecar.
No full CFG.
No data-flow semantics.
No behavior claims from graph data.
No second provider loop.
No second tool loop.
```
