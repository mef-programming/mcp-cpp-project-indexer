# P21 Resolver Candidate Quality

Implemented phase: Function Graph Accuracy and Maintenance Slice P21.

## Changed

- Member-call local type hints now match namespaced member containers by tail when the local type is unqualified.
- Qualified data accesses add `qualified_data_name` basis when they match indexed project data.
- Existing ambiguity behavior is preserved.

## Runtime Ownership

`src/indexer/cpp_function_graph_resolver.py` owns candidate scoring and structural edge resolution.

## Verification

- `tests/test_cpp_function_graph_resolver.py` covers local type tail matching and qualified data basis.

## Non-Goals Respected

No dynamic dispatch, alias certainty, side-effect, or behavior claims.
