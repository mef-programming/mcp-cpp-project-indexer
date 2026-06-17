# P04 Visibility Context

Date: 2026-06-17
Workplan: `docs/workplans/on-demand-cpp-function-body-relation-graph.md`
Status: complete

## Implemented Phase

Step 4: Visibility Context.

This phase builds a function-local visibility context from existing project index data. It does not resolve calls yet.

## Changed Files

```text
src/indexer/cpp_function_graph_model.py
src/indexer/cpp_function_graph_visibility.py
tests/test_cpp_function_graph_visibility.py
docs/work/README.md
docs/work/P04-visibility-context.md
docs/workplans/on-demand-cpp-function-body-relation-graph.md
```

## Runtime Owner / Module

```text
src/indexer/cpp_function_graph_visibility.py
```

## Runtime Path

```text
build_function_visibility_context(index, source, ast_extract)
  -> load current function symbol
  -> collect same-file symbols and data
  -> derive current namespace and current class
  -> find current class symbol
  -> collect module membership and visible module symbols
  -> collect member data for current class
  -> carry local declarations from raw AST extract
  -> return FunctionVisibilityContext
```

## Input / Output Contract

Input:

```text
loaded index object
FunctionSourceSlice
optional FunctionAstExtract
```

Output:

```text
FunctionVisibilityContext
  file id/path
  function symbol id
  current namespace
  current class symbol/name
  imported module names
  visible module symbols
  same-file symbols
  same-file data
  member data
  local declarations
  empty using declaration/directive and namespace alias candidates for later phases
```

## Artifacts / Storage Evidence

No durable storage is added in this phase.

## Decision Authority / Governance

No MCP tool or runtime authority is added.

The visibility context is metadata for later structural resolution. It is not behavior evidence.

## Tests Run

```text
python -m unittest tests.test_cpp_function_graph_source tests.test_cpp_function_graph_extract tests.test_cpp_function_graph_visibility
python -m py_compile src/indexer/cpp_function_graph_model.py src/indexer/cpp_function_graph_service.py src/indexer/cpp_function_graph_parser.py src/indexer/cpp_function_graph_extract.py src/indexer/cpp_function_graph_tree_sitter.py src/indexer/cpp_function_graph_visibility.py tests/test_cpp_function_graph_source.py tests/test_cpp_function_graph_extract.py tests/test_cpp_function_graph_visibility.py
git diff --check
```

## Non-Goals Respected

```text
No resolver added.
No MCP tool exposed.
No server dispatch changed.
No cache or SQLite schema added.
No behavior claims from graph data.
No external API resolution.
No full C++ visibility semantics.
```

## Follow-Up Work

Next slice: Step 5 Project-Only Resolver v0.1.
