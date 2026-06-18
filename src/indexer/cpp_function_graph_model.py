from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


FUNCTION_GRAPH_SCHEMA = "cpp.function_body_graph.v0.1"
SOURCE_STRUCTURE_CLAIM_STRENGTH = "source_structure_allowed"
BEHAVIOR_CLAIMS_ALLOWED = False

FunctionGraphMode = Literal["cache_only", "compute_if_missing", "refresh"]
ResolutionStatus = Literal["exact", "probable", "ambiguous", "unresolved", "external"]
FunctionGraphEdgeKind = Literal[
    "calls_resolved",
    "calls_candidate",
    "calls_ambiguous",
    "calls_external",
    "calls_unresolved",
    "reads_data_candidate",
    "writes_data_candidate",
    "uses_type_candidate",
    "control_flow_marker",
]

RESOLUTION_STATUSES: tuple[str, ...] = ("exact", "probable", "ambiguous", "unresolved", "external")
FUNCTION_GRAPH_EDGE_KINDS: tuple[str, ...] = (
    "calls_resolved",
    "calls_candidate",
    "calls_ambiguous",
    "calls_external",
    "calls_unresolved",
    "reads_data_candidate",
    "writes_data_candidate",
    "uses_type_candidate",
    "control_flow_marker",
)


@dataclass(frozen=True, slots=True)
class FunctionGraphRequest:
    symbol_id: str
    mode: FunctionGraphMode = "compute_if_missing"
    include_control_flow: bool = True
    include_data_access: bool = True
    include_external: bool = True
    max_edges: int = 200


@dataclass(frozen=True, slots=True)
class FunctionSourceSlice:
    symbol_id: str
    function_name: str | None
    qualified_name: str | None
    symbol_type: str
    file_id: str
    relative_path: str
    start_line: int
    end_line: int
    base_line: int
    base_byte: int
    text: str
    function_body_fingerprint: str


@dataclass(frozen=True, slots=True)
class FunctionAstExtract:
    symbol_id: str
    source_fingerprint: str
    parser_id: str
    parser_version: str
    extractor_version: str
    calls: tuple[dict, ...] = ()
    member_accesses: tuple[dict, ...] = ()
    local_declarations: tuple[dict, ...] = ()
    control_flow: tuple[dict, ...] = ()


@dataclass(frozen=True, slots=True)
class FunctionVisibilityContext:
    file_id: str
    file_path: str
    function_symbol_id: str
    current_namespace: tuple[str, ...]
    current_class_symbol_id: str | None
    current_class_name: str | None
    imported_modules: tuple[str, ...]
    visible_exported_symbols: tuple[dict, ...]
    same_file_symbols: tuple[dict, ...]
    same_file_data: tuple[dict, ...]
    member_data: tuple[dict, ...]
    using_declarations: tuple[dict, ...] = ()
    using_directives: tuple[dict, ...] = ()
    namespace_aliases: tuple[dict, ...] = ()
    local_declarations: tuple[dict, ...] = ()
    nested_type_symbols: tuple[dict, ...] = ()
    base_type_symbols: tuple[dict, ...] = ()


@dataclass(frozen=True, slots=True)
class FunctionGraphEdge:
    from_symbol_id: str
    edge_kind: FunctionGraphEdgeKind
    to_text: str
    to_symbol_id: str | None
    resolution_status: ResolutionStatus
    confidence: float
    basis: tuple[str, ...]
    candidates: tuple[dict, ...] = ()
    claim_strength: str = SOURCE_STRUCTURE_CLAIM_STRENGTH
    behavior_claims_allowed: bool = BEHAVIOR_CLAIMS_ALLOWED


@dataclass(frozen=True, slots=True)
class FunctionGraphFingerprints:
    function_body: str
    graph: str
    file: str | None = None
    symbol_index: str | None = None
    module_visibility: str | None = None


@dataclass(frozen=True, slots=True)
class FunctionGraphResult:
    schema: str
    status: str
    from_cache: bool
    symbol_id: str
    function_name: str | None
    qualified_name: str | None
    file: str
    start_line: int
    end_line: int
    parser_id: str | None
    parser_version: str | None
    resolver_version: str | None
    claim_strength: str
    behavior_claims_allowed: bool
    fingerprints: FunctionGraphFingerprints
    edges: tuple[FunctionGraphEdge, ...]


@dataclass(frozen=True, slots=True)
class FunctionGraphError:
    code: str
    message: str
    symbol_id: str | None = None
