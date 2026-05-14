from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from cpp_index_model import Diagnostic, SourceRange, StructuralEvent, Token
from cpp_index_utils import normalize_signature_spacing, source_text_range
from cpp_lexer import tokenize_lines, tokens_to_text


@dataclass(slots=True)
class ModuleScanResult:
    module: dict[str, Any]
    imports: list[dict[str, Any]]
    includes: list[dict[str, Any]]
    exports: list[dict[str, Any]]
    structural_events: list[StructuralEvent]
    diagnostics: list[Diagnostic]


# ---------------------------------------------------------------------------
# Statement stream
# ---------------------------------------------------------------------------

def iter_semicolon_statements(tokens: list[Token]):
    start = 0

    for index, token in enumerate(tokens):
        if token.value != ";":
            continue

        statement = tokens[start : index + 1]

        if statement:
            yield statement

        start = index + 1


def statement_values(statement: list[Token]) -> list[str]:
    return [token.value for token in statement]


def statement_range(statement: list[Token]) -> SourceRange:
    first = statement[0]
    last = statement[-1]

    return SourceRange(
        start_line=first.line,
        end_line=last.line,
        start_col0=first.col0,
        end_col0=last.col0,
    )


def statement_text(lines: list[str], statement: list[Token]) -> str:
    first = statement[0]
    last = statement[-1]

    return source_text_range(
        lines,
        first.line,
        last.line,
        last.col0 + len(last.value),
    )


# ---------------------------------------------------------------------------
# Token-level parsing helpers
# ---------------------------------------------------------------------------

def parse_dotted_name(tokens: list[Token], index: int) -> tuple[str | None, int]:
    parts: list[str] = []

    if index >= len(tokens) or tokens[index].kind != "identifier":
        return None, index

    parts.append(tokens[index].value)
    index += 1

    while index + 1 < len(tokens):
        if tokens[index].value != ".":
            break

        if tokens[index + 1].kind != "identifier":
            break

        parts.append(tokens[index + 1].value)
        index += 2

    return ".".join(parts), index


def parse_module_declaration(statement: list[Token]) -> dict[str, Any] | None:
    values = statement_values(statement)

    if not values:
        return None

    index = 0
    exported = False

    if values[index] == "export":
        exported = True
        index += 1

    if index >= len(statement) or statement[index].value != "module":
        return None

    module_token = statement[index]
    index += 1

    # global module fragment: module;
    if index < len(statement) and statement[index].value == ";":
        return {
            "kind": "global_module_fragment",
            "exported": False,
            "moduleName": None,
            "partitionName": None,
            "fullModuleName": None,
            "startLine": module_token.line,
            "endLine": statement[-1].line,
            "startCol0": module_token.col0,
            "endCol0": statement[-1].col0,
        }

    # private module fragment: module : private;
    if (
        index + 2 < len(statement)
        and statement[index].value == ":"
        and statement[index + 1].value == "private"
        and statement[index + 2].value == ";"
    ):
        return {
            "kind": "private_module_fragment",
            "exported": False,
            "moduleName": None,
            "partitionName": None,
            "fullModuleName": None,
            "startLine": module_token.line,
            "endLine": statement[-1].line,
            "startCol0": module_token.col0,
            "endCol0": statement[-1].col0,
        }

    module_name, index = parse_dotted_name(statement, index)

    if module_name is None:
        return None

    partition_name = None

    if index < len(statement) and statement[index].value == ":":
        partition_name, index = parse_dotted_name(statement, index + 1)

        if partition_name is None:
            return None

    if index >= len(statement) or statement[index].value != ";":
        return None

    full_module_name = module_name

    if partition_name:
        full_module_name = f"{module_name}:{partition_name}"

    return {
        "kind": "module_declaration",
        "exported": exported,
        "moduleName": module_name,
        "partitionName": partition_name,
        "fullModuleName": full_module_name,
        "startLine": module_token.line,
        "endLine": statement[-1].line,
        "startCol0": module_token.col0,
        "endCol0": statement[-1].col0,
    }


