from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

from cpp_index_model import Token
from cpp_index_utils import normalize_signature_spacing
from cpp_lexer import split_top_level_commas, tokenize_lines, token_values, tokens_to_text, update_angle_depth


DATA_DECLARATION_SCHEMA = "cpp.data_declarations.v1"


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

STORAGE_SPECIFIERS = {
    "static",
    "inline",
    "constexpr",
    "constinit",
    "extern",
    "mutable",
    "thread_local",
}

LEADING_SPECIFIERS_TO_KEEP_AS_TYPE = {
    "const",
    "volatile",
    "signed",
    "unsigned",
    "short",
    "long",
}

DECLARATION_SKIP_KEYWORDS = {
    "using",
    "typedef",
    "friend",
    "static_assert",
    "return",
    "co_return",
    "if",
    "for",
    "while",
    "switch",
    "catch",
    "case",
    "default",
    "namespace",
    "class",
    "struct",
    "enum",
    "import",
    "module",
    "export",
    "requires",
}

ACCESS_LABELS = {
    "public",
    "protected",
    "private",
}

IDENTIFIER_IGNORE_AS_NAME = STORAGE_SPECIFIERS | DECLARATION_SKIP_KEYWORDS | {
    "virtual",
    "override",
    "final",
    "noexcept",
    "decltype",
    "sizeof",
    "alignof",
    "consteval",
    "constinit",
    "const_cast",
    "static_cast",
    "reinterpret_cast",
    "dynamic_cast",
}


@dataclass(slots=True)
class ScopeInfo:
    kind: str
    name: str
    qualified_name: str
    open_line: int
    close_line: int


@dataclass(slots=True)
class DataCandidate:
    declaration_kind: str
    scope_kind: str
    container: str
    name: str
    qualified_name: str
    start_line: int
    end_line: int
    signature: str
    type_text: str
    storage: list[str]
    initializer_kind: str


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


def _event_start_line(event: Any) -> int:
    return int(_get_value(event, "start_line", _get_value(event, "startLine", 0)) or 0)


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
# Source/signature helpers
# ---------------------------------------------------------------------------

def _hash_text(text: str, length: int = 12) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:length]


def make_data_id(*, file_id: str, start_line: int, end_line: int, signature: str) -> str:
    return f"d_{file_id}_{start_line:06d}_{end_line:06d}_{_hash_text(signature)}"


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
    # Handles:
    #   protected:
    #       int value;
    # and the rare compact form:
    #   protected: int value;
    for index in range(0, len(tokens) - 1):
        if tokens[index].value in ACCESS_LABELS and tokens[index + 1].value == ":":
            return tokens[index + 2:]

    return tokens


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


def storage_from_tokens(tokens: list[Token]) -> list[str]:
    storage: list[str] = []

    for token in tokens:
        if token.value in STORAGE_SPECIFIERS and token.value not in storage:
            storage.append(token.value)

    return storage


def remove_storage_tokens(tokens: list[Token]) -> list[Token]:
    return [token for token in tokens if token.value not in STORAGE_SPECIFIERS]

def split_common_type_and_first_declarator_prefix(
    tokens_before_first_name: list[Token],
) -> tuple[list[Token], list[Token]]:
    """Split common declaration type from first declarator pointer/ref prefix.

    C/C++ pointer and reference markers belong to the declarator, not to the
    common declaration specifier sequence:

        int *a, b;

    `a` is `int*`, but `b` is `int`. For the first declarator we therefore
    move a trailing pointer/ref suffix from the common prefix into the first
    declarator prefix.

    This is intentionally conservative. Plain trailing `const` in `int const a`
    stays part of the common type. `const`/`volatile` are only moved when they
    are attached to a trailing pointer/ref declarator suffix, e.g.
    `int * const a`.
    """

    if not tokens_before_first_name:
        return [], []

    index = len(tokens_before_first_name)

    cv_start = index

    while cv_start > 0 and tokens_before_first_name[cv_start - 1].value in {"const", "volatile"}:
        cv_start -= 1

    if cv_start == index:
        pointer_probe = index - 1
    else:
        pointer_probe = cv_start - 1

    if pointer_probe < 0 or tokens_before_first_name[pointer_probe].value not in {"*", "&", "&&"}:
        return tokens_before_first_name, []

    split_index = pointer_probe

    while split_index > 0:
        value = tokens_before_first_name[split_index - 1].value

        if value not in {"*", "&", "&&", "const", "volatile"}:
            break

        split_index -= 1

    return tokens_before_first_name[:split_index], tokens_before_first_name[split_index:]

