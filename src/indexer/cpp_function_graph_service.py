from __future__ import annotations

import hashlib
import json

from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

from cpp_function_graph_cache import (
    FunctionAstCacheKey,
    FunctionGraphCacheKey,
    graph_cache_options_fingerprint,
    graph_cache_options_for_request,
    graph_cache_options_payload,
    stable_json_fingerprint,
)
from cpp_function_graph_extract import EXTRACTOR_VERSION, LightweightFunctionBodyParser
from cpp_function_graph_model import (
    BEHAVIOR_CLAIMS_ALLOWED,
    FUNCTION_GRAPH_SCHEMA,
    SOURCE_STRUCTURE_CLAIM_STRENGTH,
    FunctionGraphError,
    FunctionGraphFingerprints,
    FunctionGraphRequest,
    FunctionGraphResult,
    FunctionSourceSlice,
)
from cpp_function_graph_parser import (
    parser_cache_version,
    parser_capability_status,
    parser_status_fingerprint,
    parser_status_payload,
)
from cpp_function_graph_resolver import RESOLVER_VERSION, resolve_function_graph_edges
from cpp_function_graph_storage import FunctionGraphStorage
from cpp_function_graph_visibility import build_function_visibility_context
from cpp_project_index import CODE_ENTITY_CALLABLE_SYMBOL_TYPES


FUNCTION_GRAPH_XREF_SCHEMA = "cpp.function_graph_xrefs.v0.1"
FUNCTION_GRAPH_NEIGHBORHOOD_SCHEMA = "cpp.function_graph_neighborhood.v0.1"


class FunctionGraphSourceError(ValueError):
    def __init__(self, error: FunctionGraphError) -> None:
        super().__init__(error.message)
        self.error = error

    def to_dict(self) -> dict[str, Any]:
        return asdict(self.error)


