# P25 Real Project Smoke Expansion

Implemented phase: Function Graph Parser and Ranking Slice P25.

## Changed

- Real-index MCP smoke now builds a multi-file temporary C++ project.
- The smoke computes more than one function graph and checks cache, xrefs, and symbol neighborhood output.

## Verification

- `tests/test_cpp_function_graph_mcp_smoke.py`

## Non-Goals Respected

No new MCP tool was added.
