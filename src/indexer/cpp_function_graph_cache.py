from __future__ import annotations

import hashlib
import json

from dataclasses import dataclass
from typing import Any

from cpp_function_graph_model import FunctionAstExtract, FunctionGraphResult


@dataclass(frozen=True, slots=True)
class FunctionAstCacheKey:
    function_symbol_id: str
    function_body_fingerprint: str
    parser_id: str
    parser_version: str
    extractor_version: str


@dataclass(frozen=True, slots=True)
class FunctionGraphCacheKey:
    function_symbol_id: str
    function_body_fingerprint: str
    file_fingerprint: str
    symbol_index_fingerprint: str
    module_visibility_fingerprint: str
    parser_id: str
    parser_version: str
    resolver_version: str


def stable_json_fingerprint(payload: Any) -> str:
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def ast_cache_key_for_extract(extract: FunctionAstExtract) -> FunctionAstCacheKey:
    return FunctionAstCacheKey(
        function_symbol_id=extract.symbol_id,
        function_body_fingerprint=extract.source_fingerprint,
        parser_id=extract.parser_id,
        parser_version=extract.parser_version,
        extractor_version=extract.extractor_version,
    )


def graph_cache_key_for_result(
    result: FunctionGraphResult,
    *,
    file_fingerprint: str,
    symbol_index_fingerprint: str,
    module_visibility_fingerprint: str,
) -> FunctionGraphCacheKey:
    return FunctionGraphCacheKey(
        function_symbol_id=result.symbol_id,
        function_body_fingerprint=result.fingerprints.function_body,
        file_fingerprint=file_fingerprint,
        symbol_index_fingerprint=symbol_index_fingerprint,
        module_visibility_fingerprint=module_visibility_fingerprint,
        parser_id=result.parser_id or "",
        parser_version=result.parser_version or "",
        resolver_version=result.resolver_version or "",
    )