def text_fingerprint(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def stable_payload_fingerprint(payload: dict[str, Any]) -> str:
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return text_fingerprint(text)


class FunctionGraphSourceService:
    def __init__(
        self,
        *,
        project_root: Path,
        index: Any,
        index_root: Path | None = None,
        parser: Any | None = None,
        storage: FunctionGraphStorage | None = None,
    ) -> None:
        self.project_root = project_root
        self.index = index
        self.index_root = index_root
        self.parser = parser or LightweightFunctionBodyParser()
        self.storage = storage or (FunctionGraphStorage.from_index_root(index_root) if index_root is not None else None)

    def extract_function_source(self, symbol_id: str) -> FunctionSourceSlice:
        symbol = self._symbol_by_id(symbol_id)
        symbol_type = str(symbol.get("type") or "")

        if symbol_type not in CODE_ENTITY_CALLABLE_SYMBOL_TYPES:
            raise FunctionGraphSourceError(
                FunctionGraphError(
                    code="not_callable_symbol",
                    message=f"Symbol {symbol_id!r} is not an indexed callable symbol.",
                    symbol_id=symbol_id,
                )
            )

        file_id = str(symbol.get("fileId") or "")
        file_item = self._file_by_id(file_id, symbol_id=symbol_id)
        relative_path = str(file_item.get("relativePath") or file_id)
        source_path = self.project_root / relative_path

        if not source_path.exists():
            raise FunctionGraphSourceError(
                FunctionGraphError(
                    code="source_file_missing",
                    message=f"Source file for symbol {symbol_id!r} does not exist: {relative_path}",
                    symbol_id=symbol_id,
                )
            )

        start_line = int(symbol.get("startLine") or 0)
        end_line = int(symbol.get("endLine") or 0)
        if start_line < 1 or end_line < start_line:
            raise FunctionGraphSourceError(
                FunctionGraphError(
                    code="invalid_symbol_range",
                    message=f"Symbol {symbol_id!r} has an invalid source range.",
                    symbol_id=symbol_id,
                )
            )

        source_text = source_path.read_text(encoding="utf-8", errors="replace")
        lines = source_text.splitlines(keepends=True)

        if start_line > len(lines):
            raise FunctionGraphSourceError(
                FunctionGraphError(
                    code="symbol_range_out_of_file",
                    message=f"Symbol {symbol_id!r} starts beyond the end of {relative_path}.",
                    symbol_id=symbol_id,
                )
            )

        end_line = min(end_line, len(lines))
        function_text = "".join(lines[start_line - 1:end_line])
        base_byte = len("".join(lines[:start_line - 1]).encode("utf-8"))

        return FunctionSourceSlice(
            symbol_id=symbol_id,
            function_name=str(symbol.get("shortName") or "") or None,
            qualified_name=str(symbol.get("qualifiedName") or "") or None,
            symbol_type=symbol_type,
            file_id=file_id,
            relative_path=relative_path,
            start_line=start_line,
            end_line=end_line,
            base_line=start_line,
            base_byte=base_byte,
            text=function_text,
            function_body_fingerprint=text_fingerprint(function_text),
        )

    def build_empty_graph_result(
        self,
        request_or_symbol_id: FunctionGraphRequest | str,
    ) -> FunctionGraphResult:
        request = (
            FunctionGraphRequest(symbol_id=request_or_symbol_id)
            if isinstance(request_or_symbol_id, str)
            else request_or_symbol_id
        )
        source = self.extract_function_source(request.symbol_id)
        graph_fingerprint = stable_payload_fingerprint(
            {
                "schema": FUNCTION_GRAPH_SCHEMA,
                "symbolId": source.symbol_id,
                "functionBodyFingerprint": source.function_body_fingerprint,
                "edges": [],
                "parser": None,
                "resolver": None,
            }
        )

        return FunctionGraphResult(
            schema=FUNCTION_GRAPH_SCHEMA,
            status="computed",
            from_cache=False,
            symbol_id=source.symbol_id,
            function_name=source.function_name,
            qualified_name=source.qualified_name,
            file=source.relative_path,
            start_line=source.start_line,
            end_line=source.end_line,
            parser_id=None,
            parser_version=None,
            resolver_version=None,
            claim_strength=SOURCE_STRUCTURE_CLAIM_STRENGTH,
            behavior_claims_allowed=BEHAVIOR_CLAIMS_ALLOWED,
            fingerprints=FunctionGraphFingerprints(
                function_body=source.function_body_fingerprint,
                graph=graph_fingerprint,
            ),
            edges=(),
        )

    def get_function_body_graph(
        self,
        request_or_symbol_id: FunctionGraphRequest | str,
        *,
        file_fingerprint: str,
        symbol_index_fingerprint: str,
        module_visibility_fingerprint: str,
    ) -> FunctionGraphResult:
        request = (
            FunctionGraphRequest(symbol_id=request_or_symbol_id)
            if isinstance(request_or_symbol_id, str)
            else request_or_symbol_id
        )
        source = self.extract_function_source(request.symbol_id)
        parser_status = parser_capability_status(self.parser)
        cache_parser_version = parser_cache_version(self.parser)
        parser_status_hash = parser_status_fingerprint(parser_status)
        cache_options = graph_cache_options_for_request(request)
        cache_options_fingerprint = graph_cache_options_fingerprint(cache_options)
        cache_resolver_version = _cache_resolver_version(request, parser_status_hash=parser_status_hash)
        graph_key = FunctionGraphCacheKey(
            function_symbol_id=source.symbol_id,
            function_body_fingerprint=source.function_body_fingerprint,
            file_fingerprint=file_fingerprint,
            symbol_index_fingerprint=symbol_index_fingerprint,
            module_visibility_fingerprint=module_visibility_fingerprint,
            parser_id=self.parser.parser_id,
            parser_version=cache_parser_version,
            resolver_version=cache_resolver_version,
        )

        if self.storage is not None and request.mode != "refresh":
            cached = self.storage.load_graph_result(graph_key)
            if cached is not None:
                return replace(cached, from_cache=True, status="computed")

        if request.mode == "cache_only":
            return replace(
                self.build_empty_graph_result(request),
                status="cache_miss",
                parser_id=self.parser.parser_id,
                parser_version=self.parser.parser_version,
                resolver_version=RESOLVER_VERSION,
                fingerprints=FunctionGraphFingerprints(
                    function_body=source.function_body_fingerprint,
                    graph=stable_json_fingerprint(
                        {
                            "schema": FUNCTION_GRAPH_SCHEMA,
                            "status": "cache_miss",
                            "symbolId": source.symbol_id,
                            "functionBodyFingerprint": source.function_body_fingerprint,
                            "parser": self.parser.parser_id,
                            "resolver": RESOLVER_VERSION,
                            "cacheResolver": cache_resolver_version,
                            "parserStatusFingerprint": parser_status_hash,
                            "graphOptionsFingerprint": cache_options_fingerprint,
                        }
                    ),
                    file=file_fingerprint,
                    symbol_index=symbol_index_fingerprint,
                    module_visibility=module_visibility_fingerprint,
                ),
            )

        ast_extract = None
        ast_key = FunctionAstCacheKey(
            function_symbol_id=source.symbol_id,
            function_body_fingerprint=source.function_body_fingerprint,
            parser_id=self.parser.parser_id,
            parser_version=cache_parser_version,
            extractor_version=EXTRACTOR_VERSION,
        )
        if self.storage is not None and request.mode != "refresh":
            ast_extract = self.storage.load_ast_extract(ast_key)

        if ast_extract is None:
            ast_extract = self.parser.parse_function(
                symbol_id=source.symbol_id,
                source_fingerprint=source.function_body_fingerprint,
                function_text=source.text,
                base_line=source.base_line,
                base_byte=source.base_byte,
            )
            if self.storage is not None:
                self.storage.store_ast_extract(ast_key, ast_extract)

        visibility = build_function_visibility_context(
            index=self.index,
            source=source,
            ast_extract=ast_extract,
        )
        edges = resolve_function_graph_edges(
            ast_extract=ast_extract,
            visibility=visibility,
            include_control_flow=request.include_control_flow,
            include_data_access=request.include_data_access,
            include_external=request.include_external,
            max_edges=request.max_edges,
        )
        graph_fingerprint = stable_json_fingerprint(
            {
                "schema": FUNCTION_GRAPH_SCHEMA,
                "symbolId": source.symbol_id,
                "functionBodyFingerprint": source.function_body_fingerprint,
                "parser": {
                    "id": ast_extract.parser_id,
                    "version": ast_extract.parser_version,
                },
                "resolverVersion": RESOLVER_VERSION,
                "cacheResolverVersion": cache_resolver_version,
                "parserStatus": parser_status_payload(parser_status),
                "parserStatusFingerprint": parser_status_hash,
                "graphOptions": graph_cache_options_payload(cache_options),
                "graphOptionsFingerprint": cache_options_fingerprint,
                "edges": [asdict(edge) for edge in edges],
            }
        )
        result = FunctionGraphResult(
            schema=FUNCTION_GRAPH_SCHEMA,
            status="computed",
            from_cache=False,
            symbol_id=source.symbol_id,
            function_name=source.function_name,
            qualified_name=source.qualified_name,
            file=source.relative_path,
            start_line=source.start_line,
            end_line=source.end_line,
            parser_id=ast_extract.parser_id,
            parser_version=ast_extract.parser_version,
            resolver_version=RESOLVER_VERSION,
            claim_strength=SOURCE_STRUCTURE_CLAIM_STRENGTH,
            behavior_claims_allowed=BEHAVIOR_CLAIMS_ALLOWED,
            fingerprints=FunctionGraphFingerprints(
                function_body=source.function_body_fingerprint,
                graph=graph_fingerprint,
                file=file_fingerprint,
                symbol_index=symbol_index_fingerprint,
                module_visibility=module_visibility_fingerprint,
            ),
            edges=edges,
        )

        if self.storage is not None:
            self.storage.store_graph_result(graph_key, result)

        return result

    def get_call_xrefs_from(self, symbol_id: str, *, limit: int = 200) -> dict[str, Any]:
        self._symbol_by_id(symbol_id)
        storage = self._require_storage()
        edges = storage.list_edges_from(symbol_id, limit=limit)
        return {
            "schema": FUNCTION_GRAPH_XREF_SCHEMA,
            "status": "computed",
            "direction": "from",
            "symbolId": symbol_id,
            "symbol": self._compact_symbol(symbol_id),
            "returnedEdges": len(edges),
            "claimStrength": SOURCE_STRUCTURE_CLAIM_STRENGTH,
            "behaviorClaimsAllowed": BEHAVIOR_CLAIMS_ALLOWED,
            "edges": list(edges),
        }

    def get_call_xrefs_to(self, symbol_id: str, *, limit: int = 200) -> dict[str, Any]:
        self._symbol_by_id(symbol_id)
        storage = self._require_storage()
        edges = storage.list_edges_to(symbol_id, limit=limit)
        return {
            "schema": FUNCTION_GRAPH_XREF_SCHEMA,
            "status": "computed",
            "direction": "to",
            "symbolId": symbol_id,
            "symbol": self._compact_symbol(symbol_id),
            "returnedEdges": len(edges),
            "claimStrength": SOURCE_STRUCTURE_CLAIM_STRENGTH,
            "behaviorClaimsAllowed": BEHAVIOR_CLAIMS_ALLOWED,
            "edges": list(edges),
        }

    def get_symbol_neighborhood(
        self,
        symbol_id: str,
        *,
        incoming_limit: int = 100,
        outgoing_limit: int = 100,
    ) -> dict[str, Any]:
        self._symbol_by_id(symbol_id)
        storage = self._require_storage()
        incoming_edges = storage.list_edges_to(symbol_id, limit=incoming_limit)
        outgoing_edges = storage.list_edges_from(symbol_id, limit=outgoing_limit)
        return {
            "schema": FUNCTION_GRAPH_NEIGHBORHOOD_SCHEMA,
            "status": "computed",
            "symbolId": symbol_id,
            "target": self._compact_symbol(symbol_id),
            "callerCount": len(_unique_edge_symbol_ids(incoming_edges, key="fromSymbolId")),
            "calleeCount": len(_unique_edge_symbol_ids(outgoing_edges, key="toSymbolId")),
            "incomingEdgeCount": len(incoming_edges),
            "outgoingEdgeCount": len(outgoing_edges),
            "claimStrength": SOURCE_STRUCTURE_CLAIM_STRENGTH,
            "behaviorClaimsAllowed": BEHAVIOR_CLAIMS_ALLOWED,
            "callers": [
                self._compact_symbol(caller_id)
                for caller_id in _unique_edge_symbol_ids(incoming_edges, key="fromSymbolId")
            ],
            "callees": [
                self._compact_symbol(callee_id)
                for callee_id in _unique_edge_symbol_ids(outgoing_edges, key="toSymbolId")
            ],
            "incomingEdges": list(incoming_edges),
            "outgoingEdges": list(outgoing_edges),
        }

    def _symbol_by_id(self, symbol_id: str) -> dict[str, Any]:
        symbol = self.index.symbol_by_id.get(symbol_id)

        if symbol is None:
            raise FunctionGraphSourceError(
                FunctionGraphError(
                    code="symbol_not_found",
                    message=f"Symbol {symbol_id!r} was not found in the loaded index.",
                    symbol_id=symbol_id,
                )
            )

        return dict(symbol)

    def _file_by_id(self, file_id: str, *, symbol_id: str) -> dict[str, Any]:
        file_item = self.index.file_by_id.get(file_id)

        if file_item is None:
            raise FunctionGraphSourceError(
                FunctionGraphError(
                    code="file_not_found",
                    message=f"File {file_id!r} for symbol {symbol_id!r} was not found in the loaded index.",
                    symbol_id=symbol_id,
                )
            )

        return dict(file_item)

    def _require_storage(self) -> FunctionGraphStorage:
        if self.storage is None:
            raise FunctionGraphSourceError(
                FunctionGraphError(
                    code="graph_storage_unavailable",
                    message="Function graph storage is not configured for this index.",
                )
            )

        return self.storage

    def _compact_symbol(self, symbol_id: str) -> dict[str, Any]:
        symbol = self._symbol_by_id(symbol_id)
        file_id = str(symbol.get("fileId") or "")
        file_item = self.index.file_by_id.get(file_id) or {}
        return {
            "symbolId": symbol_id,
            "kind": symbol.get("type"),
            "name": symbol.get("shortName"),
            "qualifiedName": symbol.get("qualifiedName"),
            "file": file_item.get("relativePath") or symbol.get("relativePath"),
            "startLine": symbol.get("startLine"),
            "endLine": symbol.get("endLine"),
        }


def function_graph_result_to_api(result: FunctionGraphResult) -> dict[str, Any]:
    return {
        "schema": result.schema,
        "status": result.status,
        "fromCache": result.from_cache,
        "symbolId": result.symbol_id,
        "functionName": result.function_name,
        "qualifiedName": result.qualified_name,
        "file": result.file,
        "range": {
            "startLine": result.start_line,
            "endLine": result.end_line,
        },
        "parser": {
            "id": result.parser_id,
            "version": result.parser_version,
        },
        "resolver": {
            "version": result.resolver_version,
        },
        "claimStrength": result.claim_strength,
        "behaviorClaimsAllowed": result.behavior_claims_allowed,
        "fingerprints": {
            "functionBody": result.fingerprints.function_body,
            "file": result.fingerprints.file,
            "symbolIndex": result.fingerprints.symbol_index,
            "moduleVisibility": result.fingerprints.module_visibility,
            "graph": result.fingerprints.graph,
        },
        "edges": [
            {
                "edgeKind": edge.edge_kind,
                "toText": edge.to_text,
                "toSymbolId": edge.to_symbol_id,
                "resolutionStatus": edge.resolution_status,
                "confidence": edge.confidence,
                "basis": list(edge.basis),
                "candidates": list(edge.candidates),
                "claimStrength": edge.claim_strength,
                "behaviorClaimsAllowed": edge.behavior_claims_allowed,
            }
            for edge in result.edges
        ],
    }


def _unique_edge_symbol_ids(edges: tuple[dict[str, Any], ...], *, key: str) -> tuple[str, ...]:
    seen: set[str] = set()
    result: list[str] = []
    for edge in edges:
        value = edge.get(key)
        if not isinstance(value, str) or not value or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return tuple(result)


def _cache_resolver_version(request: FunctionGraphRequest, *, parser_status_hash: str) -> str:
    options = graph_cache_options_for_request(request)
    return (
        f"{RESOLVER_VERSION};options={graph_cache_options_fingerprint(options)}"
        f";parserStatus={parser_status_hash}"
    )