def is_top_level_value(tokens: list[Token], index: int, value: str) -> bool:
    paren_depth = 0
    bracket_depth = 0
    brace_depth = 0
    angle_depth = 0

    for current_index, token in enumerate(tokens):
        token_value = token.value

        if current_index == index:
            return (
                token_value == value
                and paren_depth == 0
                and bracket_depth == 0
                and brace_depth == 0
                and angle_depth == 0
            )

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
        else:
            angle_depth = update_angle_depth(token_value, angle_depth)

    return False


def first_top_level_index(tokens: list[Token], values: set[str]) -> int | None:
    paren_depth = 0
    bracket_depth = 0
    brace_depth = 0
    angle_depth = 0

    for index, token in enumerate(tokens):
        value = token.value

        if (
            value in values
            and paren_depth == 0
            and bracket_depth == 0
            and brace_depth == 0
            and angle_depth == 0
        ):
            return index

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
        else:
            angle_depth = update_angle_depth(value, angle_depth)

    return None


def initializer_kind_from_tokens(tokens_after_name: list[Token]) -> str:
    if not tokens_after_name:
        return "none"

    for token in tokens_after_name:
        if token.value == "=":
            return "equals"

        if token.value == "{":
            return "brace"

        if token.value == "(":
            return "paren"

        if token.value == ";":
            return "none"

    return "none"


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
        # Treat signatures starting with enum as enum scopes for enumerator extraction.
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


def container_for_line(scopes: list[ScopeInfo], line_no: int) -> tuple[str, str]:
    scope = innermost_scope(scopes, line_no)

    if scope is None:
        return "", "global"

    if scope.kind in {"class", "struct"}:
        return scope.qualified_name, scope.kind

    if scope.kind == "enum":
        return scope.qualified_name, "enum"

    # Namespace scope: use the innermost namespace as container.
    return scope.qualified_name, "namespace"


def qualified_name(container: str, name: str) -> str:
    if not container:
        return name

    return f"{container}::{name}"


# ---------------------------------------------------------------------------
# Statement iteration
# ---------------------------------------------------------------------------

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
        else:
            angle_depth = update_angle_depth(value, angle_depth)

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
# Data declaration classification
# ---------------------------------------------------------------------------

def statement_should_be_skipped(tokens: list[Token]) -> bool:
    if not tokens:
        return True

    values = set(token_values(tokens))

    if values & DECLARATION_SKIP_KEYWORDS:
        # `template<class T> concept C = ...;` is handled separately.
        if "concept" in values:
            return False

        return True

    # Preprocessor and macro-like directives should have been skipped by the lexer,
    # but keep this defensive.
    if "#" in values:
        return True

    return False


def classify_concept_statement(
    *,
    tokens: list[Token],
    scopes: list[ScopeInfo],
    lines: list[str],
) -> DataCandidate | None:
    _, rest = strip_template_prefix(tokens)
    values = token_values(rest)

    if "concept" not in values:
        return None

    concept_index = values.index("concept")

    if concept_index + 1 >= len(rest):
        return None

    name_token = rest[concept_index + 1]

    if name_token.kind != "identifier":
        return None

    start_line = tokens[0].line
    end_line = tokens[-1].line
    container, scope_kind = container_for_line(scopes, start_line)

    if scope_kind not in {"global", "namespace", "class", "struct"}:
        return None

    name = name_token.value
    signature = source_range_text(lines, start_line, end_line)

    return DataCandidate(
        declaration_kind="concept",
        scope_kind=scope_kind,
        container=container,
        name=name,
        qualified_name=qualified_name(container, name),
        start_line=start_line,
        end_line=end_line,
        signature=signature,
        type_text="concept",
        storage=[],
        initializer_kind="equals" if "=" in values else "unknown",
    )


def find_declarator_name(part: list[Token]) -> tuple[int, str] | None:
    # V1 safety rule:
    #
    # Do not index parenthesized data initializers.
    #
    # At namespace scope, declarations such as:
    #
    #     int value(0);
    #     inline int value(0);
    #
    # can be valid variable declarations. However, they are syntactically too
    # close to function declarations:
    #
    #     int value();
    #     constexpr int value() noexcept;
    #     auto value() -> int;
    #
    # For the routing index, false negatives are safer than false positives.
    # Missing a rare parenthesized variable initializer is acceptable. Indexing
    # a function declaration as data would pollute find_data/list_type_members
    # results.
    #
    # Therefore V1 only indexes conservative data declaration forms:
    #
    #     T value;
    #     T value = expr;
    #     T value{expr};
    #     T values[2];
    #
    # Parenthesized declarators are skipped by default.

    stop_index = first_top_level_index(part, {"=", "{", ":"})

    if stop_index is None:
        declarator = part
    else:
        declarator = part[:stop_index]

    if not declarator:
        return None

    for index, token in enumerate(declarator):
        if token.value == "(":
            return None

    for index in range(len(declarator) - 1, -1, -1):
        token = declarator[index]

        if token.kind != "identifier":
            continue

        if token.value in IDENTIFIER_IGNORE_AS_NAME:
            continue

        next_token = declarator[index + 1] if index + 1 < len(declarator) else None

        if next_token is not None and next_token.value == "(":
            return None

        return index, token.value

    return None


