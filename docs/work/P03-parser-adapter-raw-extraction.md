# P03 Parser Adapter and Raw Extraction

Date: 2026-06-17
Workplan: `docs/workplans/on-demand-cpp-function-body-relation-graph.md`
Status: complete

## Implemented Phase

Step 3: Parser Adapter and Raw Extraction.

This phase adds a parser protocol, a dependency-free lightweight raw extractor, and a Tree-sitter adapter boundary that remains unavailable until a real dependency is deliberately added.

## Changed Files

```text
src/indexer/cpp_function_graph_parser.py
src/indexer/cpp_function_graph_extract.py
src/indexer/cpp_function_graph_tree_sitter.py
tests/test_cpp_function_graph_extract.py
docs/work/README.md
docs/work/P03-parser-adapter-raw-extraction.md
docs/workplans/on-demand-cpp-function-body-relation-graph.md
```

## Runtime Owner / Module

Parser protocol:

```text
src/indexer/cpp_function_graph_parser.py
```

Raw extraction:

```text
src/indexer/cpp_function_graph_extract.py
```

Tree-sitter boundary:

```text
src/indexer/cpp_function_graph_tree_sitter.py
```

## Runtime Path

```text
LightweightFunctionBodyParser.parse_function(...)
  -> extract_raw_function_ast(...)
  -> FunctionAstExtract(calls, member_accesses, local_declarations, control_flow)
```

The extractor maps each occurrence back to original file coordinates using `base_line` and `base_byte`.

## Extracted Facts

```text
calls:
  callee text, callKind, argumentCount, line, column, byte

member_accesses:
  text, read/write candidate, line, column, byte

local_declarations:
  name, typeText, line, column, byte

control_flow:
  marker, line, column, byte
```

These are structural syntax facts only. They do not resolve project symbols or support behavior claims.

## Dependency Decision

No Tree-sitter dependency was added in this phase.

`TreeSitterCppFunctionBodyParser` raises `TreeSitterUnavailableError` at construction time. This preserves the adapter seam without pretending Tree-sitter is configured.

## Tests Run

```text
python -m unittest tests.test_cpp_function_graph_source tests.test_cpp_function_graph_extract
python -m py_compile src/indexer/cpp_function_graph_model.py src/indexer/cpp_function_graph_service.py src/indexer/cpp_function_graph_parser.py src/indexer/cpp_function_graph_extract.py src/indexer/cpp_function_graph_tree_sitter.py tests/test_cpp_function_graph_source.py tests/test_cpp_function_graph_extract.py
git diff --check
```

## Non-Goals Respected

```text
No Tree-sitter dependency added.
No resolver added.
No MCP tool exposed.
No server dispatch changed.
No cache or SQLite schema added.
No behavior claims from graph data.
No external API resolution.
```

## Follow-Up Work

Next slice: Step 4 Visibility Context.
