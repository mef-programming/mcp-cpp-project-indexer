# Workplan: On-Demand C++ Function Body Relation Graph

Date: 2026-06-17
Status: completed
Implementation language: Python
Component: C++ MCP Project Indexer
Priority: medium/high

## Goal

Add an on-demand function-body graph layer to the existing Python C++ indexer.

The existing indexer already owns the base project knowledge:

```text
files
module graph/tree
module imports/exports
namespace/class/function/type symbols
declaration/definition ranges
symbol ids
file/index fingerprints
```

The new feature adds lazy function-body analysis:

```text
function symbol id
-> extract complete function text including body
-> parse with Tree-sitter or another AST parser
-> extract syntactic relations
-> resolve names against the existing index
-> cache graph edges with fingerprints
```

The graph is a navigation/evidence-routing structure, not a full C++ compiler.

## Core Principle

```text
Tree-sitter extracts syntax.
The existing indexer resolves project visibility.
The function graph stores candidate/resolved edges.
Source reads prove behavior.
External APIs stay external.
```

## Non-Goals

This work must not attempt to build a complete C++ semantic compiler.

Out of scope:

```text
full overload resolution
full template instantiation
ADL correctness
macro expansion semantics
external API resolution
standard library resolution
Win32/WIL/ATL/STL semantic modeling
compile_commands-dependent correctness
```

External APIs are not resolved. They are marked as external or unresolved.

## Runtime Model

The feature is on-demand.

Normal indexing remains fast and unchanged:

```text
normal index run
  -> file/module/symbol/range/fingerprint index
```

Function graph analysis runs only when requested:

```text
get_function_body_graph(symbolId, mode=compute_if_missing)
  -> extract function text
  -> parse AST
  -> resolve project-local candidates
  -> cache result
  -> return graph
```

Optional future background warmup may precompute frequently used functions, but the first implementation must not block the base index.

## Current Implementation State

```text
Step 0 preflight is documented in docs/work/P00-function-graph-preflight.md.
Step 1 function source extraction is documented in docs/work/P01-function-source-extraction.md.
Step 2 data contracts and empty graph result are documented in docs/work/P02-data-contracts-empty-graph.md.
Step 3 parser adapter and raw extraction are documented in docs/work/P03-parser-adapter-raw-extraction.md.
Step 4 visibility context is documented in docs/work/P04-visibility-context.md.
Step 5 project-only resolver is documented in docs/work/P05-project-only-resolver.md.
Step 6 cache and SQLite storage is documented in docs/work/P06-cache-sqlite-storage.md.
Step 7 MCP tool get_function_body_graph is documented in docs/work/P07-mcp-get-function-body-graph.md.
Step 8 xrefs and neighborhood tools are documented in docs/work/P08-xrefs-neighborhood.md.
Step 9 resolution improvements are documented in docs/work/P09-resolution-improvements.md.
Step 10 optional vector sidecar deferral is documented in docs/work/P10-optional-vector-sidecar-deferred.md.
No parser dependency has been added yet.
The first MCP function graph tool and persisted xref/neighborhood tools have been registered.
The optional vector sidecar remains future work only and was not implemented.
```

## Proposed MCP Tools

### get_function_body_graph

Purpose:

```text
Return direct function-body relation candidates for one function.
```

Input:

```json
{
  "symbolId": "s_f_...",
  "mode": "cache_only | compute_if_missing | refresh",
  "includeControlFlow": true,
  "includeDataAccess": true,
  "includeExternal": true,
  "maxEdges": 200
}
```

Output:

```json
{
  "schema": "cpp.function_body_graph.v0.1",
  "status": "computed",
  "fromCache": false,
  "symbolId": "s_f_...",
  "functionName": "NavigationBandProgress::Paint",
  "parser": "tree-sitter-cpp",
  "claimStrength": "source_structure_allowed",
  "behaviorClaimsAllowed": false,
  "graphFingerprint": "abc123",
  "sourceFingerprint": "def456",
  "edges": []
}
```

### get_call_xrefs_from

Purpose:

```text
Return callees / outgoing call edges for a function.
```

Direction:

```text
caller -> callee
```

### get_call_xrefs_to

Purpose:

