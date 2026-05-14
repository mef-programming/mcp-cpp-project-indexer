from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from cpp_index_model import (
    CONTROL_KEYWORDS,
    DECLARATION_SKIP_PREFIXES,
    BraceRecord,
    Diagnostic,
    ScopeFrame,
    SourceRange,
    StructuralEvent,
    TemplateAttachment,
    TemplatePrefix,
    Token,
)
from cpp_index_utils import source_text_range
from cpp_lexer import (
    find_matching_token,
    first_identifier,
    split_top_level_commas,
    tokenize_lines,
    token_values,
    tokens_to_text,
)
from cpp_module_scan import determine_fragment_for_line


@dataclass(slots=True)
class StructuralScanResult:
    events: list[StructuralEvent]
    scope_map: list[list[ScopeFrame]]
    scope_intervals: list[dict[str, Any]]
    function_body_ranges: list[dict[str, Any]]
    diagnostics: list[Diagnostic]


# ---------------------------------------------------------------------------
# Qualification / scope helpers
# ---------------------------------------------------------------------------

def build_qualified_name(scope_stack: list[ScopeFrame], name: str) -> str:
    if not name:
        return ""

    # Already-qualified names, e.g. A::B::Foo, are kept as visible.
    if "::" in name:
        return name

    parts = [
        frame.name
        for frame in scope_stack
        if frame.kind in {"namespace", "class", "struct"}
        and frame.name
        and not frame.name.startswith("<")
    ]
    parts.append(name)
    return "::".join(parts)


def current_scope_exported(scope_stack: list[ScopeFrame]) -> bool:
    return any(frame.exported for frame in scope_stack)


def current_type_name(scope_stack: list[ScopeFrame]) -> str:
    for frame in reversed(scope_stack):
        if frame.kind in {"class", "struct"}:
            return frame.name

    return ""


# ---------------------------------------------------------------------------
# Template prefix parsing
# ---------------------------------------------------------------------------

def parse_template_prefixes(
    segment: list[Token],
    lines: list[str],
) -> tuple[list[TemplatePrefix], list[Token]]:
    """Detach one or more leading template<...> prefixes from a statement.

    The returned prefixes are attached later to the following class/struct/
    function/method event. They are not emitted as standalone symbols.
    """

    if not segment:
        return [], segment

    prefixes: list[TemplatePrefix] = []
    index = 0

    if index < len(segment) and segment[index].value == "export":
        index += 1

    while index < len(segment) and segment[index].value == "template":
        template_token = segment[index]
        lt_index = index + 1

        if lt_index >= len(segment) or segment[lt_index].value != "<":
            break

        gt_index = find_matching_angle(segment, lt_index)

        if gt_index is None:
            break

        after_gt = gt_index + 1
        requires_tokens: list[Token] = []

        if after_gt < len(segment) and segment[after_gt].value == "requires":
            requires_start = after_gt
            after_gt += 1

            while after_gt < len(segment):
                # Stop at the next declaration introducer. This is best-effort;
                # the indexer only needs to preserve the visible prefix text.
                if segment[after_gt].value in {
                    "template",
                    "class",
                    "struct",
                    "enum",
                    "namespace",
                    "auto",
                    "void",
                    "bool",
                    "char",
                    "short",
                    "int",
                    "long",
                    "float",
                    "double",
                }:
                    break

                after_gt += 1

            requires_tokens = segment[requires_start:after_gt]

        end_token = segment[after_gt - 1]
        raw_prefix = source_text_range(
            lines,
            template_token.line,
            end_token.line,
            end_token.col0 + len(end_token.value),
        )

        parameter_tokens = segment[lt_index + 1 : gt_index]
        parameters = [
            tokens_to_text(part)
            for part in split_top_level_commas(parameter_tokens)
            if part
        ]

        prefixes.append(
            TemplatePrefix(
                start_line=template_token.line,
                end_line=end_token.line,
                start_col0=template_token.col0,
                end_col0_exclusive=end_token.col0 + len(end_token.value),
                raw_prefix=raw_prefix,
                parameters=parameters,
                requires_clause=tokens_to_text(requires_tokens),
            )
        )

        index = after_gt

    if not prefixes:
        return [], segment

    return prefixes, segment[index:]


