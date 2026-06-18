# P40 Auto Type Local Initializer Hints

Date: 2026-06-18
Status: Completed

## Implemented Phase

Local `auto` initializer hints for project-local member candidate ranking.

## Changed Files

- `src/indexer/cpp_function_graph_extract.py`
- `src/indexer/cpp_function_graph_tree_sitter.py`
- `src/indexer/cpp_function_graph_resolver.py`
- `tests/test_cpp_function_graph_extract.py`
- `tests/test_cpp_function_graph_resolver.py`

## Runtime Owner

`src/indexer` owns parser extracts and resolver hints.

## What Changed

- Parser extracts now preserve simple local initializer text and `initializerCallee`.
- Resolver uses project-local factory return signatures as type hints for `auto` locals.
- Candidate basis includes `auto_initializer_call_hint` and `return_type_hint`.

## Evidence

Example structural pattern:

```text
auto client = MakeClient();
client.Run();
```

If `MakeClient` is project-local and its indexed signature returns `Client`, `client.Run` can rank methods on `Client` as candidates.

## Tests

- Parser fixture verifies `initializerCallee`.
- Resolver fixture verifies member candidate ranking from an `auto` initializer.

## Non-Goals Respected

- No external type semantics.
- No behavior claims.
