# P07 MCP get_function_body_graph

Date: 2026-06-17
Workplan: `docs/workplans/on-demand-cpp-function-body-relation-graph.md`
Status: complete

## Implemented Phase

Step 7: MCP Tool `get_function_body_graph`.

This phase exposes the first on-demand function graph tool through the existing MCP server while keeping parsing, resolution, and cache ownership in `src/indexer`.

## Changed Files

```text
src/indexer/cpp_function_graph_service.py
src/server/code_index_mcp_server.py
tests/test_cpp_function_graph_source.py
tests/test_cpp_function_graph_mcp_schema.py
docs/work/README.md
docs/work/P07-mcp-get-function-body-graph.md
docs/workplans/on-demand-cpp-function-body-relation-graph.md
```

## Runtime Owner / Module

MCP schema, argument validation, and dispatch:

```text
src/server/code_index_mcp_server.py
```

Function graph runtime:

```text
src/indexer/cpp_function_graph_service.py
src/indexer/cpp_function_graph_extract.py
src/indexer/cpp_function_graph_visibility.py
src/indexer/cpp_function_graph_resolver.py
src/indexer/cpp_function_graph_storage.py
```

## Runtime Path

```text
get_function_body_graph(symbolId, mode)
  -> CodeIndexTools validates MCP args
  -> CodeIndexTools computes file/symbol/module visibility fingerprints
  -> FunctionGraphSourceService extracts indexed function text
  -> AST extract cache lookup
  -> lightweight parser on cache miss
  -> visibility context from existing index data
  -> project-only resolver
  -> graph cache store/load in existing index.sqlite
  -> compact MCP JSON response
```

## Public API

First MCP v1 tool:

```text
get_function_body_graph
```

Modes:

```text
cache_only
compute_if_missing
refresh
```

Output includes:

```text
schema
status
fromCache
symbolId
functionName
qualifiedName
parser/resolver metadata
fingerprints
edges
claimStrength=source_structure_allowed
behaviorClaimsAllowed=false
```

## Artifacts / Storage Evidence

Graph results are backed by:

```text
functionBody fingerprint
file fingerprint
symbolIndex fingerprint
moduleVisibility fingerprint
graph fingerprint
```

Computed graph results are stored in the existing `index.sqlite` function graph tables from Step 6.

## Decision Authority / Governance

No provider loop, tool loop, or governance path is introduced.

The tool is structural only:

```text
claimStrength=source_structure_allowed
behaviorClaimsAllowed=false
```

Behavior claims still require source-read tools such as `read_symbol` or `read_range`.

## Tests Run

```text
python -m unittest tests.test_cpp_function_graph_source tests.test_cpp_function_graph_extract tests.test_cpp_function_graph_visibility tests.test_cpp_function_graph_resolver tests.test_cpp_function_graph_storage tests.test_cpp_function_graph_mcp_schema
python -m py_compile src/indexer/cpp_function_graph_model.py src/indexer/cpp_function_graph_service.py src/indexer/cpp_function_graph_parser.py src/indexer/cpp_function_graph_extract.py src/indexer/cpp_function_graph_tree_sitter.py src/indexer/cpp_function_graph_visibility.py src/indexer/cpp_function_graph_resolver.py src/indexer/cpp_function_graph_cache.py src/indexer/cpp_function_graph_storage.py src/server/code_index_mcp_server.py tests/test_cpp_function_graph_source.py tests/test_cpp_function_graph_extract.py tests/test_cpp_function_graph_visibility.py tests/test_cpp_function_graph_resolver.py tests/test_cpp_function_graph_storage.py tests/test_cpp_function_graph_mcp_schema.py
git diff --check
```

Result:

```text
Ran 18 tests - OK
py_compile - OK
git diff --check - OK
```

## Non-Goals Respected

```text
No get_call_xrefs_from tool added.
No get_call_xrefs_to tool added.
No get_symbol_neighborhood tool added.
No parser or resolver logic added to the MCP server.
No external API semantic resolution.
No behavior claims from graph data.
No second provider loop.
No second tool loop.
No broad helper library or new top-level package.
```

## Follow-Up Work

Next slice: Step 8 Xrefs and Neighborhood, using persisted edges from Step 6/7.