def find_matching_angle(tokens: list[Token], open_index: int) -> int | None:
    depth = 0

    for index in range(open_index, len(tokens)):
        value = tokens[index].value

        if value == "<":
            depth += 1
        elif value == ">":
            depth -= 1

            if depth == 0:
                return index

    return None


def template_target_kind(symbol_kind: str) -> str:
    return {
        "class": "class_template",
        "class_declaration": "class_template",
        "struct": "struct_template",
        "struct_declaration": "struct_template",
        "enum": "enum_template",
        "function": "function_template",
        "function_declaration": "function_template",
        "method": "method_template",
        "method_declaration": "method_template",
        "constructor": "constructor_template",
        "constructor_declaration": "constructor_template",
        "destructor": "destructor_template",
        "destructor_declaration": "destructor_template",
        "operator": "operator_template",
        "operator_declaration": "operator_template",
    }.get(symbol_kind, "unknown_template")


def attach_template(
    symbol_kind: str,
    prefixes: list[TemplatePrefix],
) -> TemplateAttachment | None:
    if not prefixes:
        return None

    return TemplateAttachment(
        target_kind=template_target_kind(symbol_kind),
        specialization_kind="primary_template",
        prefixes=prefixes,
    )


# ---------------------------------------------------------------------------
# Declaration classification
# ---------------------------------------------------------------------------

def split_nested_namespace_from_tokens(tokens: list[Token]) -> list[str]:
    names: list[str] = []

    for token in tokens:
        if token.kind == "identifier":
            names.append(token.value)
            continue

        if token.value == "::":
            continue

        break

    if not names:
        return ["<anonymous namespace>"]

    return names


def classify_namespace_open(segment: list[Token]) -> tuple[bool, list[str], bool, bool]:
    values = token_values(segment)

    if "namespace" not in values:
        return False, [], False, False

    ns_index = values.index("namespace")
    is_inline = "inline" in values[:ns_index]
    is_exported = "export" in values[:ns_index]

    # namespace alias: namespace X = Y;
    if "=" in values[ns_index + 1 :]:
        return False, [], False, False

    return True, split_nested_namespace_from_tokens(segment[ns_index + 1 :]), is_inline, is_exported


CLASS_HEAD_IGNORED_IDENTIFIERS = {
    "final",
    "sealed",
}


def find_type_name_after_class_key(tokens: list[Token], class_key_index: int) -> str | None:
    """Best-effort class/struct head name extraction.

    Handles patterns such as:
      class Foo
      class Foo final
      class DECLSPEC_NOVTABLE Foo
      struct __declspec(...) Foo

    This does not expand macros. It only avoids treating macro-like declspec
    tokens before the real class name as the class name.
    """

    head_tokens: list[Token] = []
    paren_depth = 0
    bracket_depth = 0
    angle_depth = 0

    for token in tokens[class_key_index + 1:]:
        value = token.value

        if value == "(":
            paren_depth += 1
        elif value == ")":
            paren_depth = max(0, paren_depth - 1)
        elif value == "[":
            bracket_depth += 1
        elif value == "]":
            bracket_depth = max(0, bracket_depth - 1)
        elif value == "<":
            angle_depth += 1
        elif value == ">":
            angle_depth = max(0, angle_depth - 1)

        if (
            value == ":"
            and paren_depth == 0
            and bracket_depth == 0
            and angle_depth == 0
        ):
            break

        if (
            value == "{"
            and paren_depth == 0
            and bracket_depth == 0
            and angle_depth == 0
        ):
            break

        if (
            value == ";"
            and paren_depth == 0
            and bracket_depth == 0
            and angle_depth == 0
        ):
            break

        head_tokens.append(token)

    identifiers = [
        token.value
        for token in head_tokens
        if token.kind == "identifier"
        and token.value not in CLASS_HEAD_IGNORED_IDENTIFIERS
    ]

    if not identifiers:
        return None

    # Important:
    #   class DECLSPEC_NOVTABLE Browser
    # should yield Browser, not DECLSPEC_NOVTABLE.
    #
    #   class Browser final
    # yields Browser because final is ignored above.
    return identifiers[-1]