```text
Return callers / incoming call edges for a function.
```

Direction:

```text
callee <- caller
```

### get_symbol_neighborhood

Purpose:

```text
Return callers + target + callees in one compact neighborhood.
```

Useful for Relay planning before deciding which source ranges to read.

Tool ownership for all function graph tools:

```text
MCP schema/registration: src/server/code_index_mcp_server.py
Runtime implementation: src/indexer/cpp_function_graph_service.py
Parser/resolver/cache: src/indexer/cpp_function_graph_*.py
Storage lookup: src/indexer/cpp_function_graph_storage.py
```

## Claim Strength

Function graph results are structural evidence only.

```text
function body graph       -> source_structure_allowed
read_symbol/read_range    -> source_behavior_allowed
vector/semantic hit       -> routing_hint_only
module export/import data -> metadata_only
```

The function graph may say:

```text
Paint probably calls _CalculatePulseOpacity.
```

It must not support behavior claims such as:

```text
_CalculatePulseOpacity computes animation opacity.
```

Behavior claims require source-read evidence.

## Project Layout Fit

The current repository source tree is:

```text
src/
  indexer/  C++ index build, update, scanner, SQLite, watcher, export, and source/range lookup
  server/   MCP stdio/HTTP server, management API, tool listing, and tool dispatch
  ui/       Textual TUI, terminal control center, and legacy menu UI
```

Root-level Python files are thin backward-compatible wrappers and must not own new function graph logic.

## Python Module Layout

Attach production code to the existing `src/indexer` owner. Keep the MCP transport and tool registration in the existing `src/server` owner.

Proposed files:

```text
src/indexer/
  cpp_function_graph_model.py
  cpp_function_graph_parser.py
  cpp_function_graph_tree_sitter.py
  cpp_function_graph_extract.py
  cpp_function_graph_visibility.py
  cpp_function_graph_resolver.py
  cpp_function_graph_cache.py
  cpp_function_graph_storage.py
  cpp_function_graph_service.py

src/server/
  code_index_mcp_server.py

tests/                         optional if a test tree is introduced in the phase
  fixtures/function_graph/
  test_cpp_function_graph_*.py
```

Do not create `src/cpp_indexer/`; it does not match this project. Do not put parser, resolver, or cache logic into `src/server/code_index_mcp_server.py`; that file should only expose MCP schema, validate tool arguments, call the indexer service, and format responses.

## Existing Owner Boundaries

Indexer owner:

```text
src/indexer/cpp_project_index.py
  loaded index model and source-range tool logic

src/indexer/cpp_index_sqlite.py
  SQLite lookup index writer/reader

src/indexer/cpp_file_index.py
src/indexer/cpp_structural_scan.py
src/indexer/cpp_module_scan.py
  existing source, symbol, and module extraction inputs
```

Server owner:

```text
src/server/code_index_mcp_server.py
  tool_definitions(...)
  CodeIndexTools tool methods
  McpServer.tool_handlers
```

The function graph service should live under `src/indexer` and be called by `CodeIndexTools`. New MCP tool definitions and handler registration belong in `src/server/code_index_mcp_server.py`.

### cpp_function_graph_model.py

Defines stable data contracts:

```python
@dataclass(frozen=True)
class FunctionAstExtract:
    symbol_id: str
    source_fingerprint: str
    parser_version: str
    calls: list[CallOccurrence]
    member_accesses: list[MemberAccessOccurrence]
    local_declarations: list[LocalDeclaration]
    control_flow: list[ControlFlowMarker]

@dataclass(frozen=True)
class FunctionGraphEdge:
    from_symbol_id: str
    edge_kind: str
    to_text: str
    to_symbol_id: str | None
    resolution_status: str
    confidence: float
    basis: list[str]
    claim_strength: str
    behavior_claims_allowed: bool
```

Allowed `resolution_status` values:

```text
exact
probable
ambiguous
unresolved
external
```

Allowed edge kinds:

```text
calls_resolved
calls_candidate
calls_ambiguous
calls_external
calls_unresolved
reads_data_candidate
writes_data_candidate
uses_type_candidate
control_flow_marker
```

## On-Demand Flow

