# P41 Nested Inherited Member Context

Date: 2026-06-18
Status: Completed

## Implemented Phase

Nested type and simple base type candidate context.

## Changed Files

- `src/indexer/cpp_function_graph_model.py`
- `src/indexer/cpp_function_graph_visibility.py`
- `src/indexer/cpp_function_graph_resolver.py`
- `tests/test_cpp_function_graph_visibility.py`
- `tests/test_cpp_function_graph_resolver.py`

## Runtime Owner

`src/indexer` owns visibility context and resolver candidate ranking.

## What Changed

- `FunctionVisibilityContext` now carries compact `nested_type_symbols` and `base_type_symbols`.
- Visibility context derives these from existing indexed type symbols and class signatures.
- Resolver ranks member candidates with `nested_type_context` and `base_class_context`.

## Evidence

The resolver can surface candidates from indexed nested/base type containers, but still marks them as structural candidates.

## Tests

- Visibility fixture verifies nested/base type symbols are included.
- Resolver fixture verifies nested/base candidate basis.

## Non-Goals Respected

- No C++ inheritance semantics guarantee.
- No virtual dispatch or dynamic dispatch certainty.