def parse_import_declaration(statement: list[Token]) -> dict[str, Any] | None:
    values = statement_values(statement)

    if not values:
        return None

    index = 0
    exported = False

    if values[index] == "export":
        exported = True
        index += 1

    if index >= len(statement) or statement[index].value != "import":
        return None

    import_token = statement[index]
    index += 1

    if index >= len(statement):
        return None

    raw_tokens: list[Token] = []

    while index < len(statement) and statement[index].value != ";":
        raw_tokens.append(statement[index])
        index += 1

    if index >= len(statement) or statement[index].value != ";":
        return None

    if not raw_tokens:
        return None

    raw_module = import_target_text(raw_tokens)

    return {
        "kind": "import_declaration",
        "exported": exported,
        "rawModule": raw_module,
        "startLine": import_token.line,
        "endLine": statement[-1].line,
        "startCol0": import_token.col0,
        "endCol0": statement[-1].col0,
    }


def import_target_text(tokens: list[Token]) -> str:
    if not tokens:
        return ""

    # Partition import:
    #   import :ElementImpl;
    # must become:
    #   :ElementImpl
    if tokens[0].value == ":":
        return ":" + tokens_to_text(tokens[1:]).lstrip()

    # Header unit:
    #   import <vector>;
    if tokens[0].value == "<":
        return tokens_to_text(tokens).replace("< ", "<").replace(" >", ">")

    # Normal module name:
    #   import Foo.Bar;
    return tokens_to_text(tokens).replace(" . ", ".")


def classify_module_unit_kind(
    *,
    is_module_unit: bool,
    exported: bool,
    partition_name: str | None,
) -> str:
    if not is_module_unit:
        return "non_module"

    if exported and partition_name:
        return "module_partition_interface"

    if exported:
        return "module_interface"

    if partition_name:
        return "module_partition_implementation"

    return "module_implementation"


def classify_import_kind(raw_module: str) -> str:
    text = raw_module.strip()

    if text.startswith("<") and text.endswith(">"):
        return "header_unit_import"

    if text == "std" or text.startswith("std."):
        return "std_import"

    if text.startswith(":"):
        return "module_partition_import"

    # Stream parser already tokenized this; this final check is intentionally
    # simple and only classifies the normalized token text.
    if text and all(part and (part[0].isalpha() or part[0] == "_") for part in text.split(".")):
        return "module_import"

    return "unknown_import"


def resolve_import_module(
    *,
    raw_module: str,
    import_kind: str,
    module_name: str | None,
) -> str | None:
    raw_module = raw_module.replace(": ", ":")
    
    if import_kind == "module_partition_import":
        if module_name is None:
            return None

        return f"{module_name}{raw_module}"

    return raw_module


def determine_fragment_for_line(line_no: int, module_info: dict[str, Any]) -> str:
    global_fragment = module_info["globalModuleFragment"]
    private_fragment = module_info["privateModuleFragment"]

    if (
        global_fragment["present"]
        and global_fragment["startLine"] <= line_no <= global_fragment["endLine"]
    ):
        return "global_module_fragment"

    if (
        private_fragment["present"]
        and private_fragment["startLine"] <= line_no <= private_fragment["endLine"]
    ):
        return "private_module_fragment"

    if module_info["isModuleUnit"]:
        return "module_purview"

    return "unknown"


# ---------------------------------------------------------------------------
# JSON/event builders
# ---------------------------------------------------------------------------

def make_module_structural_event(
    *,
    kind: str,
    start_line: int,
    end_line: int,
    start_col0: int,
    end_col0: int,
    signature: str,
    order: int,
    exported: bool,
    fragment: str,
) -> StructuralEvent:
    return StructuralEvent(
        kind=kind,
        name="",
        qualified_name="",
        start_line=start_line,
        end_line=end_line,
        start_col0=start_col0,
        end_col0=end_col0,
        open_brace_line=None,
        open_brace_col0=None,
        close_line=None,
        signature=signature,
        order=order,
        inline=False,
        exported=exported,
        fragment=fragment,
        template=None,
    )


