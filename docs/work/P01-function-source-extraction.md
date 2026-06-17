# P01 Function Source Extraction

Date: 2026-06-17
Workplan: `docs/workplans/on-demand-cpp-function-body-relation-graph.md`
Status: complete

## Implemented Phase

Step 1: Function Source Extraction.

This phase adds the first runtime slice for the function graph workplan. It resolves an indexed callable symbol to its exact source text and returns source coordinates plus a stable fingerprint.

## Changed Files

```text
src/indexer/cpp_function_graph_model.py
src/indexer/cpp_function_graph_service.py
tests/test_cpp_function_graph_source.py
docs/work/README.md
docs/work/P01-function-source-extraction.md
docs/workplans/on-demand-cpp-function-body-relation-graph.md
```

## Runtime Owner / Module

Owner:

```text
src/indexer/cpp_function_graph_service.py
```

Inputs:

```text
project_root
loaded index object with symbol_by_id and file_by_id
symbolId for an indexed callable symbol
```

Outputs:

```text
FunctionSourceSlice
  symbol metadata
  fileId and relativePath
  start/end lines
  base line and byte offset
  raw source text
  functionBodyFingerprint
```

## Runtime Path

```text
FunctionGraphSourceService.extract_function_source(symbolId)
  -> look up symbol in loaded index
  -> reject missing or non-callable symbols
  -> resolve fileId through file_by_id
  -> read exact indexed source range from project_root / relativePath
  -> compute sha256 source fingerprint
  -> return FunctionSourceSlice
```

## Artifacts / Storage Evidence

No durable storage is added in this phase.

The returned `function_body_fingerprint` is the source evidence key for later AST extraction and cache phases.

## Decision Authority / Governance

Not applicable for this repository slice.

The implementation does not add an MCP tool and does not grant behavior authority. It only extracts indexed source text.

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
No MCP tool exposed.
No server dispatch changed.
No cache or SQLite schema added.
No behavior claims from graph data.
No external API resolution.
```

## Follow-Up Work

Next slice: Step 2 Data Contracts and Empty Service Shell.