def classify_type_open(segment: list[Token]) -> tuple[bool, str, str, bool]:
    values = token_values(segment)
    exported = "export" in values[:2]

    for index, token in enumerate(segment):
        if token.value not in {"class", "struct"}:
            continue

        type_name = find_type_name_after_class_key(segment, index)

        if type_name:
            return True, token.value, type_name, exported

        return False, "", "", False

    return False, "", "", False

def classify_type_declaration(segment: list[Token]) -> tuple[bool, str, str, bool]:
    is_type, type_kind, type_name, exported = classify_type_open(segment)

    if not is_type:
        return False, "", "", False

    return True, f"{type_kind}_declaration", type_name, exported


def classify_enum(segment: list[Token]) -> tuple[bool, str, bool]:
    values = token_values(segment)

    if "enum" not in values:
        return False, "", False

    enum_index = values.index("enum")
    exported = "export" in values[:enum_index]
    index = enum_index + 1

    if index < len(segment) and segment[index].value in {"class", "struct"}:
        index += 1

    if index < len(segment) and segment[index].kind == "identifier":
        return True, segment[index].value, exported

    return True, "<anonymous enum>", exported


def should_skip_declaration_statement(segment: list[Token]) -> bool:
    if not segment:
        return True

    values = token_values(segment)

    if not values:
        return True

    first = next((value for value in values if value != "export"), "")

    if first in DECLARATION_SKIP_PREFIXES:
        return True

    if first in {"module", "import", "#"}:
        return True

    if is_macro_like_invocation_statement(segment):
        return True

    return False


def is_macro_like_invocation_statement(segment: list[Token]) -> bool:
    """Return true for macro-style invocations such as DEFINE_FOO(...);.

    The indexer does not expand macros. A macro invocation may generate symbols,
    but those generated symbols do not have an exact visible source range in this
    file. Therefore they must not become function_declaration symbols.
    """

    if len(segment) < 2:
        return False

    first = segment[0]

    if first.kind != "identifier":
        return False

    if segment[1].value != "(":
        return False

    name = first.value

    # Typical Windows/ATL/WTL/MFC/project macro style:
    #   DEFINE_ENUM_FLAG_OPERATORS(...)
    #   DECLARE_MESSAGE_MAP()
    #   IMPLEMENT_DYNAMIC(...)
    #   BEGIN_MSG_MAP(...)
    if name.isupper():
        return True

    macro_prefixes = (
        "DEFINE_",
        "DECLARE_",
        "IMPLEMENT_",
        "BEGIN_",
        "END_",
        "ATL_",
        "WTL_",
        "AFX_",
    )

    return any(name.startswith(prefix) for prefix in macro_prefixes)


# ---------------------------------------------------------------------------
# Function/method classification
# ---------------------------------------------------------------------------

def find_function_paren_index(segment: list[Token]) -> int | None:
    initializer_colon = find_top_level_initializer_colon(segment)

    if initializer_colon is not None:
        search_segment = segment[:initializer_colon]
    else:
        search_segment = segment

    candidates: list[int] = []

    for index, token in enumerate(search_segment):
        if token.value != "(":
            continue

        if index == 0:
            continue

        previous = search_segment[index - 1]

        if previous.value in CONTROL_KEYWORDS:
            continue

        if previous.value in {"decltype", "sizeof", "alignof", "noexcept"}:
            continue

        close = find_matching_token(search_segment, index, "(", ")")

        if close is None:
            continue

        candidates.append(index)

    if not candidates:
        return None

    return candidates[-1]


