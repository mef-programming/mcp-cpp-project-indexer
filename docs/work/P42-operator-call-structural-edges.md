# P42 Operator Call Structural Edges

Date: 2026-06-18
Status: Completed

## Implemented Phase

Structural operator call support for resolver candidates.

## Changed Files

- `src/indexer/cpp_function_graph_extract.py`
- `src/indexer/cpp_function_graph_tree_sitter.py`
- `src/indexer/cpp_function_graph_resolver.py`
- `tests/test_cpp_function_graph_extract.py`
- `tests/test_cpp_function_graph_resolver.py`

## Runtime Owner

`src/indexer` owns parser extraction and resolver edge construction.

## What Changed

- Lightweight and Tree-sitter extracts can emit `operator[]` structural call candidates.
- Resolver treats local object calls as possible `operator()` structural calls when the object has a local type hint.
- Candidate basis includes `operator_call_syntax`.

## Evidence

Operator edges remain normal call edges with structural basis. Unresolved operator syntax stays unresolved or external according to existing rules.

## Tests

- Parser fixture verifies `operator[]` extraction.
- Resolver fixture verifies `operator()` and `operator[]` project-local candidates.

## Non-Goals Respected

- No side-effect claims.
- No runtime dispatch claims.
