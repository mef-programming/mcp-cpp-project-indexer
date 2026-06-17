# P10 Optional Vector Sidecar Deferred

Date: 2026-06-17
Workplan: `docs/workplans/completed/on-demand-cpp-function-body-relation-graph.md`
Status: deferred

## Implemented Phase

Step 10: Optional Vector Sidecar.

No runtime implementation was added. The workplan explicitly defines this step as future work only.

## Changed Files

```text
docs/work/README.md
docs/work/P10-optional-vector-sidecar-deferred.md
docs/workplans/on-demand-cpp-function-body-relation-graph.md
docs/workplans/README.md
```

## Runtime Owner / Module

No runtime owner is introduced.

Future vector sidecar work, if accepted later, must remain outside behavior evidence:

```text
vector sidecar -> routing_hint_only
function graph -> source_structure_allowed
source reads -> source_behavior_allowed
```

## Runtime Path

No runtime path is added.

The current initial implementation remains:

```text
get_function_body_graph
  -> on-demand source extraction
  -> parser adapter
  -> project-only resolver
  -> graph cache

get_call_xrefs_from / get_call_xrefs_to / get_symbol_neighborhood
  -> persisted graph edges only
```

## Artifacts / Storage Evidence

No vector tables, embeddings, sidecar files, or warmup jobs are added.

Existing evidence remains the SQLite function graph cache and edge tables from Steps 6-8.

## Decision Authority / Governance

No provider loop, tool loop, governance path, or model/provider behavior is changed.

No vector output can be used as behavior evidence.

## Tests Run

```text
python -m unittest tests.test_cpp_function_graph_source tests.test_cpp_function_graph_extract tests.test_cpp_function_graph_visibility tests.test_cpp_function_graph_resolver tests.test_cpp_function_graph_storage tests.test_cpp_function_graph_mcp_schema
python -m py_compile src/indexer/cpp_function_graph_model.py src/indexer/cpp_function_graph_service.py src/indexer/cpp_function_graph_parser.py src/indexer/cpp_function_graph_extract.py src/indexer/cpp_function_graph_tree_sitter.py src/indexer/cpp_function_graph_visibility.py src/indexer/cpp_function_graph_resolver.py src/indexer/cpp_function_graph_cache.py src/indexer/cpp_function_graph_storage.py src/server/code_index_mcp_server.py tests/test_cpp_function_graph_source.py tests/test_cpp_function_graph_extract.py tests/test_cpp_function_graph_visibility.py tests/test_cpp_function_graph_resolver.py tests/test_cpp_function_graph_storage.py tests/test_cpp_function_graph_mcp_schema.py
git diff --check
```

Result:

```text
Ran 24 tests - OK
py_compile - OK
git diff --check - OK
```

## Non-Goals Respected

```text
No vector sidecar implementation.
No embedding dependency.
No background warmup job.
No new MCP tool.
No behavior claims from routing hints.
No second provider loop.
No second tool loop.
```

## Completion Note

The initial function-body graph workplan is complete after Steps 0-9 plus this explicit Step 10 deferral.
