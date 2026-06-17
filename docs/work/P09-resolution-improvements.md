# P09 Resolution Improvements

Date: 2026-06-17
Workplan: `docs/workplans/on-demand-cpp-function-body-relation-graph.md`
Status: complete

## Implemented Phase

Step 9: Resolution Improvements.

This phase improves project-local candidate quality for function graph resolution without claiming compiler-level C++ semantics.

## Changed Files

```text
src/indexer/cpp_function_graph_visibility.py
src/indexer/cpp_function_graph_resolver.py
tests/test_cpp_function_graph_visibility.py
tests/test_cpp_function_graph_resolver.py
docs/work/README.md
docs/work/P09-resolution-improvements.md
docs/workplans/on-demand-cpp-function-body-relation-graph.md
```

## Runtime Owner / Module

Visibility context:

```text
src/indexer/cpp_function_graph_visibility.py
```

Project-only resolver:

```text
src/indexer/cpp_function_graph_resolver.py
```

## Runtime Path

```text
FunctionGraphSourceService
  -> build_function_visibility_context
     -> optional using declarations/directives and namespace aliases from index metadata
  -> resolve_function_graph_edges
     -> namespace alias expansion for qualified calls
     -> using declaration / using namespace candidates for unqualified calls
     -> simple local type hint for member calls
     -> arity-based candidate scoring
```

## Resolution Contract

Added candidate-quality signals:

```text
using_declaration
using_namespace
namespace_alias
local_type_hint
arity_match
arity_mismatch
```

Ambiguous overloads remain ambiguous. Scores sort candidates; they do not create fake exact matches.

## Artifacts / Storage Evidence

Stored graph edges continue to carry:

```text
claimStrength=source_structure_allowed
behaviorClaimsAllowed=false
```

Candidate refs may now include score and basis fields when ambiguity requires ranking.

## Decision Authority / Governance

No MCP schema or dispatch change is added in this phase.

No provider loop, tool loop, or governance path is introduced.

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
No full overload resolution.
No ADL.
No template instantiation.
No external API semantic resolution.
No behavior claims from graph data.
No MCP tool expansion.
No second provider loop.
No second tool loop.
```

## Follow-Up Work

Initial workplan implementation slices are complete except optional future work such as vector sidecar or broader parser dependency decisions.