def extract_function_name(segment: list[Token], paren_index: int) -> tuple[str, str]:
    if paren_index <= 0:
        return "", ""

    # Operator overloads:
    #   operator bool()
    #   operator=()
    #   operator()(...)
    #   A::operator bool()
    op_index = None

    for index in range(paren_index - 1, -1, -1):
        if segment[index].value == "operator":
            op_index = index
            break

    if op_index is not None:
        name_start = op_index

        # Include a qualified prefix before operator, but never consume a
        # return type. Only walk across explicit :: separators.
        while (
            name_start >= 2
            and segment[name_start - 1].value == "::"
            and segment[name_start - 2].kind == "identifier"
        ):
            name_start -= 2

        name_tokens = segment[name_start:paren_index]
        visible_name = tokens_to_text(name_tokens)
        visible_name = (
            visible_name
            .replace(" :: ", "::")
            .replace(":: ", "::")
            .replace(" ::", "::")
        )

        short_name = visible_name.split("::")[-1]
        return short_name, visible_name

    # Normal function/method name.
    # Important:
    #   BOOL EnableScrollBar(...)
    # must yield:
    #   EnableScrollBar
    # not:
    #   BOOL EnableScrollBar
    end_index = paren_index - 1

    if end_index < 0:
        return "", ""

    if segment[end_index].kind != "identifier":
        return "", ""

    name_start = end_index

    # Destructor:
    #   ~Editor()
    #   Editor::~Editor()
    if name_start - 1 >= 0 and segment[name_start - 1].value == "~":
        name_start -= 1

    # Qualified name:
    #   Editor::EnableScrollBar()
    #   Namespace::Type::EnableScrollBar()
    # Only walk across explicit :: separators. This prevents consuming the
    # return type in declarations such as BOOL EnableScrollBar(...).
    while (
        name_start >= 2
        and segment[name_start - 1].value == "::"
        and segment[name_start - 2].kind == "identifier"
    ):
        name_start -= 2

        if name_start - 1 >= 0 and segment[name_start - 1].value == "~":
            name_start -= 1

    name_tokens = segment[name_start:paren_index]
    visible_name = tokens_to_text(name_tokens)
    visible_name = (
        visible_name
        .replace(" :: ", "::")
        .replace(":: ", "::")
        .replace(" ::", "::")
        .replace("~ ", "~")
    )

    short_name = visible_name.split("::")[-1]
    return short_name, visible_name

def find_top_level_initializer_colon(segment: list[Token]) -> int | None:
    paren_depth = 0
    bracket_depth = 0
    angle_depth = 0

    for index, token in enumerate(segment):
        value = token.value

        if value == "(":
            paren_depth += 1
        elif value == ")":
            paren_depth = max(0, paren_depth - 1)
        elif value == "[":
            bracket_depth += 1
        elif value == "]":
            bracket_depth = max(0, bracket_depth - 1)
        elif value == "<":
            angle_depth += 1
        elif value == ">":
            angle_depth = max(0, angle_depth - 1)

        if (
            value == ":"
            and paren_depth == 0
            and bracket_depth == 0
            and angle_depth == 0
        ):
            # Ignore scope operator ::. Your tokenizer emits :: as one token,
            # but this keeps the helper safe if that changes later.
            prev_value = segment[index - 1].value if index > 0 else ""
            next_value = segment[index + 1].value if index + 1 < len(segment) else ""

            if prev_value == ":" or next_value == ":":
                continue

            return index

    return None


def classify_function_like(
    *,
    segment: list[Token],
    scope_stack: list[ScopeFrame],
    has_body: bool,
) -> tuple[bool, str, str, str]:
    if not segment:
        return False, "", "", ""

    values = token_values(segment)

    if any(value in values for value in {"namespace", "class", "struct", "enum"}):
        return False, "", "", ""

    first = first_identifier(segment)

    if first and first.value in CONTROL_KEYWORDS:
        return False, "", "", ""

    paren_index = find_function_paren_index(segment)

    if paren_index is None:
        return False, "", "", ""

    short_name, visible_name = extract_function_name(segment, paren_index)

    if not short_name or short_name in CONTROL_KEYWORDS:
        return False, "", "", ""

    type_name = current_type_name(scope_stack)

    # Reject call-like / macro-like statements:
    #   DEFINE_ENUM_FLAG_OPERATORS(X);
    #   ASSUME(x);
    #   ATLASSERT(x);
    #
    # A single Identifier(...) statement is not a free function declaration.
    # In class scope it is only valid as a constructor when the identifier
    # matches the current type name. This avoids name-based macro guessing.
    if (
        paren_index == 1
        and segment[0].kind == "identifier"
        and "::" not in visible_name
        and not short_name.startswith("operator")
        and not short_name.startswith("~")
    ):
        type_name = current_type_name(scope_stack)

        if not type_name or short_name != type_name:
            return False, "", "", ""
        
    inside_type = bool(type_name)
    is_destructor = short_name.startswith("~")
    clean_short = short_name[1:] if is_destructor else short_name
    is_constructor = bool(type_name and clean_short == type_name and not is_destructor)
    is_operator = short_name.startswith("operator")

    if is_operator:
        kind = "operator" if has_body else "operator_declaration"
    elif is_destructor:
        kind = "destructor" if has_body else "destructor_declaration"
    elif is_constructor:
        kind = "constructor" if has_body else "constructor_declaration"
    elif inside_type:
        kind = "method" if has_body else "method_declaration"
    else:
        kind = "function" if has_body else "function_declaration"

    qualified_name = build_qualified_name(scope_stack, visible_name)
    return True, kind, short_name, qualified_name


