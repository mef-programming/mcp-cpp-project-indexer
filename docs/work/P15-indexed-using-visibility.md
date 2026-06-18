# P15 Indexed Using Visibility

Implemented phase: Function Graph Hardening Slice P15.

## Changed

- `src/indexer/cpp_structural_scan.py` extracts file-scope and namespace-scope `using` declarations, `using namespace` directives, and namespace aliases.
- `src/indexer/cpp_file_index.py` stores those lists in each file index.
- `src/indexer/cpp_function_graph_visibility.py` loads those visibility candidates from in-memory index attributes or the persisted file index.
- The resolver continues to treat these as project-local candidates, not compiler-semantic proof.

## Runtime Ownership

The indexer owns extraction and persistence. Function graph visibility owns compact context construction.

## Verification

- `tests/test_cpp_function_graph_visibility.py` covers file-index-backed scope item loading.
- `tests/test_cpp_function_graph_mcp_smoke.py` covers real index build plus MCP graph resolution through `using namespace`.

## Non-Goals Respected

No new MCP tools. No behavior claims. No compiler-level overload or alias certainty.
