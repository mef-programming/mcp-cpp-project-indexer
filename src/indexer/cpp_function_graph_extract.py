from __future__ import annotations

import re

from cpp_function_graph_model import FunctionAstExtract


EXTRACTOR_VERSION = "cpp-function-graph-raw-extractor-v0.2"
LIGHTWEIGHT_PARSER_ID = "cpp-lightweight-function-parser"
LIGHTWEIGHT_PARSER_VERSION = "v0.2"

CONTROL_FLOW_WORDS = {
    "if",
    "switch",
    "for",
    "while",
    "return",
    "throw",
    "try",
    "catch",
    "co_await",
    "co_return",
}

CALL_EXCLUDE_WORDS = CONTROL_FLOW_WORDS | {
    "sizeof",
    "alignof",
    "decltype",
    "static_cast",
    "reinterpret_cast",
    "const_cast",
    "dynamic_cast",
}

CALL_RE = re.compile(
    r"(?P<callee>\b[A-Za-z_]\w*(?:(?:::|->|\.)[A-Za-z_]\w*)*)"
    r"(?:\s*<[^;{}()]*>)?\s*\(",
)
CHAINED_RESULT_CALL_RE = re.compile(
    r"(?:\)|\])\s*(?:->|\.)\s*(?P<callee>[A-Za-z_]\w*)\s*\(",
)
CONTROL_RE = re.compile(r"\b(if|switch|for|while|return|throw|try|catch|co_await|co_return)\b")
LOCAL_DECL_RE = re.compile(
    r"^\s*(?P<type>(?:const\s+)?(?:auto|[A-Za-z_]\w*(?:::[A-Za-z_]\w*)?)(?:\s*<[^;=(){}]*>)?(?:\s*[*&])?)\s+"
    r"(?P<name>[A-Za-z_]\w*)\s*(?:[=;({])"
)
MEMBER_TOKEN_RE = re.compile(r"\b(?P<member>this->\w+(?:(?:->|\.)\w+)*|[A-Za-z_]\w*(?:(?:->|\.)\w+)+|_\w+)\b")
ASSIGNMENT_RE = re.compile(r"(?P<lhs>this->\w+(?:(?:->|\.)\w+)*|[A-Za-z_]\w*(?:(?:->|\.)\w+)+|_\w+)\s*=")


def extract_raw_function_ast(
    *,
    symbol_id: str,
    source_fingerprint: str,
    function_text: str,
    base_line: int,
    base_byte: int,
    parser_id: str = LIGHTWEIGHT_PARSER_ID,
    parser_version: str = LIGHTWEIGHT_PARSER_VERSION,
) -> FunctionAstExtract:
    calls: list[dict] = []
    member_accesses: list[dict] = []
    local_declarations: list[dict] = []
    control_flow: list[dict] = []

    byte_cursor = base_byte
    in_body = False
    for offset, line in enumerate(function_text.splitlines(keepends=True)):
        line_number = base_line + offset
        line_text = line.rstrip("\r\n")

        if in_body:
            calls.extend(_extract_calls(line_text, line_number=line_number, byte_cursor=byte_cursor))
            member_accesses.extend(_extract_member_accesses(line_text, line_number=line_number, byte_cursor=byte_cursor))
            local_declarations.extend(_extract_local_declarations(line_text, line_number=line_number, byte_cursor=byte_cursor))
            control_flow.extend(_extract_control_flow(line_text, line_number=line_number, byte_cursor=byte_cursor))

        if "{" in line_text:
            in_body = True

        byte_cursor += len(line.encode("utf-8"))

    return FunctionAstExtract(
        symbol_id=symbol_id,
        source_fingerprint=source_fingerprint,
        parser_id=parser_id,
        parser_version=parser_version,
        extractor_version=EXTRACTOR_VERSION,
        calls=tuple(calls),
        member_accesses=tuple(member_accesses),
        local_declarations=tuple(local_declarations),
        control_flow=tuple(control_flow),
    )