# ---------------------------------------------------------------------------
# Event construction helpers
# ---------------------------------------------------------------------------

def make_signature(
    *,
    lines: list[str],
    segment: list[Token],
    fallback_token: Token,
    end_col0_exclusive: int | None,
    template_prefixes: list[TemplatePrefix],
) -> tuple[int, int, str]:
    if template_prefixes:
        start_line = template_prefixes[0].start_line
        start_col0 = template_prefixes[0].start_col0
    elif segment:
        start_line = segment[0].line
        start_col0 = segment[0].col0
    else:
        start_line = fallback_token.line
        start_col0 = fallback_token.col0

    signature = source_text_range(
        lines,
        start_line,
        fallback_token.line,
        end_col0_exclusive,
    )

    return start_line, start_col0, signature


def make_event(
    *,
    kind: str,
    name: str,
    qualified_name: str,
    start_line: int,
    end_line: int | None,
    start_col0: int | None,
    end_col0: int | None,
    open_brace_line: int | None,
    open_brace_col0: int | None,
    close_line: int | None,
    signature: str,
    order: int,
    exported: bool,
    fragment: str,
    inline: bool = False,
    template: TemplateAttachment | None = None,
) -> StructuralEvent:
    return StructuralEvent(
        kind=kind,
        name=name,
        qualified_name=qualified_name,
        start_line=start_line,
        end_line=end_line,
        start_col0=start_col0,
        end_col0=end_col0,
        open_brace_line=open_brace_line,
        open_brace_col0=open_brace_col0,
        close_line=close_line,
        signature=signature,
        order=order,
        inline=inline,
        exported=exported,
        fragment=fragment,
        template=template,
    )


ACCESS_SPECIFIERS = {
    "public",
    "protected",
    "private",
}


def is_access_specifier_label(segment: list[Token]) -> bool:
    values = token_values(segment)

    return (
        len(values) == 1
        and values[0] in ACCESS_SPECIFIERS
    )


def is_inside_non_declaration_body(brace_stack: list[BraceRecord]) -> bool:
    """Return true when semicolon statements must not be parsed as symbols.

    Declarations are only emitted at global, namespace, and class/struct scope.
    Ordinary statements inside function bodies or nested blocks must not become
    function_declaration symbols just because they contain a call expression.
    """

    return any(
        record.kind in {"function", "block", "enum"}
        for record in brace_stack
    )


# ---------------------------------------------------------------------------
# Main structure scan
# ---------------------------------------------------------------------------