```text
1. Receive symbolId.
2. Resolve symbolId to file path and function range from existing index.
3. Extract complete function text including signature and body.
4. Compute function body fingerprint.
5. Check FunctionGraphCache.
6. If cache miss:
     parse function text with AST parser
     extract raw syntactic occurrences
     build visibility context
     resolve occurrences against existing index
     write graph edges
     store cache entry
7. Return compact graph result.
```

## Function Extraction

The indexer already knows function ranges.

Preferred input to parser:

```text
complete function definition including signature and body
```

Example:

```cpp
void NavigationBandProgress::Paint(...)
{
    auto opacity = _CalculatePulseOpacity();
    _OverlayPosition = opacity;
}
```

If only the body is available in a future fallback path, wrap it:

```cpp
void __mw_dummy_function__()
{
    // original body here
}
```

Line and byte offsets must be mapped back to original file coordinates.

## AST Parser Adapter

Do not bind the rest of the indexer directly to Tree-sitter.

Use an adapter interface:

```python
class FunctionBodyParser(Protocol):
    parser_id: str
    parser_version: str

    def parse_function(
        self,
        function_text: str,
        base_line: int,
        base_byte: int,
    ) -> FunctionAstExtract:
        ...
```

Initial implementation:

```text
Tree-sitter C++ adapter
```

Future optional implementations:

```text
Clang/clangd semantic adapter
custom lightweight parser
test fixture parser
```

## Raw AST Extraction

The AST extractor should initially extract only cheap, stable syntax facts.

### Calls

Extract:

```text
callee text
qualified/unqualified/member call kind
argument count
line/column/range
```

Examples:

```cpp
foo()
NS::foo()
object.foo()
this->foo()
```

### Member/Data Accesses

Extract:

```text
identifier/member text
read/write candidate
assignment lhs/rhs
line/range
```

Examples:

```cpp
_OverlayPosition = value;     -> write candidate
if (_OverlayPosition > 0)     -> read candidate
this->_State.Reset();         -> member call or member access
```

### Local Declarations

Extract:

```text
local name
type text when visible
line/range
```

Examples:

```cpp
RECT rc;
auto opacity = ...;
NavigationBandProgress* p = ...;
```

### Control-Flow Markers

Extract compact markers only:

```text
if
switch
for
while
return
throw
try
catch
co_await
co_return
```

Control-flow markers are not a full CFG in v0.1.

## Visibility Context

Before resolving names, build a function-local visibility context from existing index data.

Input:

```text
function symbol
file
module imports
module exports visible to file
namespace stack
class/type stack
base classes if indexed
same-file symbols
anonymous namespace symbols
static/file-local symbols
using declarations
using namespace directives
namespace aliases
local declarations from AST
```

Suggested structure:

```python
@dataclass
class FunctionVisibilityContext:
    file_id: str
    file_path: str
    function_symbol_id: str
    current_namespace: list[str]
    current_class_symbol_id: str | None
    imported_modules: list[str]
    visible_exported_symbols: list[str]
    same_file_symbols: list[str]
    anonymous_namespace_symbols: list[str]
    using_declarations: list[UsingDeclaration]
    using_directives: list[UsingDirective]
    namespace_aliases: list[NamespaceAlias]
    local_declarations: list[LocalDeclaration]
```

## Handling using namespace

`using namespace NS;` must be treated as an additional candidate namespace, not as a guaranteed match.

Store:

```text
scope id
namespace name
activeFromLine
activeToLine
source file
```

Resolution rule:

```text
Unqualified lookup includes active using namespace candidates.
If more than one candidate matches, return ambiguous.
```

Example:

```cpp
using namespace SmartFTP::Theme;

void Paint()
{
    DrawBackground();
}
```

Resolver checks:

```text
current class
current namespace
same-file symbols
using namespace SmartFTP::Theme
imported module exports
```

If one project-local candidate remains:

```text
resolutionStatus = probable or exact
```

If multiple candidates remain:

```text
resolutionStatus = ambiguous
```

## Name Resolution Rules

### Unqualified Call

For:

```cpp
foo(a, b);
```

Resolver order:

