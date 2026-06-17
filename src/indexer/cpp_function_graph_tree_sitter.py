from __future__ import annotations

from cpp_function_graph_model import FunctionAstExtract


class TreeSitterUnavailableError(RuntimeError):
    pass


class TreeSitterCppFunctionBodyParser:
    parser_id = "tree-sitter-cpp"
    parser_version = "unavailable"

    def __init__(self) -> None:
        raise TreeSitterUnavailableError(
            "Tree-sitter C++ is not configured for this project yet. "
            "Use LightweightFunctionBodyParser for tests until the dependency is added."
        )

    def parse_function(
        self,
        *,
        symbol_id: str,
        source_fingerprint: str,
        function_text: str,
        base_line: int,
        base_byte: int,
    ) -> FunctionAstExtract:
        raise TreeSitterUnavailableError("Tree-sitter C++ parser is not available.")
