from __future__ import annotations

import importlib.util

from cpp_function_graph_model import FunctionAstExtract
from cpp_function_graph_extract import EXTRACTOR_VERSION, extract_raw_function_ast
from cpp_function_graph_parser import ParserCapabilityStatus


class TreeSitterUnavailableError(RuntimeError):
    pass


def tree_sitter_cpp_dependency_status() -> dict[str, str | bool]:
    """Return dependency status without importing optional packages eagerly."""

    missing = [
        package
        for package in ("tree_sitter", "tree_sitter_cpp")
        if importlib.util.find_spec(package) is None
    ]
    if missing:
        return {
            "available": False,
            "reason": "missing_optional_dependency",
            "packages": ",".join(missing),
        }

    return {
        "available": True,
        "reason": "dependency_present_adapter_not_enabled",
        "packages": "tree_sitter,tree_sitter_cpp",
    }


class TreeSitterCppFunctionBodyParser:
    parser_id = "tree-sitter-cpp"
    parser_version = "optional-parity-spike-v0.1"

    def __init__(self) -> None:
        status = tree_sitter_cpp_dependency_status()
        if not status["available"]:
            raise TreeSitterUnavailableError(
                "Tree-sitter C++ optional dependencies are not configured for this project: "
                f"{status['packages']}. Use LightweightFunctionBodyParser fallback."
            )

    def parser_status(self) -> ParserCapabilityStatus:
        return ParserCapabilityStatus(
            parser_id=self.parser_id,
            parser_version=self.parser_version,
            available=True,
            reason="dependency_present_parity_spike",
            capabilities=(
                "tree_sitter_parse_probe",
                "calls",
                "member_accesses",
                "local_declarations",
                "control_flow_markers",
                "normalized_lightweight_extract",
            ),
            dependency_status=tree_sitter_cpp_dependency_status(),
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
        self._probe_parse(function_text)
        return extract_raw_function_ast(
            symbol_id=symbol_id,
            source_fingerprint=source_fingerprint,
            function_text=function_text,
            base_line=base_line,
            base_byte=base_byte,
            parser_id=self.parser_id,
            parser_version=self.parser_version,
        )

    def _probe_parse(self, function_text: str) -> None:
        try:
            from tree_sitter import Language, Parser
            import tree_sitter_cpp

            language_ref = tree_sitter_cpp.language()
            language = Language(language_ref)
            parser = Parser(language)
            tree = parser.parse(function_text.encode("utf-8"))
        except Exception as exc:  # pragma: no cover - depends on optional package API
            raise TreeSitterUnavailableError(
                "Tree-sitter C++ dependencies are present, but the adapter could not "
                f"create a parse tree: {exc}"
            ) from exc

        if tree is None or tree.root_node is None:
            raise TreeSitterUnavailableError("Tree-sitter C++ parser returned no parse tree.")