def declarator_array_suffix(part: list[Token], name_index: int) -> str:
    suffix_tokens: list[Token] = []
    index = name_index + 1

    while index < len(part):
        token = part[index]

        if token.value != "[":
            break

        depth = 0
        start = index

        while index < len(part):
            value = part[index].value

            if value == "[":
                depth += 1
            elif value == "]":
                depth -= 1

                if depth == 0:
                    index += 1
                    suffix_tokens.extend(part[start:index])
                    break

            index += 1
        else:
            break

    if not suffix_tokens:
        return ""

    return tokens_to_text(suffix_tokens).replace(" ", "")


def type_text_for_declarator(
    *,
    base_type_tokens: list[Token],
    declarator_prefix_tokens: list[Token],
    part: list[Token],
    name_index: int,
) -> str:
    array_suffix = declarator_array_suffix(part, name_index)
    type_tokens = remove_storage_tokens([*base_type_tokens, *declarator_prefix_tokens])
    type_text = tokens_to_text(type_tokens).strip()

    if array_suffix:
        type_text += array_suffix

    return type_text


def classify_data_statement(
    *,
    tokens: list[Token],
    scopes: list[ScopeInfo],
    lines: list[str],
) -> list[DataCandidate]:
    tokens = strip_access_label_prefix(tokens)
    tokens = strip_trailing_semicolon(tokens)

    if not tokens:
        return []

    concept = classify_concept_statement(tokens=tokens, scopes=scopes, lines=lines)

    if concept is not None:
        return [concept]

    if statement_should_be_skipped(tokens):
        return []

    template_prefix, rest = strip_template_prefix(tokens)
    is_variable_template = bool(template_prefix)

    if not rest:
        return []

    parts = split_top_level_commas(rest)

    if not parts:
        return []

    first_name = find_declarator_name(parts[0])

    if first_name is None:
        return []

    first_name_index, _ = first_name
    base_type_tokens, first_declarator_prefix_tokens = split_common_type_and_first_declarator_prefix(
        parts[0][:first_name_index]
    )
    storage = storage_from_tokens(rest)

    candidates: list[DataCandidate] = []
    start_line = tokens[0].line
    end_line = tokens[-1].line
    container, scope_kind = container_for_line(scopes, start_line)

    if scope_kind == "enum":
        return []

    if scope_kind in {"class", "struct"}:
        declaration_kind = "field"
    elif is_variable_template:
        declaration_kind = "variable_template"
    else:
        declaration_kind = "global_variable"

    for part_index, part in enumerate(parts):
        name_result = find_declarator_name(part)

        if name_result is None:
            continue

        name_index, name = name_result
        tokens_after_name = part[name_index + 1:]
        initializer_kind = initializer_kind_from_tokens(tokens_after_name)

        if initializer_kind == "paren":
            # See V1 safety rule in find_declarator_name().
            continue

        if part_index == 0:
            declarator_prefix_tokens = first_declarator_prefix_tokens
        else:
            declarator_prefix_tokens = part[:name_index]

        type_text = type_text_for_declarator(
            base_type_tokens=base_type_tokens,
            declarator_prefix_tokens=declarator_prefix_tokens,
            part=part,
            name_index=name_index,
        )

        if not type_text and declaration_kind != "concept":
            continue

        signature = source_range_text(lines, start_line, end_line)

        if not signature:
            signature = tokens_to_text(tokens)

        candidates.append(
            DataCandidate(
                declaration_kind=declaration_kind,
                scope_kind=scope_kind,
                container=container,
                name=name,
                qualified_name=qualified_name(container, name),
                start_line=start_line,
                end_line=end_line,
                signature=signature,
                type_text=type_text,
                storage=storage,
                initializer_kind=initializer_kind,
            )
        )

    return candidates


# ---------------------------------------------------------------------------
# Enum enumerator extraction
# ---------------------------------------------------------------------------

def tokens_in_line_range(tokens: list[Token], start_line: int, end_line: int) -> list[Token]:
    return [token for token in tokens if start_line <= token.line <= end_line]


def enum_body_tokens(tokens: list[Token], enum_scope: ScopeInfo) -> list[Token]:
    scoped_tokens = tokens_in_line_range(tokens, enum_scope.open_line, enum_scope.close_line)

    open_index = None
    close_index = None

    for index, token in enumerate(scoped_tokens):
        if token.value == "{":
            open_index = index
            break

    for index in range(len(scoped_tokens) - 1, -1, -1):
        if scoped_tokens[index].value == "}":
            close_index = index
            break

    if open_index is None or close_index is None or close_index <= open_index:
        return []

    return scoped_tokens[open_index + 1:close_index]


