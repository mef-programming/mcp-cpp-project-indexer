# P02 Data Contracts and Empty Graph

Date: 2026-06-17
Workplan: `docs/workplans/on-demand-cpp-function-body-relation-graph.md`
Status: complete

## Implemented Phase

Step 2: Data Contracts and Empty Service Shell.

This phase extends the Step 1 source extraction slice with stable graph request/result contracts and a deterministic empty graph response.

## Changed Files

```text
src/indexer/cpp_function_graph_model.py
src/indexer/cpp_function_graph_service.py
tests/test_cpp_function_graph_source.py
docs/work/README.md
docs/work/P02-data-contracts-empty-graph.md
docs/workplans/on-demand-cpp-function-body-relation-graph.md
```

## Runtime Owner / Module

Contracts:

```text
src/indexer/cpp_function_graph_model.py
```

Service:

```text
src/indexer/cpp_function_graph_service.py
```

## Runtime Path

```text
FunctionGraphSourceService.build_empty_graph_result(symbolId | FunctionGraphRequest)
  -> extract indexed function source
  -> create FunctionGraphFingerprints
  -> return FunctionGraphResult with zero edges
```

## Input / Output Contract

Input:

```text
FunctionGraphRequest
  symbol_id
  mode
  include_control_flow
  include_data_access
  include_external
  max_edges
```

Output:

```text
FunctionGraphResult
  schema=cpp.function_body_graph.v0.1
  status=computed
  from_cache=false
  symbol metadata
  file/range metadata
  parser/resolver metadata unset
  claimStrength=source_structure_allowed
  behaviorClaimsAllowed=false
  function body and graph fingerprints
  edges=()
```

## Artifacts / Storage Evidence

No durable storage is added in this phase.

The empty graph fingerprint is deterministic and derived from schema, symbol id, source fingerprint, empty edges, and unset parser/resolver metadata.

## Decision Authority / Governance

No MCP tool or server authority is added.

The result explicitly preserves `source_structure_allowed` and `behaviorClaimsAllowed=false`.

## Tests Run

```text
python -m unittest tests.test_cpp_function_graph_source
python -m py_compile src/indexer/cpp_function_graph_model.py src/indexer/cpp_function_graph_service.py tests/test_cpp_function_graph_source.py
git diff --check
```

## Non-Goals Respected

```text
No parser added.
No Tree-sitter dependency added.
No resolver added.
No MCP tool exposed.
No server dispatch changed.
No cache or SQLite schema added.
No behavior claims from graph data.
```

## Follow-Up Work

Next slice: Step 3 Parser Adapter and Raw Extraction.