def _extract_calls(line: str, *, line_number: int, byte_cursor: int) -> list[dict]:
    result: list[dict] = []
    stripped = _strip_line_comment(line)
    seen: set[tuple[str, int]] = set()
    for match in CALL_RE.finditer(stripped):
        if match.start("callee") > 0 and stripped[match.start("callee") - 1] in {".", ">"}:
            continue
        callee = match.group("callee")
        tail = callee.rsplit("::", 1)[-1].rsplit("->", 1)[-1].rsplit(".", 1)[-1]
        if tail in CALL_EXCLUDE_WORDS or _looks_like_macro_invocation(tail):
            continue

        seen.add((callee, match.start("callee")))
        result.append(
            {
                "callee": callee,
                "callKind": _call_kind(callee),
                "argumentCount": _argument_count(line, match.end() - 1),
                "line": line_number,
                "column": match.start("callee"),
                "byte": byte_cursor + len(line[:match.start("callee")].encode("utf-8")),
                "kind": "call_expression",
            }
        )

    for match in CHAINED_RESULT_CALL_RE.finditer(stripped):
        callee = match.group("callee")
        if callee in CALL_EXCLUDE_WORDS or _looks_like_macro_invocation(callee):
            continue
        key = (callee, match.start("callee"))
        if key in seen:
            continue
        result.append(
            {
                "callee": callee,
                "callKind": "member",
                "argumentCount": _argument_count(stripped, match.end() - 1),
                "line": line_number,
                "column": match.start("callee"),
                "byte": byte_cursor + len(line[:match.start("callee")].encode("utf-8")),
                "kind": "call_expression",
            }
        )

    return result


def _extract_member_accesses(line: str, *, line_number: int, byte_cursor: int) -> list[dict]:
    stripped = _strip_line_comment(line)
    writes = {
        match.group("lhs"): match
        for match in ASSIGNMENT_RE.finditer(stripped)
    }
    result: list[dict] = []
    seen: set[tuple[str, int]] = set()

    for match in MEMBER_TOKEN_RE.finditer(stripped):
        text = match.group("member")
        key = (text, match.start("member"))
        if key in seen:
            continue
        seen.add(key)
        result.append(
            {
                "text": text,
                "accessKind": "write_candidate" if text in writes else "read_candidate",
                "line": line_number,
                "column": match.start("member"),
                "byte": byte_cursor + len(line[:match.start("member")].encode("utf-8")),
                "kind": "member_access",
            }
        )

    return result


def _extract_local_declarations(line: str, *, line_number: int, byte_cursor: int) -> list[dict]:
    stripped = _strip_line_comment(line)
    match = LOCAL_DECL_RE.match(stripped)
    if match is None:
        return []

    type_text = match.group("type").strip()
    if type_text in CONTROL_FLOW_WORDS:
        return []

    name = match.group("name")
    name_column = stripped.find(name)
    return [
        {
            "name": name,
            "typeText": type_text,
            "line": line_number,
            "column": name_column,
            "byte": byte_cursor + len(line[:name_column].encode("utf-8")),
            "kind": "local_declaration",
        }
    ]


def _extract_control_flow(line: str, *, line_number: int, byte_cursor: int) -> list[dict]:
    result: list[dict] = []
    for match in CONTROL_RE.finditer(_strip_line_comment(line)):
        result.append(
            {
                "marker": match.group(1),
                "line": line_number,
                "column": match.start(1),
                "byte": byte_cursor + len(line[:match.start(1)].encode("utf-8")),
                "kind": "control_flow_marker",
            }
        )
    return result


def _call_kind(callee: str) -> str:
    if "->" in callee or "." in callee:
        return "member"
    if "::" in callee:
        return "qualified"
    return "unqualified"


def _argument_count(line: str, open_paren_index: int) -> int:
    close_index = _find_close_paren(line, open_paren_index)
    if close_index is None:
        return 0

    text = line[open_paren_index + 1:close_index].strip()
    if not text:
        return 0

    depth = 0
    count = 1
    for char in text:
        if char in "([{":
            depth += 1
        elif char in ")]}" and depth > 0:
            depth -= 1
        elif char == "," and depth == 0:
            count += 1
    return count


def _find_close_paren(line: str, open_paren_index: int) -> int | None:
    depth = 0
    for index in range(open_paren_index, len(line)):
        char = line[index]
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return index
    return None


def _strip_line_comment(line: str) -> str:
    index = line.find("//")
    return line if index < 0 else line[:index]


def _looks_like_macro_invocation(name: str) -> bool:
    return name.isupper() or name.startswith(("ASSERT_", "VERIFY_", "TRACE_"))


class LightweightFunctionBodyParser:
    parser_id = LIGHTWEIGHT_PARSER_ID
    parser_version = LIGHTWEIGHT_PARSER_VERSION

    def parse_function(
        self,
        *,
        symbol_id: str,
        source_fingerprint: str,
        function_text: str,
        base_line: int,
        base_byte: int,
    ) -> FunctionAstExtract:
        return extract_raw_function_ast(
            symbol_id=symbol_id,
            source_fingerprint=source_fingerprint,
            function_text=function_text,
            base_line=base_line,
            base_byte=base_byte,
            parser_id=self.parser_id,
            parser_version=self.parser_version,
        )
