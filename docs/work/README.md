# Work Logs

This folder records implemented or prepared work slices for active workplans.

## Active Logs

- [P00 Function Graph Preflight](P00-function-graph-preflight.md) - preflight for `docs/workplans/on-demand-cpp-function-body-relation-graph.md`.
- [P01 Function Source Extraction](P01-function-source-extraction.md) - Step 1 runtime slice for indexed function source extraction.
- [P02 Data Contracts and Empty Graph](P02-data-contracts-empty-graph.md) - Step 2 contracts and empty graph result shell.
- [P03 Parser Adapter and Raw Extraction](P03-parser-adapter-raw-extraction.md) - Step 3 parser protocol, lightweight extractor, and Tree-sitter isolation.
- [P04 Visibility Context](P04-visibility-context.md) - Step 4 function-local visibility context from existing index data.
- [P05 Project-Only Resolver](P05-project-only-resolver.md) - Step 5 project-local call resolver v0.1.
- [P06 Cache and SQLite Storage](P06-cache-sqlite-storage.md) - Step 6 AST/graph caches and persisted graph edges in the existing index database.
- [P07 MCP get_function_body_graph](P07-mcp-get-function-body-graph.md) - Step 7 first MCP function graph tool and compact structural response.
- [P08 Xrefs and Neighborhood](P08-xrefs-neighborhood.md) - Step 8 persisted caller/callee xrefs and compact symbol neighborhood.
- [P09 Resolution Improvements](P09-resolution-improvements.md) - Step 9 using/alias/local type hint and overload scoring improvements.
- [P10 Optional Vector Sidecar Deferred](P10-optional-vector-sidecar-deferred.md) - Step 10 explicit future-work deferral, no runtime implementation.
- [P11 Data and Control-Flow Edges](P11-data-control-flow-edges.md) - graph edge expansion for data access and control-flow markers.

## Completed Logs

- Function graph workplan logs P00-P10 are complete for the initial implementation.