def scan_structure(
    lines: list[str],
    *,
    module_info: dict[str, Any],
) -> StructuralScanResult:
    tokens = tokenize_lines(lines)
    events: list[StructuralEvent] = []
    diagnostics: list[Diagnostic] = []

    scope_stack: list[ScopeFrame] = []
    brace_stack: list[BraceRecord] = []

    statement_start = 0
    order = 0

    for index, token in enumerate(tokens):
        if token.value == ":":
            segment = tokens[statement_start:index]
            if is_access_specifier_label(segment):
                statement_start = index + 1
                continue
    
        if token.value == "{":
            raw_segment = tokens[statement_start:index]
            template_prefixes, segment = parse_template_prefixes(raw_segment, lines)
            new_events: list[StructuralEvent] = []

            is_namespace, namespace_names, is_inline, namespace_exported = classify_namespace_open(segment)

            if is_namespace:
                start_line, start_col0, signature = make_signature(
                    lines=lines,
                    segment=segment,
                    fallback_token=token,
                    end_col0_exclusive=token.col0,
                    template_prefixes=template_prefixes,
                )
                exported = namespace_exported or current_scope_exported(scope_stack)
                running_stack = list(scope_stack)

                for namespace_name in namespace_names:
                    qualified_name = build_qualified_name(running_stack, namespace_name)
                    event = make_event(
                        kind="namespace",
                        name=namespace_name,
                        qualified_name=qualified_name,
                        start_line=start_line,
                        end_line=None,
                        start_col0=start_col0,
                        end_col0=None,
                        open_brace_line=token.line,
                        open_brace_col0=token.col0,
                        close_line=None,
                        signature=signature,
                        order=order,
                        exported=exported,
                        fragment=determine_fragment_for_line(start_line, module_info),
                        inline=is_inline,
                    )
                    order += 1
                    events.append(event)
                    new_events.append(event)

                    running_stack.append(
                        ScopeFrame(
                            kind="namespace",
                            name=namespace_name,
                            qualified_name=qualified_name,
                            start_line=start_line,
                            inline=is_inline,
                            exported=exported,
                        )
                    )

                scope_stack = running_stack
                brace_stack.append(BraceRecord(kind="scope", events=new_events))
                statement_start = index + 1
                continue

            is_type, type_kind, type_name, type_exported = classify_type_open(segment)

            if is_type:
                start_line, start_col0, signature = make_signature(
                    lines=lines,
                    segment=segment,
                    fallback_token=token,
                    end_col0_exclusive=token.col0,
                    template_prefixes=template_prefixes,
                )
                exported = type_exported or current_scope_exported(scope_stack)
                qualified_name = build_qualified_name(scope_stack, type_name)
                template = attach_template(type_kind, template_prefixes)
                event = make_event(
                    kind=type_kind,
                    name=type_name,
                    qualified_name=qualified_name,
                    start_line=start_line,
                    end_line=None,
                    start_col0=start_col0,
                    end_col0=None,
                    open_brace_line=token.line,
                    open_brace_col0=token.col0,
                    close_line=None,
                    signature=signature,
                    order=order,
                    exported=exported,
                    fragment=determine_fragment_for_line(start_line, module_info),
                    template=template,
                )
                order += 1
                events.append(event)
                new_events.append(event)

                scope_stack.append(
                    ScopeFrame(
                        kind=type_kind,
                        name=type_name,
                        qualified_name=qualified_name,
                        start_line=start_line,
                        exported=exported,
                    )
                )

                brace_stack.append(BraceRecord(kind="scope", events=new_events))
                statement_start = index + 1
                continue

            is_enum, enum_name, enum_exported = classify_enum(segment)

            if is_enum:
                start_line, start_col0, signature = make_signature(
                    lines=lines,
                    segment=segment,
                    fallback_token=token,
                    end_col0_exclusive=token.col0,
                    template_prefixes=template_prefixes,
                )
                exported = enum_exported or current_scope_exported(scope_stack)
                event = make_event(
                    kind="enum",
                    name=enum_name,
                    qualified_name=build_qualified_name(scope_stack, enum_name),
                    start_line=start_line,
                    end_line=None,
                    start_col0=start_col0,
                    end_col0=None,
                    open_brace_line=token.line,
                    open_brace_col0=token.col0,
                    close_line=None,
                    signature=signature,
                    order=order,
                    exported=exported,
                    fragment=determine_fragment_for_line(start_line, module_info),
                    template=attach_template("enum", template_prefixes),
                )
                order += 1
                events.append(event)
                brace_stack.append(BraceRecord(kind="enum", events=[event]))
                statement_start = index + 1
                continue

            is_function, function_kind, function_name, qualified_name = classify_function_like(
                segment=segment,
                scope_stack=scope_stack,
                has_body=True,
            )

            if is_function:
                signature_end_token = token
                signature_end_col0_exclusive = token.col0

                initializer_colon = find_top_level_initializer_colon(segment)

                if initializer_colon is not None:
                    colon_token = segment[initializer_colon]
                    signature_end_token = colon_token
                    signature_end_col0_exclusive = colon_token.col0

                start_line, start_col0, signature = make_signature(
                    lines=lines,
                    segment=segment,
                    fallback_token=signature_end_token,
                    end_col0_exclusive=signature_end_col0_exclusive,
                    template_prefixes=template_prefixes,
                )
                exported = current_scope_exported(scope_stack) or ("export" in token_values(segment[:2]))
                event = make_event(
                    kind=function_kind,
                    name=function_name,
                    qualified_name=qualified_name,
                    start_line=start_line,
                    end_line=None,
                    start_col0=start_col0,
                    end_col0=None,
                    open_brace_line=token.line,
                    open_brace_col0=token.col0,
                    close_line=None,
                    signature=signature,
                    order=order,
                    exported=exported,
                    fragment=determine_fragment_for_line(start_line, module_info),
                    template=attach_template(function_kind, template_prefixes),
                )
                order += 1
                events.append(event)
                brace_stack.append(BraceRecord(kind="function", events=[event]))
                statement_start = index + 1
                continue

            brace_stack.append(BraceRecord(kind="block", events=[]))
            statement_start = index + 1
            continue

        if token.value == "}":
            if brace_stack:
                record = brace_stack.pop()

                for event in record.events:
                    event.close_line = token.line
                    event.end_line = token.line
                    event.end_col0 = token.col0

                if record.kind == "scope":
                    for _ in record.events:
                        if scope_stack:
                            scope_stack.pop()
            else:
                diagnostics.append(
                    Diagnostic(
                        severity="warning",
                        code="unmatched_brace",
                        message="Unmatched closing brace.",
                        start_line=token.line,
                        end_line=token.line,
                    )
                )

            statement_start = index + 1
            continue

        if token.value == ";":
            if is_inside_non_declaration_body(brace_stack):
                statement_start = index + 1
                continue

            raw_segment = tokens[statement_start:index]
            template_prefixes, segment = parse_template_prefixes(raw_segment, lines)

            if should_skip_declaration_statement(segment):
                statement_start = index + 1
                continue

            start_line, start_col0, signature = make_signature(
                lines=lines,
                segment=segment,
                fallback_token=token,
                end_col0_exclusive=token.col0 + len(token.value),
                template_prefixes=template_prefixes,
            )
            exported = current_scope_exported(scope_stack) or ("export" in token_values(segment[:2]))
            fragment = determine_fragment_for_line(start_line, module_info)
            event: StructuralEvent | None = None

            is_type_decl, type_kind, type_name, type_exported = classify_type_declaration(segment)

            if is_type_decl:
                event = make_event(
                    kind=type_kind,
                    name=type_name,
                    qualified_name=build_qualified_name(scope_stack, type_name),
                    start_line=start_line,
                    end_line=token.line,
                    start_col0=start_col0,
                    end_col0=token.col0,
                    open_brace_line=None,
                    open_brace_col0=None,
                    close_line=None,
                    signature=signature,
                    order=order,
                    exported=exported or type_exported,
                    fragment=fragment,
                    template=attach_template(type_kind, template_prefixes),
                )

            if event is None:
                is_enum_decl, enum_name, enum_exported = classify_enum(segment)

                if is_enum_decl:
                    event = make_event(
                        kind="enum",
                        name=enum_name,
                        qualified_name=build_qualified_name(scope_stack, enum_name),
                        start_line=start_line,
                        end_line=token.line,
                        start_col0=start_col0,
                        end_col0=token.col0,
                        open_brace_line=None,
                        open_brace_col0=None,
                        close_line=None,
                        signature=signature,
                        order=order,
                        exported=exported or enum_exported,
                        fragment=fragment,
                        template=attach_template("enum", template_prefixes),
                    )

            if event is None:
                is_function, function_kind, function_name, qualified_name = classify_function_like(
                    segment=segment,
                    scope_stack=scope_stack,
                    has_body=False,
                )

                if is_function:
                    event = make_event(
                        kind=function_kind,
                        name=function_name,
                        qualified_name=qualified_name,
                        start_line=start_line,
                        end_line=token.line,
                        start_col0=start_col0,
                        end_col0=token.col0,
                        open_brace_line=None,
                        open_brace_col0=None,
                        close_line=None,
                        signature=signature,
                        order=order,
                        exported=exported,
                        fragment=fragment,
                        template=attach_template(function_kind, template_prefixes),
                    )

            if event is not None:
                events.append(event)
                order += 1

            statement_start = index + 1
            continue

    if brace_stack:
        for record in brace_stack:
            if record.events:
                for event in record.events:
                    diagnostics.append(
                        Diagnostic(
                            severity="warning",
                            code="unmatched_brace",
                            message=(
                                "Unmatched opening brace record: "
                                f"recordKind={record.kind}, "
                                f"eventKind={event.kind}, "
                                f"name={event.qualified_name or event.name}, "
                                f"startLine={event.start_line}, "
                                f"openBraceLine={event.open_brace_line}"
                            ),
                            start_line=event.open_brace_line or event.start_line,
                            end_line=event.open_brace_line or event.start_line,
                        )
                    )
            else:
                diagnostics.append(
                    Diagnostic(
                        severity="warning",
                        code="unmatched_brace",
                        message=f"Unmatched opening brace record: recordKind={record.kind}",
                    )
                )

    scope_map = build_scope_map_from_events(events=events, total_lines=len(lines))
    scope_intervals = build_scope_intervals(events)
    function_body_ranges = build_function_body_ranges(events)

    return StructuralScanResult(
        events=events,
        scope_map=scope_map,
        scope_intervals=scope_intervals,
        function_body_ranges=function_body_ranges,
        diagnostics=diagnostics,
    )