```text
1. local declarations / local callable objects
2. current class / this scope
3. base classes, if indexed
4. enclosing namespace chain
5. same-file static symbols
6. anonymous namespace symbols
7. using declarations
8. using namespace candidates
9. imported module exports visible in scope
10. global project symbols
11. external/unresolved
```

### Qualified Call

For:

```cpp
UI::DrawText(...)
SmartFTP::UI::DrawText(...)
```

Resolver order:

```text
1. expand namespace aliases
2. search exact qualified name in project index
3. search imported module exports
4. if not found, mark external/unresolved
```

### Member Call

For:

```cpp
object.foo()
this->foo()
foo()
```

Resolution:

```text
this->foo:
  current class member search

object.foo:
  if object type is known from local declarations or member data, search that type
  otherwise return member_call_candidate

foo:
  normal unqualified lookup, including class scope
```

## Overload Resolution

Do not attempt complete C++ overload resolution in v0.1.

Use scoring.

Signals:

```text
name match
scope match
argument count match
default argument compatibility when known
member/static context
literal argument hints
local variable type hints when available
```

Output may be:

```text
exact
probable
ambiguous
unresolved
external
```

If multiple overloads are plausible, return all candidates with scores.

Example:

```json
{
  "resolutionStatus": "ambiguous",
  "toText": "Draw",
  "candidates": [
    {
      "symbolId": "s_f_1",
      "qualifiedName": "SmartFTP::UI::Draw(HDC, RECT)",
      "score": 0.72,
      "basis": ["using_namespace", "arity_match"]
    },
    {
      "symbolId": "s_f_2",
      "qualifiedName": "SmartFTP::UI::Draw(ID2D1DeviceContext*, D2D1_RECT_F)",
      "score": 0.68,
      "basis": ["using_namespace", "arity_match"]
    }
  ]
}
```

## External API Rule

The resolver must not try to resolve external APIs.

If the target is not in:

```text
project symbol index
project module exports
same-file symbols
known project namespaces
```

then mark as:

```text
resolutionStatus = external
```

Example:

```cpp
SendMessageW(...)
std::move(...)
wil::unique_hmodule(...)
```

Output:

```json
{
  "edgeKind": "calls_external",
  "toText": "SendMessageW",
  "resolutionStatus": "external",
  "reason": "not_in_project_symbol_index",
  "claimStrength": "source_structure_allowed",
  "behaviorClaimsAllowed": false
}
```

## Graph Expansion

Direct graph:

```text
depth = 1
```

Only direct function-body edges.

Recursive graph:

```text
depth > 1
```

May parse project-local resolved/probable callees lazily.

Expansion guards:

```text
maxDepth
maxNodes
maxEdges
cycle detection
external stops
ambiguous does not auto-expand unless explicitly requested
```

Default:

```text
depth = 1
maxNodes = 25
maxEdges = 200
```

## Cache Design

Use two cache layers.

### Function AST Extract Cache

Key:

```text
functionBodyFingerprint
parserId
parserVersion
extractorVersion
```

Value:

```text
raw AST-derived calls/member accesses/local declarations/control-flow markers
```

### Function Graph Resolution Cache

Key:

```text
functionBodyFingerprint
fileFingerprint
symbolIndexFingerprint
moduleVisibilityFingerprint
parserId
parserVersion
resolverVersion
```

Value:

```text
resolved/candidate/ambiguous/external graph edges
```

Reason:

```text
Function text may stay unchanged while module exports or symbol index changes.
In that case AST extraction remains valid but resolution must refresh.
```

## Fingerprints

Each graph result should include:

```text
functionBodyFingerprint
fileFingerprint
symbolIndexFingerprint
moduleVisibilityFingerprint
parserVersion
resolverVersion
graphFingerprint
```

These fingerprints make cached graph results safe to reuse and explainable in Relay artifacts.

## Storage

Initial implementation may use SQLite tables next to the existing index database.

Implement storage through the existing SQLite ownership pattern in `src/indexer/cpp_index_sqlite.py` plus a focused `src/indexer/cpp_function_graph_storage.py` helper. Do not create a separate database root or a second index lifecycle.

Suggested tables:

```sql
function_ast_extract_cache(
  function_symbol_id TEXT,
  function_body_fingerprint TEXT,
  parser_id TEXT,
  parser_version TEXT,
  extractor_version TEXT,
  payload_json TEXT,
  created_at TEXT,
  PRIMARY KEY(function_symbol_id, function_body_fingerprint, parser_id, parser_version, extractor_version)
);

function_graph_cache(
  function_symbol_id TEXT,
  graph_fingerprint TEXT,
  function_body_fingerprint TEXT,
  file_fingerprint TEXT,
  symbol_index_fingerprint TEXT,
  module_visibility_fingerprint TEXT,
  parser_id TEXT,
  parser_version TEXT,
  resolver_version TEXT,
  payload_json TEXT,
  created_at TEXT,
  PRIMARY KEY(function_symbol_id, graph_fingerprint)
);

function_graph_edges(
  graph_fingerprint TEXT,
  from_symbol_id TEXT,
  to_symbol_id TEXT,
  to_text TEXT,
  edge_kind TEXT,
  resolution_status TEXT,
  confidence REAL,
  claim_strength TEXT,
  behavior_claims_allowed INTEGER,
  basis_json TEXT,
  evidence_json TEXT
);
```

## Result Example

```json
{
  "schema": "cpp.function_body_graph.v0.1",
  "status": "computed",
  "fromCache": false,
  "symbolId": "s_f_paint",
  "functionName": "NavigationBandProgress::Paint",
  "file": "DWrapper/Shell/AddressBand/NavigationBandProgress/NavigationBandProgress.cpp",
  "range": {
    "startLine": 580,
    "endLine": 690
  },
  "parser": {
    "id": "tree-sitter-cpp",
    "version": "..."
  },
  "resolver": {
    "version": "cpp-function-graph-resolver-v0.1"
  },
  "claimStrength": "source_structure_allowed",
  "behaviorClaimsAllowed": false,
  "fingerprints": {
    "functionBody": "sha256:...",
    "file": "sha256:...",
    "symbolIndex": "sha256:...",
    "moduleVisibility": "sha256:...",
    "graph": "sha256:..."
  },
  "edges": [
    {
      "edgeKind": "calls_candidate",
      "toText": "_CalculatePulseOpacity",
      "toSymbolId": "s_f_calc_pulse",
      "resolutionStatus": "probable",
      "confidence": 0.86,
      "basis": ["same_class", "arity_match", "same_file"],
      "evidence": {
        "line": 621,
        "kind": "tree_sitter_call_expression"
      }
    }
  ]
}
```

## Relay Usage

Relay may use the graph for planning:

```text
find_symbol Paint
-> get_function_body_graph(Paint)
-> see direct helper/data candidates
-> read_symbol Paint
-> read_symbol selected project-local helper
-> read_data selected member if needed
```

Relay must not use the graph as behavior evidence.

Allowed Relay claim:

```text
The graph suggests Paint has a project-local call candidate to _CalculatePulseOpacity.
```

Not allowed without source read:

```text
_CalculatePulseOpacity computes the animation opacity.
```

## Testing

### Unit Tests

Place tests according to the first phase that introduces a test tree. If this repository still has no test root when Phase 1 starts, create a small top-level `tests/` tree rather than hiding tests under `src/indexer`.

```text
extract_unqualified_call
extract_qualified_call
extract_member_call
extract_assignment_lhs_member_write
extract_local_declaration
using_namespace_adds_candidate_scope
namespace_alias_expands_qualified_name
same_class_method_resolution
same_file_static_resolution
imported_module_export_resolution
ambiguous_overload_set
external_api_marked_external
cache_hit_same_fingerprints
resolution_refresh_on_module_visibility_change
```

### Fixture Files

Create compact C++ fixtures for:

```text
namespace lookup
using namespace
using declaration
namespace alias
class member call
static same-file helper
anonymous namespace helper
overloaded functions
imported module export call
external Win32/STL call
member data read/write
```

### Smoke Test

```text
check-function-body-graph-smoke
```

Scenario:

```text
1. Load small fixture project.
2. Find function symbol.
3. Call get_function_body_graph(compute_if_missing).
4. Assert expected edges.
5. Call again with cache_only.
6. Assert cache hit and same graphFingerprint.
```

## Implementation Steps

