from __future__ import annotations

from typing import Any

from cpp_index_model import (
    FUNCTION_SPECIFIERS,
    STORAGE_SPECIFIERS,
    SYMBOL_TYPES,
    Diagnostic,
    StructuralEvent,
    Token,
)
from cpp_index_utils import (
    container_from_qualified_name,
    make_signature_hash,
    make_symbol_id,
    normalize_signature_spacing,
    require_valid_line_range,
    short_name_from_qualified_name,
)
from cpp_lexer import (
    find_matching_token,
    split_top_level_commas,
    tokenize_lines,
    token_values,
    tokens_to_text,
)


# ---------------------------------------------------------------------------
# Signature-key helpers
# ---------------------------------------------------------------------------

def _tokens_from_signature(signature: str) -> list[Token]:
    # The lexer operates on source lines. For signature-key extraction the
    # already-normalized visible signature is enough; line/column values are not
    # used here.
    return tokenize_lines([signature])


def _find_function_parameter_parens(tokens: list[Token]) -> tuple[int, int] | None:
    candidates: list[tuple[int, int]] = []

    for index, token in enumerate(tokens):
        if token.value != "(":
            continue

        close = find_matching_token(tokens, index, "(", ")")

        if close is None:
            continue

        # Avoid common non-declarator parens. This is best-effort only; the raw
        # normalized signature remains the real identity fallback.
        previous = tokens[index - 1].value if index > 0 else ""

        if previous in {"decltype", "sizeof", "alignof", "noexcept", "requires"}:
            continue

        candidates.append((index, close))

    if not candidates:
        return None

    return candidates[-1]


def _parameter_key_from_tokens(tokens: list[Token]) -> dict[str, Any]:
    text = tokens_to_text(tokens)

    if not text or text == "void":
        return {
            "text": text,
            "typeText": text,
            "name": None,
            "defaultValuePresent": False,
        }

    default_value_present = any(token.value == "=" for token in tokens)
    type_tokens = list(tokens)

    if default_value_present:
        eq_index = next(index for index, token in enumerate(type_tokens) if token.value == "=")
        type_tokens = type_tokens[:eq_index]

    name: str | None = None

    if type_tokens:
        last = type_tokens[-1]

        if last.kind == "identifier":
            # Avoid treating common type-only declarations as names too eagerly.
            # This is deliberately conservative; V1 identity still uses text.
            if len(type_tokens) > 1:
                name = last.value
                type_tokens = type_tokens[:-1]

    type_text = tokens_to_text(type_tokens) if type_tokens else text

    return {
        "text": text,
        "typeText": type_text,
        "name": name,
        "defaultValuePresent": default_value_present,
    }


def extract_parameters(tokens: list[Token]) -> list[dict[str, Any]]:
    parens = _find_function_parameter_parens(tokens)

    if parens is None:
        return []

    open_index, close_index = parens
    parameter_tokens = tokens[open_index + 1 : close_index]

    if not parameter_tokens:
        return []

    parts = split_top_level_commas(parameter_tokens)

    parameters = [
        _parameter_key_from_tokens(part)
        for part in parts
        if part
    ]

    if len(parameters) == 1 and parameters[0]["text"] == "void":
        return []

    return parameters


def extract_cv_qualifier(tokens: list[Token]) -> str:
    values = token_values(tokens)
    has_const = "const" in values
    has_volatile = "volatile" in values

    if has_const and has_volatile:
        return "const volatile"

    if has_const:
        return "const"

    if has_volatile:
        return "volatile"

    return ""


def extract_ref_qualifier(tokens: list[Token]) -> str:
    values = token_values(tokens)

    if "&&" in values:
        return "&&"

    if "&" in values:
        return "&"

    return ""


def extract_noexcept_spec(tokens: list[Token]) -> str:
    values = token_values(tokens)

    if "noexcept" not in values:
        return ""

    index = values.index("noexcept")

    if index + 1 < len(tokens) and tokens[index + 1].value == "(":
        close = find_matching_token(tokens, index + 1, "(", ")")

        if close is not None:
            return tokens_to_text(tokens[index : close + 1])

    return "noexcept"