# ---------------------------------------------------------------------------
# Derived range maps
# ---------------------------------------------------------------------------

def build_scope_map_from_events(
    *,
    events: list[StructuralEvent],
    total_lines: int,
) -> list[list[ScopeFrame]]:
    scope_events = [
        event
        for event in events
        if event.kind in {"namespace", "class", "struct"}
        and event.open_brace_line is not None
        and event.close_line is not None
    ]
    scope_events.sort(key=lambda event: (event.open_brace_line or 0, event.order))

    scope_map: list[list[ScopeFrame]] = []

    for line_no in range(1, total_lines + 1):
        stack: list[ScopeFrame] = []

        for event in scope_events:
            assert event.open_brace_line is not None
            assert event.close_line is not None

            # Scope is active inside the body, not on the declaration/opening line.
            if event.open_brace_line < line_no <= event.close_line:
                stack.append(
                    ScopeFrame(
                        kind=event.kind,
                        name=event.name,
                        qualified_name=event.qualified_name,
                        start_line=event.start_line,
                        inline=event.inline,
                        exported=event.exported,
                    )
                )

        scope_map.append(stack)

    return scope_map


def build_scope_intervals(events: list[StructuralEvent]) -> list[dict[str, Any]]:
    intervals: list[dict[str, Any]] = []

    for event in events:
        if event.kind not in {"namespace", "class", "struct", "enum"}:
            continue

        if event.end_line is None:
            continue

        intervals.append(
            {
                "kind": event.kind,
                "name": event.name,
                "qualifiedName": event.qualified_name,
                "range": event.range_json(),
                "openBraceLine": event.open_brace_line,
                "closeLine": event.close_line,
            }
        )

    intervals.sort(
        key=lambda item: (
            item["range"]["startLine"],
            item["range"]["endLine"],
            item.get("qualifiedName") or "",
        )
    )
    return intervals


