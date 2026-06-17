from __future__ import annotations

import json
import sqlite3

from dataclasses import asdict
from pathlib import Path
from typing import Any

from cpp_function_graph_cache import FunctionAstCacheKey, FunctionGraphCacheKey
from cpp_function_graph_model import (
    FunctionAstExtract,
    FunctionGraphEdge,
    FunctionGraphFingerprints,
    FunctionGraphResult,
)
from cpp_index_sqlite import connect_index_db, sqlite_index_path


FUNCTION_GRAPH_STORAGE_SCHEMA = "cpp.function_graph.sqlite.v0.1"


def function_graph_db_path(index_root: Path) -> Path:
    return sqlite_index_path(index_root)


def initialize_function_graph_storage(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS function_ast_extract_cache (
            function_symbol_id TEXT NOT NULL,
            function_body_fingerprint TEXT NOT NULL,
            parser_id TEXT NOT NULL,
            parser_version TEXT NOT NULL,
            extractor_version TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY(
                function_symbol_id,
                function_body_fingerprint,
                parser_id,
                parser_version,
                extractor_version
            )
        );

        CREATE TABLE IF NOT EXISTS function_graph_cache (
            function_symbol_id TEXT NOT NULL,
            graph_fingerprint TEXT NOT NULL,
            function_body_fingerprint TEXT NOT NULL,
            file_fingerprint TEXT NOT NULL,
            symbol_index_fingerprint TEXT NOT NULL,
            module_visibility_fingerprint TEXT NOT NULL,
            parser_id TEXT NOT NULL,
            parser_version TEXT NOT NULL,
            resolver_version TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY(function_symbol_id, graph_fingerprint)
        );

        CREATE TABLE IF NOT EXISTS function_graph_edges (
            graph_fingerprint TEXT NOT NULL,
            from_symbol_id TEXT NOT NULL,
            to_symbol_id TEXT,
            to_text TEXT NOT NULL,
            edge_kind TEXT NOT NULL,
            resolution_status TEXT NOT NULL,
            confidence REAL NOT NULL,
            claim_strength TEXT NOT NULL,
            behavior_claims_allowed INTEGER NOT NULL,
            basis_json TEXT NOT NULL,
            evidence_json TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_function_graph_cache_lookup
            ON function_graph_cache(
                function_symbol_id,
                function_body_fingerprint,
                file_fingerprint,
                symbol_index_fingerprint,
                module_visibility_fingerprint,
                parser_id,
                parser_version,
                resolver_version
            );

        CREATE INDEX IF NOT EXISTS idx_function_graph_edges_from
            ON function_graph_edges(from_symbol_id);

        CREATE INDEX IF NOT EXISTS idx_function_graph_edges_to
            ON function_graph_edges(to_symbol_id);
        """
    )


class FunctionGraphStorage:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    @classmethod
    def from_index_root(cls, index_root: Path) -> "FunctionGraphStorage":
        return cls(function_graph_db_path(index_root))

    def connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        connection = connect_index_db(self.db_path)
        initialize_function_graph_storage(connection)
        return connection

    def store_ast_extract(self, key: FunctionAstCacheKey, extract: FunctionAstExtract) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO function_ast_extract_cache(
                    function_symbol_id,
                    function_body_fingerprint,
                    parser_id,
                    parser_version,
                    extractor_version,
                    payload_json
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    key.function_symbol_id,
                    key.function_body_fingerprint,
                    key.parser_id,
                    key.parser_version,
                    key.extractor_version,
                    _json_dump(asdict(extract)),
                ),
            )

    def load_ast_extract(self, key: FunctionAstCacheKey) -> FunctionAstExtract | None:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT payload_json
                FROM function_ast_extract_cache
                WHERE function_symbol_id = ?
                  AND function_body_fingerprint = ?
                  AND parser_id = ?
                  AND parser_version = ?
                  AND extractor_version = ?
                """,
                (
                    key.function_symbol_id,
                    key.function_body_fingerprint,
                    key.parser_id,
                    key.parser_version,
                    key.extractor_version,
                ),
            ).fetchone()

        if row is None:
            return None

        return _ast_extract_from_payload(json.loads(str(row["payload_json"])))

    def store_graph_result(self, key: FunctionGraphCacheKey, result: FunctionGraphResult) -> None:
        graph_fingerprint = result.fingerprints.graph
        with self.connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO function_graph_cache(
                    function_symbol_id,
                    graph_fingerprint,
                    function_body_fingerprint,
                    file_fingerprint,
                    symbol_index_fingerprint,
                    module_visibility_fingerprint,
                    parser_id,
                    parser_version,
                    resolver_version,
                    payload_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    key.function_symbol_id,
                    graph_fingerprint,
                    key.function_body_fingerprint,
                    key.file_fingerprint,
                    key.symbol_index_fingerprint,
                    key.module_visibility_fingerprint,
                    key.parser_id,
                    key.parser_version,
                    key.resolver_version,
                    _json_dump(asdict(result)),
                ),
            )
            connection.execute(
                "DELETE FROM function_graph_edges WHERE from_symbol_id = ?",
                (key.function_symbol_id,),
            )
            connection.executemany(
                """
                INSERT INTO function_graph_edges(
                    graph_fingerprint,
                    from_symbol_id,
                    to_symbol_id,
                    to_text,
                    edge_kind,
                    resolution_status,
                    confidence,
                    claim_strength,
                    behavior_claims_allowed,
                    basis_json,
                    evidence_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        graph_fingerprint,
                        edge.from_symbol_id,
                        edge.to_symbol_id,
                        edge.to_text,
                        edge.edge_kind,
                        edge.resolution_status,
                        float(edge.confidence),
                        edge.claim_strength,
                        1 if edge.behavior_claims_allowed else 0,
                        _json_dump(list(edge.basis)),
                        _json_dump({"candidates": list(edge.candidates)}),
                    )
                    for edge in result.edges
                ],
            )

    def load_graph_result(self, key: FunctionGraphCacheKey) -> FunctionGraphResult | None:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT payload_json
                FROM function_graph_cache
                WHERE function_symbol_id = ?
                  AND function_body_fingerprint = ?
                  AND file_fingerprint = ?
                  AND symbol_index_fingerprint = ?
                  AND module_visibility_fingerprint = ?
                  AND parser_id = ?
                  AND parser_version = ?
                  AND resolver_version = ?
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (
                    key.function_symbol_id,
                    key.function_body_fingerprint,
                    key.file_fingerprint,
                    key.symbol_index_fingerprint,
                    key.module_visibility_fingerprint,
                    key.parser_id,
                    key.parser_version,
                    key.resolver_version,
                ),
            ).fetchone()

        if row is None:
            return None

        return _graph_result_from_payload(json.loads(str(row["payload_json"])))

    def list_edges_from(self, symbol_id: str, *, limit: int = 200) -> tuple[dict[str, Any], ...]:
        return self._list_edges("from_symbol_id", symbol_id, limit=limit)

    def list_edges_to(self, symbol_id: str, *, limit: int = 200) -> tuple[dict[str, Any], ...]:
        return self._list_edges("to_symbol_id", symbol_id, limit=limit)

    def _list_edges(self, column: str, symbol_id: str, *, limit: int) -> tuple[dict[str, Any], ...]:
        if column not in {"from_symbol_id", "to_symbol_id"}:
            raise ValueError("Invalid edge lookup column.")

        safe_limit = max(0, min(int(limit), 1000))
        with self.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT *
                FROM function_graph_edges
                WHERE {column} = ?
                ORDER BY graph_fingerprint, edge_kind, to_text
                LIMIT ?
                """,
                (symbol_id, safe_limit),
            ).fetchall()

        return tuple(_edge_row_to_dict(row) for row in rows)