def split_top_level_enumerator_commas(tokens: list[Token]) -> list[list[Token]]:
    parts: list[list[Token]] = []
    current: list[Token] = []
    paren_depth = 0
    bracket_depth = 0
    brace_depth = 0
    angle_depth = 0

    for token in tokens:
        value = token.value

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
        else:
            angle_depth = update_angle_depth(value, angle_depth)

        if (
            value == ","
            and paren_depth == 0
            and bracket_depth == 0
            and brace_depth == 0
            and angle_depth == 0
        ):
            if current:
                parts.append(current)
                current = []
            continue

        current.append(token)

    if current:
        parts.append(current)

    return parts


def emit_enumerators(
    *,
    tokens: list[Token],
    enum_scope: ScopeInfo,
) -> list[DataCandidate]:
    body = enum_body_tokens(tokens, enum_scope)

    if not body:
        return []

    candidates: list[DataCandidate] = []

    for part in split_top_level_enumerator_commas(body):
        if not part:
            continue

        name_token = None

        for token in part:
            if token.kind == "identifier":
                name_token = token
                break

        if name_token is None:
            continue

        name = name_token.value
        signature = tokens_to_text(part)

        candidates.append(
            DataCandidate(
                declaration_kind="enumerator",
                scope_kind="enum",
                container=enum_scope.qualified_name,
                name=name,
                qualified_name=qualified_name(enum_scope.qualified_name, name),
                start_line=part[0].line,
                end_line=part[-1].line,
                signature=signature,
                type_text=enum_scope.qualified_name,
                storage=[],
                initializer_kind="equals" if "=" in token_values(part) else "none",
            )
        )

    return candidates


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def data_candidate_to_json(
    *,
    file_id: str,
    candidate: DataCandidate,
    module_fragment: str,
) -> dict[str, Any]:
    signature_hash = hashlib.sha256(candidate.signature.encode("utf-8", errors="replace")).hexdigest()

    return {
        "dataId": make_data_id(
            file_id=file_id,
            start_line=candidate.start_line,
            end_line=candidate.end_line,
            signature=candidate.signature,
        ),
        "declarationKind": candidate.declaration_kind,
        "scopeKind": candidate.scope_kind,
        "container": candidate.container,
        "name": candidate.name,
        "qualifiedName": candidate.qualified_name,
        "startLine": candidate.start_line,
        "endLine": candidate.end_line,
        "signature": candidate.signature,
        "typeText": candidate.type_text,
        "storage": candidate.storage,
        "initializerKind": candidate.initializer_kind,
        "signatureHash": signature_hash,
        "moduleFragment": module_fragment,
    }


def emit_data_declarations(
    *,
    file_id: str,
    lines: list[str],
    structural_events: list[Any],
    module_info: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Emit conservative data/value declarations for a single C++ file.

    This is intentionally a locator, not a semantic analyzer. It indexes
    namespace/global variables, class/struct fields, variable templates,
    concepts, and enum values that are visible outside function bodies.

    Local variables are intentionally ignored.
    """

    module_info = module_info or {}
    module_fragment = str(module_info.get("fragment") or module_info.get("moduleFragment") or "unknown")
    tokens = tokenize_lines(lines)
    scopes = scope_events(structural_events)
    diagnostics: list[dict[str, Any]] = []
    candidates: list[DataCandidate] = []

    for enum_scope in scopes:
        if enum_scope.kind != "enum":
            continue

        candidates.extend(emit_enumerators(tokens=tokens, enum_scope=enum_scope))

    for statement in iter_candidate_statements(tokens=tokens, events=structural_events):
        try:
            candidates.extend(
                classify_data_statement(
                    tokens=statement,
                    scopes=scopes,
                    lines=lines,
                )
            )
        except Exception as exc:  # noqa: BLE001 - scanner diagnostics should not stop indexing.
            diagnostics.append(
                {
                    "severity": "warning",
                    "code": "data_declaration_parse_failed",
                    "message": str(exc),
                    "range": {
                        "startLine": statement[0].line if statement else 1,
                        "endLine": statement[-1].line if statement else 1,
                    },
                }
            )

    data_items = [
        data_candidate_to_json(
            file_id=file_id,
            candidate=candidate,
            module_fragment=module_fragment,
        )
        for candidate in candidates
    ]

    data_items.sort(
        key=lambda item: (
            item["startLine"],
            item["endLine"],
            item["qualifiedName"],
        )
    )

    return data_items, diagnostics
