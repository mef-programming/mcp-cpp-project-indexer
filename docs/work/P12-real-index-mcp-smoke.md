# P12 Real Index MCP Smoke

Date: 2026-06-17
Workplan: `docs/workplans/completed/function-graph-edge-expansion.md`
Status: complete

## Implemented Phase

Follow-up hardening: persist the temporary real-index MCP smoke as a regression test.

## Changed Files

```text
tests/test_cpp_function_graph_mcp_smoke.py
docs/work/README.md
docs/work/P12-real-index-mcp-smoke.md
```

## Runtime Owner / Module

No production runtime code changed.

The smoke covers:

```text
build_project_index.py
CodeIndexTools.find_symbol
CodeIndexTools.get_function_body_graph
CodeIndexTools.get_call_xrefs_from
```

## Smoke Contract

The test builds a temporary C++ project and verifies:

```text
definition symbol is selected instead of method declaration
get_function_body_graph computes a structural graph
cache_only returns the same graph fingerprint from cache
xrefs read persisted outgoing edges
data/control-flow edges are emitted by default
data/control-flow edges are removed by includeDataAccess=false and includeControlFlow=false
behaviorClaimsAllowed=false
```

## Tests Run

```text
python -m unittest tests.test_cpp_function_graph_mcp_smoke
python -m unittest discover -s tests -p "test_*.py"
```

Result:

```text
smoke test - OK
Ran 27 tests - OK
```

## Non-Goals Respected

```text
No production behavior change.
No new MCP tool.
No external service dependency.
No behavior claims from graph data.
```
