# P39 Overload Template Ranking

Date: 2026-06-18
Status: Completed

## Implemented Phase

Resolver v0.4 overload/template ranking improvements.

## Changed Files

- `src/indexer/cpp_function_graph_resolver.py`
- `tests/test_cpp_function_graph_resolver.py`

## Runtime Owner

`src/indexer` owns resolver candidate quality.

## What Changed

- Bumped resolver version to `cpp-function-graph-resolver-v0.4`.
- Normalized template call names for lookup, so `Make<T>` can match project symbol `Make`.
- Preserved ambiguity for overload-looking calls.
- Kept arity as a score/basis hint only.

## Evidence

- Resolver output can include `template_name_normalized`.
- Ambiguous overload candidates remain `calls_ambiguous`.

## Tests

- Targeted resolver tests passed.
- Full suite is required before PR.

## Non-Goals Respected

- No compiler-level overload resolution.
- No behavior claims.
