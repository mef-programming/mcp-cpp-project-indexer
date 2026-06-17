# P06 Cache and SQLite Storage

Date: 2026-06-17
Workplan: `docs/workplans/on-demand-cpp-function-body-relation-graph.md`
Status: complete

## Implemented Phase

Step 6: Cache and SQLite Storage.

This phase adds AST extract cache keys, graph resolution cache keys, and SQLite-backed storage tables in the existing index database file.

## Changed Files

```text
src/indexer/cpp_function_graph_cache.py
src/indexer/cpp_function_graph_storage.py
tests/test_cpp_function_graph_storage.py
docs/work/README.md
docs/work/P06-cache-sqlite-storage.md
docs/workplans/on-demand-cpp-function-body-relation-graph.md
```

## Runtime Owner / Module

Cache keys:

```text
src/indexer/cpp_function_graph_cache.py
```

Storage:

```text
src/indexer/cpp_function_graph_storage.py
```

The storage helper uses `cpp_index_sqlite.sqlite_index_path(index_root)`, so graph data is stored next to the existing lookup tables in `index.sqlite`.

## Runtime Path

```text
FunctionGraphStorage.from_index_root(index_root)
  -> existing index.sqlite path
  -> initialize function graph tables if missing

store_ast_extract/load_ast_extract
  -> function_ast_extract_cache

store_graph_result/load_graph_result
  -> function_graph_cache
  -> function_graph_edges

list_edges_from/list_edges_to
  -> persisted edge rows for later xref tools
```

## Cache Contract

AST extract cache key:

```text
function_symbol_id
function_body_fingerprint
parser_id
parser_version
extractor_version
```

Graph resolution cache key:

```text
function_symbol_id
function_body_fingerprint
file_fingerprint
symbol_index_fingerprint
module_visibility_fingerprint
parser_id
parser_version
resolver_version
```

Changing module visibility, symbol index, parser, or resolver fingerprints causes a cache miss.

## Artifacts / Storage Evidence

SQLite tables:

```text
function_ast_extract_cache
function_graph_cache
function_graph_edges
```

No separate database root is introduced.

## Decision Authority / Governance

No MCP tool or server authority is added.

Persisted graph edges retain `claim_strength` and `behavior_claims_allowed`.

## Tests Run

```text
python -m unittest tests.test_cpp_function_graph_source tests.test_cpp_function_graph_extract tests.test_cpp_function_graph_visibility tests.test_cpp_function_graph_resolver tests.test_cpp_function_graph_storage
python -m py_compile src/indexer/cpp_function_graph_model.py src/indexer/cpp_function_graph_service.py src/indexer/cpp_function_graph_parser.py src/indexer/cpp_function_graph_extract.py src/indexer/cpp_function_graph_tree_sitter.py src/indexer/cpp_function_graph_visibility.py src/indexer/cpp_function_graph_resolver.py src/indexer/cpp_function_graph_cache.py src/indexer/cpp_function_graph_storage.py tests/test_cpp_function_graph_source.py tests/test_cpp_function_graph_extract.py tests/test_cpp_function_graph_visibility.py tests/test_cpp_function_graph_resolver.py tests/test_cpp_function_graph_storage.py
git diff --check
```

## Non-Goals Respected

```text
No MCP tool exposed.
No server dispatch changed.
No separate database root.
No second index lifecycle.
No behavior claims from graph data.
No external API resolution.
```

## Follow-Up Work

Next slice: Step 7 MCP Tool `get_function_body_graph`.
