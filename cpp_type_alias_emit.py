from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

from cpp_index_model import Token
from cpp_index_utils import normalize_signature_spacing
from cpp_lexer import split_top_level_commas, tokenize_lines, token_values, tokens_to_text


TYPE_ALIAS_DECLARATION_SCHEMA = "cpp.type_alias_declarations.v1"


FUNCTION_EVENT_KINDS = {
    "function",
    "method",
    "constructor",
    "destructor",
    "operator",
}

SCOPE_EVENT_KINDS = {
    "namespace",
    "class",
    "struct",
    "enum",
}

ACCESS_LABELS = {
    "public",
    "protected",
    "private",
}

TYPE_ALIAS_SKIP_AFTER_USING = {
    "namespace",
    "enum",
}


@dataclass(slots=True)
class ScopeInfo:
    kind: str
    name: str
    qualified_name: str
    open_line: int
    close_line: int


@dataclass(slots=True)
class TypeAliasCandidate:
    symbol_type: str
    container: str
    short_name: str
    qualified_name: str
    start_line: int
    end_line: int
    signature: str
    exported: bool
    module_fragment: str


# ---------------------------------------------------------------------------
# Generic event access helpers
# ---------------------------------------------------------------------------

def _get_value(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(name, default)

    return getattr(obj, name, default)


def _event_kind(event: Any) -> str:
    return str(_get_value(event, "kind", ""))


def _event_name(event: Any) -> str:
    return str(_get_value(event, "name", ""))


def _event_qualified_name(event: Any) -> str:
    return str(_get_value(event, "qualified_name", _get_value(event, "qualifiedName", "")))


def _event_open_line(event: Any) -> int:
    return int(_get_value(event, "open_brace_line", _get_value(event, "openBraceLine", 0)) or 0)


def _event_close_line(event: Any) -> int | None:
    value = _get_value(event, "close_line", _get_value(event, "closeLine", None))

    if value is None:
        return None

    return int(value)


def _event_signature(event: Any) -> str:
    return str(_get_value(event, "signature", ""))


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def make_alias_symbol_id(*, file_id: str, start_line: int, end_line: int, signature: str) -> str:
    return f"s_{file_id}_{start_line:06d}_{end_line:06d}_{_hash_text(signature)[:12]}"


def source_range_text(lines: list[str], start_line: int, end_line: int) -> str:
    start_line = max(1, start_line)
    end_line = min(len(lines), end_line)

    if start_line > end_line:
        return ""

    return normalize_signature_spacing(
        " ".join(lines[line_no - 1].strip() for line_no in range(start_line, end_line + 1))
    )


def strip_trailing_semicolon(tokens: list[Token]) -> list[Token]:
    if tokens and tokens[-1].value == ";":
        return tokens[:-1]

    return tokens


def strip_access_label_prefix(tokens: list[Token]) -> list[Token]:
    for index in range(0, len(tokens) - 1):
        if tokens[index].value in ACCESS_LABELS and tokens[index + 1].value == ":":
            return tokens[index + 2:]

    return tokens


def strip_export_prefix(tokens: list[Token]) -> tuple[bool, list[Token]]:
    if tokens and tokens[0].value == "export":
        return True, tokens[1:]

    return False, tokens


def strip_template_prefix(tokens: list[Token]) -> tuple[list[Token], list[Token]]:
    if not tokens or tokens[0].value != "template":
        return [], tokens

    if len(tokens) < 2 or tokens[1].value != "<":
        return [], tokens

    depth = 0

    for index in range(1, len(tokens)):
        value = tokens[index].value

        if value == "<":
            depth += 1
        elif value == ">":
            depth -= 1

            if depth == 0:
                return tokens[: index + 1], tokens[index + 1:]

    return [], tokens


def qualified_name(container: str, name: str) -> str:
    if not container:
        return name

    return f"{container}::{name}"


def first_top_level_index(tokens: list[Token], value: str) -> int | None:
    paren_depth = 0
    bracket_depth = 0
    brace_depth = 0
    angle_depth = 0

    for index, token in enumerate(tokens):
        token_value = token.value

        if (
            token_value == value
            and paren_depth == 0
            and bracket_depth == 0
            and brace_depth == 0
            and angle_depth == 0
        ):
            return index

        if token_value == "(":
            paren_depth += 1
        elif token_value == ")":
            paren_depth = max(0, paren_depth - 1)
        elif token_value == "[":
            bracket_depth += 1
        elif token_value == "]":
            bracket_depth = max(0, bracket_depth - 1)
        elif token_value == "{":
            brace_depth += 1
        elif token_value == "}":
            brace_depth = max(0, brace_depth - 1)
        elif token_value == "<":
            angle_depth += 1
        elif token_value == ">":
            angle_depth = max(0, angle_depth - 1)

    return None


# ---------------------------------------------------------------------------
# Scope/function body helpers
# ---------------------------------------------------------------------------

def function_line_ranges(events: list[Any]) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []

    for event in events:
        if _event_kind(event) not in FUNCTION_EVENT_KINDS:
            continue

        open_line = _event_open_line(event)
        close_line = _event_close_line(event)

        if open_line and close_line:
            ranges.append((open_line, close_line))

    return ranges


def line_in_ranges(line_no: int, ranges: list[tuple[int, int]]) -> bool:
    return any(start <= line_no <= end for start, end in ranges)


def scope_events(events: list[Any]) -> list[ScopeInfo]:
    result: list[ScopeInfo] = []

    for event in events:
        kind = _event_kind(event)
        signature = _event_signature(event)

        # Older scanner snapshots may have emitted enum class as kind="class".
        if kind == "class" and signature.strip().startswith("enum"):
            kind = "enum"

        if kind not in SCOPE_EVENT_KINDS:
            continue

        open_line = _event_open_line(event)
        close_line = _event_close_line(event)

        if not open_line or close_line is None:
            continue

        result.append(
            ScopeInfo(
                kind=kind,
                name=_event_name(event),
                qualified_name=_event_qualified_name(event),
                open_line=open_line,
                close_line=close_line,
            )
        )

    result.sort(key=lambda item: (item.open_line, item.close_line))
    return result


def scope_stack_for_line(scopes: list[ScopeInfo], line_no: int) -> list[ScopeInfo]:
    active = [
        scope
        for scope in scopes
        if scope.open_line < line_no < scope.close_line
    ]
    active.sort(key=lambda item: (item.open_line, item.close_line))
    return active


def innermost_scope(scopes: list[ScopeInfo], line_no: int) -> ScopeInfo | None:
    stack = scope_stack_for_line(scopes, line_no)

    if not stack:
        return None

    return stack[-1]


def container_for_line(scopes: list[ScopeInfo], line_no: int) -> str:
    scope = innermost_scope(scopes, line_no)

    if scope is None:
        return ""

    return scope.qualified_name


def structural_open_close_lines(events: list[Any]) -> set[int]:
    lines: set[int] = set()

    for event in events:
        kind = _event_kind(event)

        if kind not in SCOPE_EVENT_KINDS and kind not in FUNCTION_EVENT_KINDS:
            continue

        open_line = _event_open_line(event)
        close_line = _event_close_line(event)

        if open_line:
            lines.add(open_line)

        if close_line:
            lines.add(close_line)

    return lines


# ---------------------------------------------------------------------------
# Statement iteration
# ---------------------------------------------------------------------------

def iter_candidate_statements(
    *,
    tokens: list[Token],
    events: list[Any],
) -> list[list[Token]]:
    skip_function_ranges = function_line_ranges(events)
    skip_scope_lines = structural_open_close_lines(events)

    statements: list[list[Token]] = []
    current: list[Token] = []
    paren_depth = 0
    bracket_depth = 0
    brace_depth = 0
    angle_depth = 0

    for token in tokens:
        if line_in_ranges(token.line, skip_function_ranges):
            if current:
                current = []
                paren_depth = 0
                bracket_depth = 0
                brace_depth = 0
                angle_depth = 0
            continue

        if token.line in skip_scope_lines:
            if current:
                current = []
                paren_depth = 0
                bracket_depth = 0
                brace_depth = 0
                angle_depth = 0
            continue

        value = token.value
        current.append(token)

        if value == "(":
            paren_depth += 1
        elif value == ")":
            paren_depth = max(0, paren_depth - 1)
        elif value == "[":
            bracket_depth += 1
        elif value == "]":
            bracket_depth = max(0, bracket_depth - 1)
        elif value == "{":
            brace_depth += 1
        elif value == "}":
            brace_depth = max(0, brace_depth - 1)
        elif value == "<":
            angle_depth += 1
        elif value == ">":
            angle_depth = max(0, angle_depth - 1)

        if (
            value == ";"
            and paren_depth == 0
            and bracket_depth == 0
            and brace_depth == 0
            and angle_depth == 0
        ):
            statements.append(current)
            current = []

    return statements


# ---------------------------------------------------------------------------
# Alias classification
# ---------------------------------------------------------------------------

def classify_using_alias(
    *,
    tokens: list[Token],
    scopes: list[ScopeInfo],
    lines: list[str],
    module_fragment: str,
) -> TypeAliasCandidate | None:
    tokens = strip_trailing_semicolon(strip_access_label_prefix(tokens))
    exported, tokens = strip_export_prefix(tokens)
    template_prefix, rest = strip_template_prefix(tokens)

    if not rest or rest[0].value != "using":
        return None

    if len(rest) >= 2 and rest[1].value in TYPE_ALIAS_SKIP_AFTER_USING:
        return None

    equals_index = first_top_level_index(rest, "=")

    if equals_index is None:
        return None

    if equals_index < 2:
        return None

    name_token = rest[1]

    if name_token.kind != "identifier":
        return None

    short_name = name_token.value
    start_line = tokens[0].line if template_prefix else rest[0].line
    end_line = tokens[-1].line
    signature = source_range_text(lines, start_line, end_line) or tokens_to_text(tokens)
    container = container_for_line(scopes, start_line)

    return TypeAliasCandidate(
        symbol_type="type_alias_template" if template_prefix else "type_alias",
        container=container,
        short_name=short_name,
        qualified_name=qualified_name(container, short_name),
        start_line=start_line,
        end_line=end_line,
        signature=signature,
        exported=exported,
        module_fragment=module_fragment,
    )


def typedef_alias_names(rest: list[Token]) -> list[str]:
    if not rest or rest[0].value != "typedef":
        return []

    parts = split_top_level_commas(rest[1:])
    names: list[str] = []

    for part in parts:
        if not part:
            continue

        # Typedef declarators can be simple:
        #   typedef unsigned long DWORD;
        # or function-pointer shaped:
        #   typedef int (*PFN)(int);
        # The alias name is usually the last identifier before any top-level
        # initializer/attribute suffix. This is conservative but handles the
        # important routing cases.
        candidate = None

        for token in part:
            if token.kind == "identifier":
                candidate = token.value

        if candidate and candidate not in names:
            names.append(candidate)

    return names


def classify_typedef_aliases(
    *,
    tokens: list[Token],
    scopes: list[ScopeInfo],
    lines: list[str],
    module_fragment: str,
) -> list[TypeAliasCandidate]:
    tokens = strip_trailing_semicolon(strip_access_label_prefix(tokens))
    exported, tokens = strip_export_prefix(tokens)

    if not tokens or tokens[0].value != "typedef":
        return []

    names = typedef_alias_names(tokens)

    if not names:
        return []

    start_line = tokens[0].line
    end_line = tokens[-1].line
    signature = source_range_text(lines, start_line, end_line) or tokens_to_text(tokens)
    container = container_for_line(scopes, start_line)

    return [
        TypeAliasCandidate(
            symbol_type="typedef_declaration",
            container=container,
            short_name=name,
            qualified_name=qualified_name(container, name),
            start_line=start_line,
            end_line=end_line,
            signature=signature,
            exported=exported,
            module_fragment=module_fragment,
        )
        for name in names
    ]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def type_alias_candidate_to_symbol(
    *,
    file_id: str,
    candidate: TypeAliasCandidate,
) -> dict[str, Any]:
    signature_hash = _hash_text(candidate.signature)

    return {
        "symbolId": make_alias_symbol_id(
            file_id=file_id,
            start_line=candidate.start_line,
            end_line=candidate.end_line,
            signature=candidate.signature,
        ),
        "type": candidate.symbol_type,
        "shortName": candidate.short_name,
        "qualifiedName": candidate.qualified_name,
        "container": candidate.container,
        "startLine": candidate.start_line,
        "endLine": candidate.end_line,
        "signature": candidate.signature,
        "signatureHash": signature_hash,
        "exported": candidate.exported,
        "moduleFragment": candidate.module_fragment,
    }


def emit_type_alias_symbols(
    *,
    file_id: str,
    lines: list[str],
    structural_events: list[Any],
    module_info: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Emit conservative type alias symbols for a single C++ file.

    This indexes named type aliases as routing symbols:

        using Name = Type;
        template<class T> using Name = Type<T>;
        typedef Type Name;
        typedef int (*PFN)(int);

    This is still a locator. It does not resolve the aliased type.
    """

    module_info = module_info or {}
    module_fragment = str(module_info.get("fragment") or module_info.get("moduleFragment") or "unknown")
    tokens = tokenize_lines(lines)
    scopes = scope_events(structural_events)
    diagnostics: list[dict[str, Any]] = []
    candidates: list[TypeAliasCandidate] = []

    for statement in iter_candidate_statements(tokens=tokens, events=structural_events):
        try:
            using_alias = classify_using_alias(
                tokens=statement,
                scopes=scopes,
                lines=lines,
                module_fragment=module_fragment,
            )

            if using_alias is not None:
                candidates.append(using_alias)
                continue

            candidates.extend(
                classify_typedef_aliases(
                    tokens=statement,
                    scopes=scopes,
                    lines=lines,
                    module_fragment=module_fragment,
                )
            )
        except Exception as exc:  # noqa: BLE001 - alias diagnostics should not stop indexing.
            diagnostics.append(
                {
                    "severity": "warning",
                    "code": "type_alias_parse_failed",
                    "message": str(exc),
                    "range": {
                        "startLine": statement[0].line if statement else 1,
                        "endLine": statement[-1].line if statement else 1,
                    },
                }
            )

    symbols = [
        type_alias_candidate_to_symbol(
            file_id=file_id,
            candidate=candidate,
        )
        for candidate in candidates
    ]

    symbols.sort(
        key=lambda item: (
            item["startLine"],
            item["endLine"],
            item["qualifiedName"],
        )
    )

    return symbols, diagnostics