def make_module_info(
    *,
    total_lines: int,
    global_fragment_decl: dict[str, Any] | None,
    module_decl: dict[str, Any] | None,
    private_fragment_decl: dict[str, Any] | None,
    module_decl_text: str | None,
) -> dict[str, Any]:
    is_module_unit = module_decl is not None
    module_name = module_decl["moduleName"] if module_decl else None
    partition_name = module_decl["partitionName"] if module_decl else None
    full_module_name = module_decl["fullModuleName"] if module_decl else None
    exported = bool(module_decl and module_decl["exported"])

    if global_fragment_decl is not None:
        if module_decl is not None:
            global_end = max(global_fragment_decl["startLine"], module_decl["startLine"] - 1)
        else:
            global_end = total_lines

        global_fragment = {
            "present": True,
            "startLine": global_fragment_decl["startLine"],
            "endLine": global_end,
            "declarationLine": global_fragment_decl["startLine"],
        }
    else:
        global_fragment = {
            "present": False,
            "startLine": None,
            "endLine": None,
            "declarationLine": None,
        }

    if private_fragment_decl is not None:
        private_fragment = {
            "present": True,
            "startLine": private_fragment_decl["startLine"],
            "endLine": total_lines,
            "declarationLine": private_fragment_decl["startLine"],
        }
    else:
        private_fragment = {
            "present": False,
            "startLine": None,
            "endLine": None,
            "declarationLine": None,
        }

    module_declaration = None

    if module_decl is not None:
        module_declaration = {
            "startLine": module_decl["startLine"],
            "endLine": module_decl["endLine"],
            "text": module_decl_text or "",
        }

    return {
        "isModuleUnit": is_module_unit,
        "unitKind": classify_module_unit_kind(
            is_module_unit=is_module_unit,
            exported=exported,
            partition_name=partition_name,
        ),
        "moduleName": module_name,
        "partitionName": partition_name,
        "fullModuleName": full_module_name,
        "isExportedModuleDeclaration": exported,
        "globalModuleFragment": global_fragment,
        "moduleDeclaration": module_declaration,
        "privateModuleFragment": private_fragment,
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def scan_module_facts(lines: list[str]) -> ModuleScanResult:
    tokens = tokenize_lines(lines)
    total_lines = len(lines)

    diagnostics: list[Diagnostic] = []
    structural_events: list[StructuralEvent] = []

    global_fragment_decl: dict[str, Any] | None = None
    private_fragment_decl: dict[str, Any] | None = None
    module_decl: dict[str, Any] | None = None
    module_decl_text: str | None = None
    parsed_imports: list[dict[str, Any]] = []

    order = 0

    for statement in iter_semicolon_statements(tokens):
        module_parse = parse_module_declaration(statement)

        if module_parse is not None:
            signature = statement_text(lines, statement)

            if module_parse["kind"] == "global_module_fragment":
                if global_fragment_decl is None:
                    global_fragment_decl = module_parse
                else:
                    diagnostics.append(
                        Diagnostic(
                            severity="warning",
                            code="unknown_module_declaration",
                            message="Duplicate global module fragment declaration.",
                            start_line=module_parse["startLine"],
                            end_line=module_parse["endLine"],
                        )
                    )

                structural_events.append(
                    make_module_structural_event(
                        kind="global_module_fragment",
                        start_line=module_parse["startLine"],
                        end_line=module_parse["endLine"],
                        start_col0=module_parse["startCol0"],
                        end_col0=module_parse["endCol0"],
                        signature=signature,
                        order=order,
                        exported=False,
                        fragment="global_module_fragment",
                    )
                )
                order += 1
                continue

            if module_parse["kind"] == "private_module_fragment":
                if private_fragment_decl is None:
                    private_fragment_decl = module_parse
                else:
                    diagnostics.append(
                        Diagnostic(
                            severity="warning",
                            code="unexpected_private_module_fragment",
                            message="Duplicate private module fragment declaration.",
                            start_line=module_parse["startLine"],
                            end_line=module_parse["endLine"],
                        )
                    )

                # Fragment is resolved again after module_info is known.
                structural_events.append(
                    make_module_structural_event(
                        kind="private_module_fragment",
                        start_line=module_parse["startLine"],
                        end_line=module_parse["endLine"],
                        start_col0=module_parse["startCol0"],
                        end_col0=module_parse["endCol0"],
                        signature=signature,
                        order=order,
                        exported=False,
                        fragment="private_module_fragment",
                    )
                )
                order += 1
                continue

            if module_parse["kind"] == "module_declaration":
                if module_decl is None:
                    module_decl = module_parse
                    module_decl_text = signature
                else:
                    diagnostics.append(
                        Diagnostic(
                            severity="warning",
                            code="duplicate_module_declaration",
                            message="Duplicate module declaration detected; keeping the first one.",
                            start_line=module_parse["startLine"],
                            end_line=module_parse["endLine"],
                        )
                    )

                structural_events.append(
                    make_module_structural_event(
                        kind="module_declaration",
                        start_line=module_parse["startLine"],
                        end_line=module_parse["endLine"],
                        start_col0=module_parse["startCol0"],
                        end_col0=module_parse["endCol0"],
                        signature=signature,
                        order=order,
                        exported=module_parse["exported"],
                        fragment="module_purview",
                    )
                )
                order += 1
                continue

        import_parse = parse_import_declaration(statement)

        if import_parse is not None:
            parsed_imports.append(
                {
                    **import_parse,
                    "text": statement_text(lines, statement),
                }
            )

            structural_events.append(
                make_module_structural_event(
                    kind="import_declaration",
                    start_line=import_parse["startLine"],
                    end_line=import_parse["endLine"],
                    start_col0=import_parse["startCol0"],
                    end_col0=import_parse["endCol0"],
                    signature=statement_text(lines, statement),
                    order=order,
                    exported=import_parse["exported"],
                    fragment="unknown",
                )
            )
            order += 1
            continue

    module_info = make_module_info(
        total_lines=total_lines,
        global_fragment_decl=global_fragment_decl,
        module_decl=module_decl,
        private_fragment_decl=private_fragment_decl,
        module_decl_text=module_decl_text,
    )

    # Now that module_info exists, fill import/export fragment data.
    imports: list[dict[str, Any]] = []
    exports: list[dict[str, Any]] = []

    if module_decl is not None and module_decl["exported"]:
        exports.append(
            {
                "kind": "export_module_declaration",
                "range": SourceRange(
                    start_line=module_decl["startLine"],
                    end_line=module_decl["endLine"],
                    start_col0=module_decl["startCol0"],
                    end_col0=module_decl["endCol0"],
                ).to_json(),
                "signature": module_decl_text or "",
                "fragment": determine_fragment_for_line(module_decl["startLine"], module_info),
            }
        )

    for item in parsed_imports:
        import_kind = classify_import_kind(item["rawModule"])
        resolved_module = resolve_import_module(
            raw_module=item["rawModule"],
            import_kind=import_kind,
            module_name=module_info["moduleName"],
        )

        if import_kind == "module_partition_import" and resolved_module is None:
            diagnostics.append(
                Diagnostic(
                    severity="warning",
                    code="unresolved_partition_import",
                    message=f"Could not resolve partition import {item['rawModule']} without current module name.",
                    start_line=item["startLine"],
                    end_line=item["endLine"],
                )
            )

        fragment = determine_fragment_for_line(item["startLine"], module_info)

        imports.append(
            {
                "kind": import_kind,
                "module": item["rawModule"],
                "resolvedModule": resolved_module,
                "isExported": item["exported"],
                "range": SourceRange(
                    start_line=item["startLine"],
                    end_line=item["endLine"],
                    start_col0=item["startCol0"],
                    end_col0=item["endCol0"],
                ).to_json(),
                "text": item["text"],
                "fragment": fragment,
            }
        )

        if item["exported"]:
            exports.append(
                {
                    "kind": "export_import",
                    "range": SourceRange(
                        start_line=item["startLine"],
                        end_line=item["endLine"],
                        start_col0=item["startCol0"],
                        end_col0=item["endCol0"],
                    ).to_json(),
                    "signature": item["text"],
                    "fragment": fragment,
                }
            )

    # MVP decision: includes are intentionally ignored and never indexed here.
    includes: list[dict[str, Any]] = []

    # Update unknown event fragments now that module_info is known.
    for event in structural_events:
        if event.fragment == "unknown":
            event.fragment = determine_fragment_for_line(event.start_line, module_info)

    return ModuleScanResult(
        module=module_info,
        imports=imports,
        includes=includes,
        exports=exports,
        structural_events=structural_events,
        diagnostics=diagnostics,
    )