def extract_requires_clause(tokens: list[Token]) -> str:
    values = token_values(tokens)

    if "requires" not in values:
        return ""

    index = values.index("requires")
    return tokens_to_text(tokens[index:])


def extract_attributes(tokens: list[Token]) -> list[str]:
    attributes: list[str] = []
    index = 0

    while index + 1 < len(tokens):
        if tokens[index].value == "[" and tokens[index + 1].value == "[":
            depth = 0
            start = index

            while index < len(tokens):
                if tokens[index].value == "[":
                    depth += 1
                elif tokens[index].value == "]":
                    depth -= 1

                    if depth == 0:
                        attributes.append(tokens_to_text(tokens[start : index + 1]))
                        break

                index += 1

        index += 1

    return attributes


def extract_storage(tokens: list[Token]) -> list[str]:
    seen: list[str] = []

    for token in tokens:
        if token.value in STORAGE_SPECIFIERS and token.value not in seen:
            seen.append(token.value)

    return seen


def extract_function_specifiers(tokens: list[Token]) -> list[str]:
    seen: list[str] = []
    values = token_values(tokens)

    for token in tokens:
        value = token.value

        if value in FUNCTION_SPECIFIERS and value not in seen:
            seen.append(value)

    if "=" in values:
        eq_index = values.index("=")

        if eq_index + 1 < len(values):
            if values[eq_index + 1] == "default" and "defaulted" not in seen:
                seen.append("defaulted")
            elif values[eq_index + 1] == "delete" and "deleted" not in seen:
                seen.append("deleted")
            elif values[eq_index + 1] == "0" and "pure_virtual" not in seen:
                seen.append("pure_virtual")

    return seen


def extract_operator_kind(event: StructuralEvent) -> str:
    if not event.kind.startswith("operator"):
        return ""

    return short_name_from_qualified_name(event.qualified_name) or event.name


def build_signature_key(event: StructuralEvent) -> dict[str, Any]:
    signature = normalize_signature_spacing(event.signature)
    tokens = _tokens_from_signature(signature)

    return {
        "qualifiedName": event.qualified_name,
        "symbolType": event.kind,
        "rawNormalizedSignature": signature,
        "templateParameters": (
            event.template.to_json()["parameters"]
            if event.template is not None
            else []
        ),
        "returnType": None,
        "cvQualifier": extract_cv_qualifier(tokens),
        "refQualifier": extract_ref_qualifier(tokens),
        "noexceptSpec": extract_noexcept_spec(tokens),
        "requiresClause": extract_requires_clause(tokens),
        "attributes": extract_attributes(tokens),
        "storage": extract_storage(tokens),
        "functionSpecifiers": extract_function_specifiers(tokens),
        "callingConvention": "",
        "operatorKind": extract_operator_kind(event),
    }


# ---------------------------------------------------------------------------
# Symbol emission
# ---------------------------------------------------------------------------

def is_symbol_event(event: StructuralEvent) -> bool:
    if event.kind == "namespace" and event.name.startswith("<anonymous"):
        return False

    return event.kind in SYMBOL_TYPES

def range_kind_for_event(event: StructuralEvent) -> str:
    if event.kind in {"namespace", "class", "struct", "enum"} and event.open_brace_line is not None:
        return "container"

    if event.kind.endswith("_declaration"):
        if event.kind in {"class_declaration", "struct_declaration"}:
            return "forward_declaration"

        return "declaration"

    if event.open_brace_line is not None:
        return "definition"

    return "declaration"


def export_source_for_event(event: StructuralEvent) -> str:
    if not event.exported:
        return "none"

    stripped = event.signature.strip()

    if stripped.startswith("export "):
        if event.kind == "namespace":
            return "direct_export"

        return "direct_export"

    return "enclosing_export_namespace"


def module_context_for_event(
    *,
    event: StructuralEvent,
    module_info: dict[str, Any],
) -> dict[str, Any]:
    return {
        "fullModuleName": module_info.get("fullModuleName"),
        "fragment": event.fragment,
    }


