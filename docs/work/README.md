# Work Logs

This folder records implemented or prepared work slices for function graph workplans.

## Completed Logs

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
- [P12 Real Index MCP Smoke](P12-real-index-mcp-smoke.md) - persisted real-index CodeIndexTools smoke for graph/cache/xref edges.
- [P13 Function Graph Workplan Hygiene](P13-function-graph-workplan-hygiene.md) - follow-up workplan closure and worklog README cleanup.
- [P14 Tree-sitter Adapter Gate](P14-tree-sitter-adapter-gate.md) - optional dependency status gate while preserving lightweight parser fallback.
- [P15 Indexed Using Visibility](P15-indexed-using-visibility.md) - file-index using declarations/directives and namespace alias visibility input.
- [P16 Cache Fingerprint Hardening](P16-cache-fingerprint-hardening.md) - structured graph option cache fingerprints.
- [P17 Function Graph Operator Docs](P17-function-graph-operator-docs.md) - compact MCP tool usage documentation.
- [P18 Hardening Patch Finalization](P18-hardening-patch-finalization.md) - dedicated branch and review-ready Function Graph scope.
- [P19 Using Scope Precision](P19-using-scope-precision.md) - scope-bounded using/alias candidates and relative namespace expansion.
- [P20 Tree-sitter Adapter Spike Decision](P20-tree-sitter-adapter-spike-decision.md) - optional adapter remains gated without hard dependency.
- [P21 Resolver Candidate Quality](P21-resolver-candidate-quality.md) - improved member and data candidate quality without behavior claims.
- [P22 Cache Storage Maintenance](P22-cache-storage-maintenance.md) - graph cache stats and version pruning.
- [P23 PR 6 Ready and Merge](P23-pr6-ready-merge.md) - PR #6 ready, merged, and local main synchronized.
- [P24 Parser Fixture Coverage](P24-parser-fixture-coverage.md) - lightweight parser coverage for templates, lambdas, chained calls, and macro noise.
- [P25 Real Project Smoke Expansion](P25-real-project-smoke-expansion.md) - multi-file real-index MCP smoke with multiple computed graphs.
- [P26 Resolver Ranking v0.3](P26-resolver-ranking-v03.md) - resolver version bump and priority coverage.
- [P27 Cache Maintenance UX](P27-cache-maintenance-ux.md) - internal cache maintenance documented without MCP tool expansion.
- [P28 Runtime Quality Baseline](P28-runtime-quality-baseline.md) - active workplan setup and realistic parser accuracy baseline.
- [P29 Parser Adapter Contract vNext](P29-parser-adapter-contract-vnext.md) - parser status/capability metadata and cache-version helpers.
- [P30 Tree-sitter Parity Spike](P30-tree-sitter-parity-spike.md) - optional dependency-gated parse probe through the existing parser protocol.
- [P31 Runtime Accuracy Smoke Matrix](P31-runtime-accuracy-smoke-matrix.md) - expanded real-index Function Graph smoke coverage.
- [P32 Resolver Cache Guardrails](P32-resolver-cache-guardrails.md) - parser status/capability cache compatibility guardrails.
- [P33 Tree-sitter Integration Preflight](P33-tree-sitter-integration-preflight.md) - PR #8 merge, fresh branch, and active Tree-sitter workplan.
- [P34 Tree-sitter Optional Packaging](P34-tree-sitter-optional-packaging.md) - optional dependency requirements without changing normal install behavior.
- [P35 Tree-sitter AST Extractor](P35-tree-sitter-ast-extractor.md) - AST traversal extraction for calls, member access, locals, and control flow.
- [P36 Tree-sitter Parser Selection Gate](P36-tree-sitter-parser-selection-gate.md) - explicit parser injection only; Lightweight remains default.
- [P37 Tree-sitter Parity And Accuracy](P37-tree-sitter-parity-and-accuracy.md) - dependency-gated Tree-sitter parity and accuracy coverage.
- [P38 Resolver Cache Preflight](P38-resolver-cache-preflight.md) - resolver/cache workplan setup and Tree-sitter workplan closure.
- [P39 Overload Template Ranking](P39-overload-template-ranking.md) - resolver v0.4 template normalization and overload ranking hints.
- [P40 Auto Type Local Initializer Hints](P40-auto-type-local-initializer-hints.md) - parser initializer metadata and project-local auto type hints.
- [P41 Nested Inherited Member Context](P41-nested-inherited-member-context.md) - nested/base type visibility context and resolver basis.
- [P42 Operator Call Structural Edges](P42-operator-call-structural-edges.md) - structural operator call extraction and resolver candidates.
- [P43 Cache Maintenance Admin UX](P43-cache-maintenance-admin-ux.md) - management-only Function Graph cache stats/prune commands.
- [P44 Real Project Resolver Cache Smoke](P44-real-project-resolver-cache-smoke.md) - resolver/cache smoke coverage and SmartFTP follow-up note.
- [P45 Cache Maintenance Stats Breakdown](P45-cache-maintenance-stats-breakdown.md) - richer management cache stats with version and edge breakdowns.
- [P46 Cache Maintenance Dry-Run Prune](P46-cache-maintenance-dry-run-prune.md) - dry-run-first prune flow with keep-current support.
- [P47 Management UI Function Graph Cache](P47-management-ui-function-graph-cache.md) - Management UI panel for cache stats, dry-run prune, and commit prune.