def build_function_body_ranges(events: list[StructuralEvent]) -> list[dict[str, Any]]:
    ranges: list[dict[str, Any]] = []

    for event in events:
        if event.kind not in {
            "function",
            "method",
            "constructor",
            "destructor",
            "operator",
        }:
            continue

        if (
            event.open_brace_line is None
            or event.close_line is None
            or event.end_line is None
        ):
            continue

        inner_body_range = None

        if event.open_brace_line + 1 <= event.close_line - 1:
            inner_body_range = SourceRange(
                start_line=event.open_brace_line + 1,
                end_line=event.close_line - 1,
            ).to_json()

        ranges.append(
            {
                "range": event.range_json(),
                "signatureRange": SourceRange(
                    start_line=event.start_line,
                    end_line=event.open_brace_line,
                    start_col0=event.start_col0,
                    end_col0=event.open_brace_col0,
                ).to_json(),
                "bodyRange": SourceRange(
                    start_line=event.open_brace_line,
                    end_line=event.close_line,
                    start_col0=event.open_brace_col0,
                    end_col0=event.end_col0,
                ).to_json(),
                "innerBodyRange": inner_body_range,
                "qualifiedName": event.qualified_name,
            }
        )

    ranges.sort(key=lambda item: (item["range"]["startLine"], item["range"]["endLine"]))
    return ranges