def _ast_extract_from_payload(payload: dict[str, Any]) -> FunctionAstExtract:
    return FunctionAstExtract(
        symbol_id=str(payload["symbol_id"]),
        source_fingerprint=str(payload["source_fingerprint"]),
        parser_id=str(payload["parser_id"]),
        parser_version=str(payload["parser_version"]),
        extractor_version=str(payload["extractor_version"]),
        calls=tuple(payload.get("calls") or ()),
        member_accesses=tuple(payload.get("member_accesses") or ()),
        local_declarations=tuple(payload.get("local_declarations") or ()),
        control_flow=tuple(payload.get("control_flow") or ()),
    )


def _graph_result_from_payload(payload: dict[str, Any]) -> FunctionGraphResult:
    fingerprints_payload = payload["fingerprints"]
    return FunctionGraphResult(
        schema=str(payload["schema"]),
        status=str(payload["status"]),
        from_cache=bool(payload["from_cache"]),
        symbol_id=str(payload["symbol_id"]),
        function_name=payload.get("function_name"),
        qualified_name=payload.get("qualified_name"),
        file=str(payload["file"]),
        start_line=int(payload["start_line"]),
        end_line=int(payload["end_line"]),
        parser_id=payload.get("parser_id"),
        parser_version=payload.get("parser_version"),
        resolver_version=payload.get("resolver_version"),
        claim_strength=str(payload["claim_strength"]),
        behavior_claims_allowed=bool(payload["behavior_claims_allowed"]),
        fingerprints=FunctionGraphFingerprints(
            function_body=str(fingerprints_payload["function_body"]),
            graph=str(fingerprints_payload["graph"]),
            file=fingerprints_payload.get("file"),
            symbol_index=fingerprints_payload.get("symbol_index"),
            module_visibility=fingerprints_payload.get("module_visibility"),
        ),
        edges=tuple(_edge_from_payload(item) for item in payload.get("edges") or ()),
    )


def _edge_from_payload(payload: dict[str, Any]) -> FunctionGraphEdge:
    return FunctionGraphEdge(
        from_symbol_id=str(payload["from_symbol_id"]),
        edge_kind=payload["edge_kind"],
        to_text=str(payload["to_text"]),
        to_symbol_id=payload.get("to_symbol_id"),
        resolution_status=payload["resolution_status"],
        confidence=float(payload["confidence"]),
        basis=tuple(payload.get("basis") or ()),
        candidates=tuple(payload.get("candidates") or ()),
        claim_strength=str(payload["claim_strength"]),
        behavior_claims_allowed=bool(payload["behavior_claims_allowed"]),
    )


def _edge_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    evidence = json.loads(str(row["evidence_json"] or "{}"))
    return {
        "graphFingerprint": row["graph_fingerprint"],
        "fromSymbolId": row["from_symbol_id"],
        "toSymbolId": row["to_symbol_id"],
        "toText": row["to_text"],
        "edgeKind": row["edge_kind"],
        "resolutionStatus": row["resolution_status"],
        "confidence": float(row["confidence"]),
        "claimStrength": row["claim_strength"],
        "behaviorClaimsAllowed": bool(row["behavior_claims_allowed"]),
        "basis": json.loads(str(row["basis_json"] or "[]")),
        "evidence": evidence,
    }


def _json_dump(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
