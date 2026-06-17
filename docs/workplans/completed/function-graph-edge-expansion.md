# Workplan: Function Graph Edge Expansion

Date: 2026-06-17
Status: completed
Component: C++ MCP Project Indexer

## Goal

Extend the completed on-demand function-body graph with the raw structural edges that the parser already extracts:

```text
member/data access candidates
control-flow markers
```

This keeps the feature on-demand and structural only.

## Scope

Allowed owners:

```text
src/indexer/cpp_function_graph_resolver.py
src/indexer/cpp_function_graph_service.py
tests/test_cpp_function_graph_resolver.py
docs/work/
docs/workplans/
```

No MCP tool expansion is required because `get_function_body_graph` already exposes:

```text
includeDataAccess
includeControlFlow
```

## Non-Goals

```text
No Tree-sitter dependency change.
No vector sidecar.
No behavior claims.
No data-flow analysis.
No full CFG.
No compiler-level aliasing or write/read certainty.
No new MCP tools.
```

## Implementation Steps

### Step 1: Data and Control-Flow Edges

Status: complete.

Actions:

```text
Emit reads_data_candidate and writes_data_candidate edges from raw member accesses.
Emit control_flow_marker edges from raw control-flow markers.
Respect includeDataAccess and includeControlFlow request flags.
Keep claimStrength=source_structure_allowed and behaviorClaimsAllowed=false.
Separate graph cache entries by relevant graph options.
```

Observable result:

```text
Unit tests prove data/control-flow edges are emitted and can be disabled.
```

## Acceptance Criteria

```text
Function graph remains on-demand.
Normal indexing remains unchanged.
Existing MCP tool schema remains stable.
Data and control-flow edges are structural only.
Cache hits do not cross incompatible include flag combinations.
```
