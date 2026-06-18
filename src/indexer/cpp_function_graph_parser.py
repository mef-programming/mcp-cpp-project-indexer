from __future__ import annotations

import hashlib
import json

from dataclasses import asdict, dataclass
from typing import Any
from typing import Protocol

from cpp_function_graph_model import FunctionAstExtract


@dataclass(frozen=True, slots=True)
class ParserCapabilityStatus:
    parser_id: str
    parser_version: str
    available: bool
    reason: str
    capabilities: tuple[str, ...]
    dependency_status: dict[str, Any] | None = None


class FunctionBodyParser(Protocol):
    parser_id: str
    parser_version: str

    def parser_status(self) -> ParserCapabilityStatus:
        ...

    def parse_function(
        self,
        *,
        symbol_id: str,
        source_fingerprint: str,
        function_text: str,
        base_line: int,
        base_byte: int,
    ) -> FunctionAstExtract:
        ...


def parser_capability_status(parser: Any) -> ParserCapabilityStatus:
    status_fn = getattr(parser, "parser_status", None)
    if callable(status_fn):
        status = status_fn()
        if isinstance(status, ParserCapabilityStatus):
            return status

    return ParserCapabilityStatus(
        parser_id=str(getattr(parser, "parser_id", "unknown")),
        parser_version=str(getattr(parser, "parser_version", "unknown")),
        available=True,
        reason="legacy_parser_without_status",
        capabilities=(),
    )


def parser_status_payload(status: ParserCapabilityStatus) -> dict[str, Any]:
    payload = asdict(status)
    payload["capabilities"] = sorted(status.capabilities)
    return payload


def parser_status_fingerprint(status: ParserCapabilityStatus) -> str:
    text = json.dumps(
        parser_status_payload(status),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def parser_cache_version(parser: Any) -> str:
    status = parser_capability_status(parser)
    return f"{status.parser_version};status={parser_status_fingerprint(status)}"
