from __future__ import annotations

import importlib.util
import re
from typing import Any

from cpp_function_graph_extract import (
    CALL_EXCLUDE_WORDS,
    EXTRACTOR_VERSION,
)
from cpp_function_graph_model import FunctionAstExtract
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
        "reason": "dependency_present_ast_adapter_available",
        "packages": "tree_sitter,tree_sitter_cpp",
    }


class TreeSitterCppFunctionBodyParser:
    parser_id = "tree-sitter-cpp"
    parser_version = "ast-extractor-v0.1"

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
            reason="dependency_present_ast_extractor",
            capabilities=(
                "calls",
                "qualified_calls",
                "member_calls",
                "member_accesses",
                "local_declarations",
                "control_flow_markers",
                "macro_noise_filter",
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
        tree = self._parse_tree(function_text)
        source_bytes = function_text.encode("utf-8")
        extractor = _TreeSitterFunctionAstExtractor(
            source_bytes=source_bytes,
            base_line=base_line,
            base_byte=base_byte,
        )
        return FunctionAstExtract(
            symbol_id=symbol_id,
            source_fingerprint=source_fingerprint,
            parser_id=self.parser_id,
            parser_version=self.parser_version,
            extractor_version=EXTRACTOR_VERSION,
            calls=tuple(extractor.extract_calls(tree.root_node)),
            member_accesses=tuple(extractor.extract_member_accesses(tree.root_node)),
            local_declarations=tuple(extractor.extract_local_declarations(tree.root_node)),
            control_flow=tuple(extractor.extract_control_flow(tree.root_node)),
        )

    def _parse_tree(self, function_text: str) -> Any:
        try:
            from tree_sitter import Language, Parser
            import tree_sitter_cpp

            language_ref = tree_sitter_cpp.language()
            language = Language(language_ref)
            parser = _new_parser(language)
            tree = parser.parse(function_text.encode("utf-8"))
        except Exception as exc:  # pragma: no cover - depends on optional package API
            raise TreeSitterUnavailableError(
                "Tree-sitter C++ dependencies are present, but the adapter could not "
                f"create a parse tree: {exc}"
            ) from exc

        if tree is None or tree.root_node is None:
            raise TreeSitterUnavailableError("Tree-sitter C++ parser returned no parse tree.")

        return tree


class _TreeSitterFunctionAstExtractor:
    def __init__(self, *, source_bytes: bytes, base_line: int, base_byte: int) -> None:
        self.source_bytes = source_bytes
        self.base_line = base_line
        self.base_byte = base_byte

    def extract_calls(self, root: Any) -> list[dict]:
        result: list[dict] = []
        seen: set[tuple[str, int]] = set()
        for node in _walk(root):
            if node.type != "call_expression":
                continue

            function_node = _child_by_field_name(node, "function")
            if function_node is None or _has_macro_call_ancestor(node, self.source_bytes):
                continue
            if function_node.type not in _SUPPORTED_CALL_FUNCTION_NODE_TYPES:
                continue

            callee = _normalise_callee(_node_text(function_node, self.source_bytes))
            if not callee:
                continue
            tail = re.split(r"::|->|\.", callee)[-1]
            if tail in CALL_EXCLUDE_WORDS or _looks_like_macro_invocation(tail):
                continue

            location = self._location(function_node)
            key = (callee, int(location["byte"]))
            if key in seen:
                continue
            seen.add(key)
            result.append(
                {
                    "callee": callee,
                    "callKind": _call_kind(callee),
                    "argumentCount": _argument_count(node),
                    "line": location["line"],
                    "column": location["column"],
                    "byte": location["byte"],
                    "kind": "call_expression",
                }
            )
        return result

    def extract_member_accesses(self, root: Any) -> list[dict]:
        result: list[dict] = []
        seen: set[tuple[str, int]] = set()
        for node in _walk(root):
            if node.type != "field_expression":
                continue

            text = _normalise_member_text(_node_text(node, self.source_bytes))
            if not text:
                continue

            location = self._location(node)
            key = (text, int(location["byte"]))
            if key in seen:
                continue
            seen.add(key)
            result.append(
                {
                    "text": text,
                    "accessKind": "write_candidate" if _is_assignment_lhs(node) else "read_candidate",
                    "line": location["line"],
                    "column": location["column"],
                    "byte": location["byte"],
                    "kind": "member_access",
                }
            )
        return result

    def extract_local_declarations(self, root: Any) -> list[dict]:
        result: list[dict] = []
        seen: set[tuple[str, int]] = set()
        for node in _walk(root):
            if node.type != "declaration" or not _has_ancestor_type(node, "compound_statement"):
                continue

            item = self._local_declaration_from_node(node)
            if item is None:
                continue
            key = (str(item["name"]), int(item["byte"]))
            if key in seen:
                continue
            seen.add(key)
            result.append(item)
        return result

    def extract_control_flow(self, root: Any) -> list[dict]:
        result: list[dict] = []
        for node in _walk(root):
            marker = _CONTROL_FLOW_NODE_MARKERS.get(node.type)
            if marker is None:
                continue

            location = self._location(node)
            result.append(
                {
                    "marker": marker,
                    "line": location["line"],
                    "column": location["column"],
                    "byte": location["byte"],
                    "kind": "control_flow_marker",
                }
            )
        return result

    def _local_declaration_from_node(self, node: Any) -> dict | None:
        declarator = _child_by_field_name(node, "declarator")
        if declarator is None:
            declarator = _find_first_descendant_type(node, {"init_declarator"})
        if declarator is None:
            return None

        name_node = _child_by_field_name(declarator, "declarator")
        if name_node is None and declarator.type == "identifier":
            name_node = declarator
        if name_node is None:
            name_node = _find_last_descendant_type(declarator, {"identifier"})
        if name_node is None:
            return None

        name = _node_text(name_node, self.source_bytes).strip()
        if not _IDENTIFIER_RE.fullmatch(name):
            return None

        declaration_text = _node_text(node, self.source_bytes)
        prefix = declaration_text.split(name, 1)[0].strip()
        type_text = _normalise_type_text(prefix)
        if not type_text:
            return None

        location = self._location(name_node)
        return {
            "name": name,
            "typeText": type_text,
            "line": location["line"],
            "column": location["column"],
            "byte": location["byte"],
            "kind": "local_declaration",
        }

    def _location(self, node: Any) -> dict[str, int]:
        row, column = node.start_point
        return {
            "line": self.base_line + int(row),
            "column": int(column),
            "byte": self.base_byte + int(node.start_byte),
        }


_CONTROL_FLOW_NODE_MARKERS = {
    "if_statement": "if",
    "switch_statement": "switch",
    "for_statement": "for",
    "for_range_loop": "for",
    "while_statement": "while",
    "return_statement": "return",
    "throw_statement": "throw",
    "try_statement": "try",
    "catch_clause": "catch",
    "co_return_statement": "co_return",
    "co_await_expression": "co_await",
}
_IDENTIFIER_RE = re.compile(r"[A-Za-z_]\w*")
_SUPPORTED_CALL_FUNCTION_NODE_TYPES = {
    "identifier",
    "field_expression",
    "qualified_identifier",
    "template_function",
}


def _new_parser(language: Any) -> Any:
    from tree_sitter import Parser

    try:
        return Parser(language)
    except TypeError:
        parser = Parser()
        parser.set_language(language)
        return parser


def _walk(root: Any) -> list[Any]:
    result: list[Any] = []
    stack = [root]
    while stack:
        node = stack.pop()
        result.append(node)
        stack.extend(reversed(list(getattr(node, "children", ()))))
    return result


def _child_by_field_name(node: Any, field_name: str) -> Any | None:
    try:
        return node.child_by_field_name(field_name)
    except Exception:
        return None


def _node_text(node: Any, source_bytes: bytes) -> str:
    return source_bytes[int(node.start_byte):int(node.end_byte)].decode("utf-8", errors="replace")


def _normalise_callee(text: str) -> str:
    callee = re.sub(r"\s+", "", text.strip())
    if not callee:
        return ""
    callee = callee.removeprefix("::")
    return _strip_trailing_template_args(callee)


def _normalise_member_text(text: str) -> str:
    return re.sub(r"\s+", "", text.strip())


def _normalise_type_text(text: str) -> str:
    text = re.sub(r"\s+", " ", text.replace("\n", " ")).strip()
    text = text.removesuffix("=").strip()
    return text


def _strip_trailing_template_args(text: str) -> str:
    if not text.endswith(">"):
        return text

    depth = 0
    for index in range(len(text) - 1, -1, -1):
        char = text[index]
        if char == ">":
            depth += 1
        elif char == "<":
            depth -= 1
            if depth == 0:
                return text[:index]
    return text


def _argument_count(call_node: Any) -> int:
    args = _child_by_field_name(call_node, "arguments")
    if args is None:
        return 0
    return sum(1 for child in getattr(args, "named_children", ()) if child.type != "comment")


def _call_kind(callee: str) -> str:
    if "->" in callee or "." in callee:
        return "member"
    if "::" in callee:
        return "qualified"
    return "unqualified"


def _looks_like_macro_invocation(name: str) -> bool:
    return name.isupper() or name.startswith(("ASSERT_", "VERIFY_", "TRACE_"))


def _has_macro_call_ancestor(node: Any, source_bytes: bytes) -> bool:
    current = getattr(node, "parent", None)
    while current is not None:
        if current.type == "call_expression":
            function_node = _child_by_field_name(current, "function")
            if function_node is not None:
                tail = re.split(r"::|->|\.", _normalise_callee(_node_text(function_node, source_bytes)))[-1]
                if _looks_like_macro_invocation(tail):
                    return True
        current = getattr(current, "parent", None)
    return False


def _has_ancestor_type(node: Any, node_type: str) -> bool:
    current = getattr(node, "parent", None)
    while current is not None:
        if current.type == node_type:
            return True
        current = getattr(current, "parent", None)
    return False


def _find_first_descendant_type(node: Any, node_types: set[str]) -> Any | None:
    for child in _walk(node):
        if child is not node and child.type in node_types:
            return child
    return None


def _find_last_descendant_type(node: Any, node_types: set[str]) -> Any | None:
    result = None
    for child in _walk(node):
        if child is not node and child.type in node_types:
            result = child
    return result


def _is_assignment_lhs(node: Any) -> bool:
    parent = getattr(node, "parent", None)
    if parent is None or parent.type not in {"assignment_expression", "update_expression"}:
        return False

    left = _child_by_field_name(parent, "left")
    return left is node
