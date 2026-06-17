from __future__ import annotations

from typing import Protocol

from cpp_function_graph_model import FunctionAstExtract


class FunctionBodyParser(Protocol):
    parser_id: str
    parser_version: str

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
