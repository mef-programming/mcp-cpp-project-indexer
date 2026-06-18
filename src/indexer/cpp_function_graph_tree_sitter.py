from __future__ import annotations

import importlib.util

from cpp_function_graph_model import FunctionAstExtract


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
    parser_version = "optional-gated"

    def __init__(self) -> None:
        status = tree_sitter_cpp_dependency_status()
        if status["available"]:
            raise TreeSitterUnavailableError(
                "Tree-sitter C++ dependencies are present, but the function graph "
                "Tree-sitter extraction adapter is not enabled yet. Use the "
                "lightweight parser fallback until the adapter has extraction parity."
            )

        raise TreeSitterUnavailableError(
            "Tree-sitter C++ optional dependencies are not configured for this project: "
            f"{status['packages']}. Use LightweightFunctionBodyParser fallback."
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