### Step 0: Repo and Contract Preflight

Status: documented.

Owner:

```text
docs/work/P00-function-graph-preflight.md
```

Actions:

```text
Inspect src/indexer/README.md and src/server/README.md.
Identify current symbol/range lookup owners in src/indexer/cpp_project_index.py.
Identify current SQLite owner in src/indexer/cpp_index_sqlite.py.
Identify current MCP schema and dispatch points in src/server/code_index_mcp_server.py.
Record Tree-sitter dependency decision before parser work starts.
```

Observable result:

```text
Worklog records owners, target paths, test strategy, dependency stance, and non-goals.
```

### Step 1: Function Source Extraction

Status: complete.

Owner:

```text
src/indexer/cpp_function_graph_service.py
```

Actions:

```text
Resolve symbolId through the loaded index.
Reject missing symbols and non-callable symbols with structured errors.
Read complete indexed symbol range from the source file.
Return function text, fileId, relativePath, start/end lines, base line/byte, and functionBodyFingerprint.
Do not parse the function body yet.
Do not expose an MCP tool yet.
```

Observable result:

```text
Unit test proves a function symbol maps to exact indexed text and stable fingerprint.
```

### Step 2: Data Contracts and Empty Service Shell

Status: complete.

Owner:

```text
src/indexer/cpp_function_graph_model.py
src/indexer/cpp_function_graph_service.py
```

Actions:

```text
Define FunctionGraphRequest, FunctionSourceSlice, FunctionAstExtract, FunctionGraphEdge, FunctionGraphResult, and FunctionGraphFingerprints.
Define allowed resolution statuses and edge kinds as explicit constants or Literal types.
Return a valid empty graph result for a function source slice.
Always emit claimStrength=source_structure_allowed and behaviorClaimsAllowed=false.
```

Observable result:

```text
Unit test proves the empty service result has schema, symbol id, source fingerprint, graph fingerprint, and zero edges.
```

### Step 3: Parser Adapter and Raw Extraction

Status: complete.

Owner:

```text
src/indexer/cpp_function_graph_parser.py
src/indexer/cpp_function_graph_extract.py
src/indexer/cpp_function_graph_tree_sitter.py
```

Actions:

```text
Define FunctionBodyParser protocol.
Keep Tree-sitter isolated behind the parser adapter.
Add a test fixture parser if Tree-sitter is not yet available in the repo dependencies.
Extract raw calls, member/data access candidates, local declarations, and compact control-flow markers.
Map parser locations back to original file lines.
```

Observable result:

```text
Unit tests prove raw extraction for unqualified call, qualified call, member call, assignment lhs member write, local declaration, and control-flow marker.
```

### Step 4: Visibility Context

Status: complete.

Owner:

```text
src/indexer/cpp_function_graph_visibility.py
```

Actions:

```text
Build FunctionVisibilityContext from existing symbol, file, module, and data indexes.
Include current namespace/class, same-file symbols, imported modules, visible exports, and indexed member data when available.
Keep using namespace/declaration and namespace alias support as recorded candidates, not guaranteed matches.
```

Observable result:

```text
Unit test proves the visibility context for a fixture function includes same-file callable symbols and current class context.
```

### Step 5: Project-Only Resolver v0.1

Status: complete.

Owner:

```text
src/indexer/cpp_function_graph_resolver.py
```

Actions:

```text
Resolve unqualified calls against current class, same-file symbols, namespace chain, and project symbols.
Resolve qualified calls by exact qualified name and imported module exports.
Resolve this->member calls against current class when indexed.
Return ambiguous candidate sets instead of choosing fake certainty.
Mark non-project targets external or unresolved.
Never model Win32, STL, WIL, ATL, ADL, templates, or full overload semantics.
```

Observable result:

```text
Unit tests prove same-class method resolution, same-file static resolution, ambiguous overload output, and external API marking.
```

### Step 6: Cache and SQLite Storage

Status: complete.

Owner:

```text
src/indexer/cpp_function_graph_cache.py
src/indexer/cpp_function_graph_storage.py
src/indexer/cpp_index_sqlite.py
```

Actions:

```text
Add AST extract cache keyed by functionBodyFingerprint, parser id/version, and extractor version.
Add graph resolution cache keyed by function body, file, symbol index, module visibility, parser, and resolver fingerprints.
Store graph edges next to the existing index database.
Do not create a separate database root or second index lifecycle.
```

Observable result:

```text
Smoke test proves compute_if_missing stores a graph and cache_only returns the same graphFingerprint.
```

### Step 7: MCP Tool get_function_body_graph

Status: complete.

Owner:

```text
src/server/code_index_mcp_server.py
```

Actions:

```text
Register get_function_body_graph in tool_definitions.
Add a CodeIndexTools method that validates arguments and calls the indexer service.
Add handler registration in McpServer.tool_handlers.
Keep output compact and packable.
Return schema, status, fromCache, symbolId, functionName, parser/resolver metadata, fingerprints, edges, claimStrength, and behaviorClaimsAllowed.
```

Observable result:

```text
MCP smoke test proves get_function_body_graph(compute_if_missing) returns structural graph data without behavior claims.
```

### Step 8: Xrefs and Neighborhood

Status: complete.

Owner:

```text
src/indexer/cpp_function_graph_storage.py
src/indexer/cpp_function_graph_service.py
src/server/code_index_mcp_server.py
```

Actions:

```text
Expose get_call_xrefs_from only after edges are persisted.
Expose get_call_xrefs_to only after incoming edge lookup exists.
Expose get_symbol_neighborhood as target plus compact caller/callee sets.
Keep all three tools structural and compact.
```

Observable result:

```text
Smoke test proves persisted edges can be queried in both directions and as a neighborhood.
```

### Step 9: Resolution Improvements

Status: complete.

Owner:

```text
src/indexer/cpp_function_graph_visibility.py
src/indexer/cpp_function_graph_resolver.py
```

Actions:

```text
Improve using namespace and using declaration candidate handling.
Add namespace alias expansion.
Improve overload scoring with arity, scope, member/static context, and local type hints.
Keep ambiguous results explicit.
```

Observable result:

```text
Unit tests prove using namespace ambiguity, namespace alias qualified lookup, and overload scoring without fake exact matches.
```

### Step 10: Optional Vector Sidecar

Status: deferred future work.

Owner:

```text
future work only
```

Actions:

```text
Do not implement until function graph storage and MCP behavior are stable.
If added later, vectors are routing_hint_only and never behavior evidence.
```

Observable result:

```text
No implementation in the initial workplan.
```

## Acceptance Criteria

```text
Base indexing remains unchanged and fast.
Function graph analysis is on-demand.
Tree-sitter or AST parser is isolated behind a Python adapter.
Complete function text including body is extracted from existing ranges.
Project-local calls resolve to symbol IDs when possible.
External calls stay external/unresolved.
Ambiguous overloads return candidate sets, not fake certainty.
Graph results are fingerprint-backed and cacheable.
MCP output declares source_structure_allowed and behaviorClaimsAllowed=false.
Relay can use graph results for planning but not behavior claims.
Smoke tests prove compute + cache + xrefs behavior.
```

## Workplan Discipline

Before implementing any phase:

```text
1. Read this workplan.
2. Inspect current index ownership for symbols, file ranges, module visibility, storage, and MCP tools.
3. Report the exact phase being implemented.
4. Report expected touched files before coding.
5. Stop if the phase would require unrelated provider behavior, a second indexer loop, or a broad helper library.
```

After implementing a phase:

```text
1. Add or update a docs/work/Pxx-*.md work log.
2. Record changed files, owner/module, runtime path, tests, cache/artifact evidence, non-goals, and follow-ups.
3. Update docs/work/README.md if a new work log file is added.
4. Keep this workplan active until all phases are complete and verified.
```

## Final Report Format

When implemented, report:

```text
Summary:
  What was added.

Parser:
  Which AST parser adapter is active.

Index integration:
  Which existing index data is used.

Resolution:
  Which name-resolution cases work.

Cache:
  Which fingerprints protect reuse.

MCP tools:
  Which tools were added.

Tests:
  Unit and smoke tests run.

Non-goals respected:
  No full C++ compiler semantics.
  No external API resolution.
  No behavior claims from graph data.
```