def symbol_from_event(
    *,
    event: StructuralEvent,
    file_id: str,
    module_info: dict[str, Any],
    line_count: int,
) -> tuple[dict[str, Any] | None, Diagnostic | None]:
    if not is_symbol_event(event):
        return None, None

    end_line = event.effective_end_line()

    try:
        require_valid_line_range(
            start_line=event.start_line,
            end_line=end_line,
            line_count=line_count,
            context=event.qualified_name or event.name or event.kind,
        )
    except ValueError as exc:
        return None, Diagnostic(
            severity="warning",
            code="dropped_symbol_invalid_range",
            message=str(exc),
            start_line=event.start_line,
            end_line=end_line,
        )

    name = event.qualified_name or event.name

    if not name:
        return None, Diagnostic(
            severity="warning",
            code="dropped_symbol_invalid_range",
            message=f"Dropped unnamed symbol event kind={event.kind}.",
            start_line=event.start_line,
            end_line=end_line,
        )

    signature_key = build_signature_key(event)
    signature_hash = make_signature_hash(signature_key)
    symbol_id = make_symbol_id(
        file_id=file_id,
        start_line=event.start_line,
        end_line=end_line,
        signature_hash=signature_hash,
    )

    symbol: dict[str, Any] = {
        "symbolId": symbol_id,
        "type": event.kind,
        "container": container_from_qualified_name(name),
        "startLine": event.start_line,
        "endLine": end_line,
        "signature": normalize_signature_spacing(event.signature),
        "signatureHash": signature_hash,
        "exported": event.exported,
        "moduleFragment": event.fragment,
    }

    if event.template is not None:
        symbol["template"] = {
            "kind": event.template.target_kind,
            "startLine": event.template.prefixes[0].start_line,
            "endLine": end_line,
        }

    return symbol, None


def emit_symbols_from_events(
    *,
    file_id: str,
    events: list[StructuralEvent],
    module_info: dict[str, Any],
    line_count: int,
) -> tuple[list[dict[str, Any]], list[Diagnostic]]:
    symbols: list[dict[str, Any]] = []
    diagnostics: list[Diagnostic] = []
    seen_symbol_ids: set[str] = set()

    for event in events:
        symbol, diagnostic = symbol_from_event(
            event=event,
            file_id=file_id,
            module_info=module_info,
            line_count=line_count,
        )

        if diagnostic is not None:
            diagnostics.append(diagnostic)

        if symbol is None:
            continue

        symbol_id = symbol["symbolId"]

        if symbol_id in seen_symbol_ids:
            diagnostics.append(
                Diagnostic(
                    severity="warning",
                    code="dropped_symbol_invalid_range",
                    message=f"Duplicate symbolId generated and dropped: {symbol_id}",
                    start_line=symbol["range"]["startLine"],
                    end_line=symbol["range"]["endLine"],
                )
            )
            continue

        seen_symbol_ids.add(symbol_id)
        symbols.append(symbol)

    symbols.sort(
    key=lambda symbol: (
        symbol.get("startLine", symbol.get("range", {}).get("startLine", 10**9)),
        symbol.get("endLine", symbol.get("range", {}).get("endLine", 10**9)),
        symbol.get("container") or "",
        symbol.get("signature") or "",
        symbol.get("type") or "",
        )
    )

    return symbols, diagnostics


# ---------------------------------------------------------------------------
# Structural event JSON emission
# ---------------------------------------------------------------------------

def structural_event_to_json(event: StructuralEvent) -> dict[str, Any]:
    result: dict[str, Any] = {
        "kind": event.kind,
        "range": event.range_json(),
        "name": event.name,
        "qualifiedName": event.qualified_name,
        "signature": normalize_signature_spacing(event.signature),
        "openBraceLine": event.open_brace_line,
        "closeLine": event.close_line,
        "exported": event.exported,
        "fragment": event.fragment,
    }

    if event.template is not None:
        result["templateTargetKind"] = event.template.target_kind
        result["templateSpecializationKind"] = event.template.specialization_kind

    return result


def structural_events_to_json(events: list[StructuralEvent]) -> list[dict[str, Any]]:
    return [
        structural_event_to_json(event)
        for event in sorted(
            events,
            key=lambda item: (
                item.start_line,
                item.effective_end_line(),
                item.order,
            ),
        )
    ]
