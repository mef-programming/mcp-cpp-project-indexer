from __future__ import annotations

from pathlib import Path
from typing import Any

from cpp_index_model import (
    INDEXER_NAME,
    INDEXER_VERSION,
    SCANNER_VERSION,
    SCHEMA_NAME,
    Diagnostic,
    SourceRange,
    StructuralEvent,
)
from cpp_index_utils import (
    decode_source,
    detect_newline_kind,
    make_content_hash,
    make_file_id,
    make_path_hash,
    normalized_relative_path,
    save_json,
    split_source_lines_preserve_count,
    utc_now_iso,
)
from cpp_lexer import blank_comments_preserve_lines
from cpp_module_scan import ModuleScanResult, scan_module_facts
from cpp_structural_scan import StructuralScanResult, scan_structure
from cpp_symbol_emit import emit_symbols_from_events, structural_events_to_json
from cpp_data_emit import emit_data_declarations
from cpp_type_alias_emit import emit_type_alias_symbols

# ---------------------------------------------------------------------------
# Export entries derived from structural events
# ---------------------------------------------------------------------------

def export_entry_from_event(event: StructuralEvent) -> dict[str, Any] | None:
    if not event.exported:
        return None

    signature = event.signature.strip()

    # Only direct export syntax becomes an exports[] entry. Symbols inside an
    # exported namespace/block carry visibility metadata, but do not each need a
    # separate export record in this routing index.
    if not signature.startswith("export "):
        return None

    if event.kind == "namespace":
        export_kind = "export_namespace"
    else:
        export_kind = "export_declaration"

    return {
        "kind": export_kind,
        "range": event.range_json(),
        "signature": signature,
        "fragment": event.fragment,
    }


def export_entries_from_events(events: list[StructuralEvent]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()

    for event in events:
        entry = export_entry_from_event(event)

        if entry is None:
            continue

        key = (
            entry["kind"],
            entry["range"]["startLine"],
            entry["range"]["endLine"],
            entry["signature"],
        )

        if key in seen:
            continue

        seen.add(key)
        entries.append(entry)

    entries.sort(
        key=lambda item: (
            item["range"]["startLine"],
            item["range"]["endLine"],
            item["kind"],
        )
    )
    return entries


def merge_export_entries(
    module_exports: list[dict[str, Any]],
    structural_exports: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()

    for entry in [*module_exports, *structural_exports]:
        key = (
            entry["kind"],
            entry["range"]["startLine"],
            entry["range"]["endLine"],
            entry["signature"],
        )

        if key in seen:
            continue

        seen.add(key)
        merged.append(entry)

    merged.sort(
        key=lambda item: (
            item["range"]["startLine"],
            item["range"]["endLine"],
            item["kind"],
        )
    )
    return merged


# ---------------------------------------------------------------------------
# Diagnostics / source metadata
# ---------------------------------------------------------------------------

def diagnostics_to_json(diagnostics: list[Diagnostic]) -> list[dict[str, Any]]:
    return [diagnostic.to_json() for diagnostic in diagnostics]


def extension_for_path(path: Path) -> str:
    return path.suffix


def display_name_for_path(path: Path) -> str:
    return path.name


def language_for_path(path: Path) -> str:
    # MVP only supports C++ routing facts.
    return "cpp"


# ---------------------------------------------------------------------------
# File index builder
# ---------------------------------------------------------------------------

def build_file_index(
    *,
    path: Path,
    project_root: Path | None = None,
    case_insensitive_paths: bool = True,
    blank_comments: bool = True,
    emit_debug: bool = False,
) -> dict[str, Any]:
    raw = path.read_bytes()
    source_text, encoding = decode_source(raw)
    newline = detect_newline_kind(raw)
    raw_lines = split_source_lines_preserve_count(source_text)
    line_count = len(raw_lines)

    relative_path = normalized_relative_path(path, project_root)
    path_hash = make_path_hash(
        relative_path,
        case_insensitive_paths=case_insensitive_paths,
    )
    content_hash = make_content_hash(raw)
    file_id = make_file_id(path_hash)

    scanner_lines = list(raw_lines)

    if blank_comments:
        scanner_lines = blank_comments_preserve_lines(scanner_lines)

    module_scan = scan_module_facts(scanner_lines)
    structural_scan = scan_structure(
        scanner_lines,
        module_info=module_scan.module,
    )

    all_events = [
        *module_scan.structural_events,
        *structural_scan.events,
    ]

    symbols, symbol_diagnostics = emit_symbols_from_events(
        file_id=file_id,
        events=structural_scan.events,
        module_info=module_scan.module,
        line_count=line_count,
    )

    type_alias_symbols, type_alias_diagnostics = emit_type_alias_symbols(
        file_id=file_id,
        lines=raw_lines,
        structural_events=structural_scan.events,
        module_info=module_scan.module,
    )

    symbols.extend(type_alias_symbols)

    data_items, data_diagnostics = emit_data_declarations(
        file_id=file_id,
        lines=raw_lines,
        structural_events=structural_scan.events,
        module_info=module_scan.module,
    )

    exports = merge_export_entries(
        module_scan.exports,
        export_entries_from_events(structural_scan.events),
    )

    diagnostics = [
        *module_scan.diagnostics,
        *structural_scan.diagnostics,
        *symbol_diagnostics,
        *type_alias_diagnostics,
        *data_diagnostics,
    ]

    file_index = {
        "schema": SCHEMA_NAME,
        "indexer": {
            "name": INDEXER_NAME,
            "version": INDEXER_VERSION,
            "scannerVersion": SCANNER_VERSION,
            "createdUtc": utc_now_iso(),
            "settings": {
                "blankComments": blank_comments,
                "caseInsensitivePaths": case_insensitive_paths,
                "includeIndexing": False,
                "analysis": False,
            },
            "idAlgorithm": (
                "fileId = 'f_' + first 24 hex chars of "
                "SHA-256(normalized project-relative path); "
                "symbolId = 's_' + short fileId + '_' + "
                "padded line range + '_' + first 12 hex chars of signatureHash"
            ),
            "hashAlgorithm": "sha256",
        },
        "fileId": file_id,
        "relativePath": relative_path,
        "displayName": display_name_for_path(path),
        "extension": extension_for_path(path),
        "language": language_for_path(path),
        "pathHash": path_hash,
        "contentHash": content_hash,
        "lineCount": line_count,
        "tokenCount": structural_scan.token_count,
        "encoding": encoding,
        "newline": newline,
        "module": module_scan.module,
        "imports": module_scan.imports,
        "includes": module_scan.includes,
        "exports": exports,
        "symbols": symbols,
        "data": data_items,
        "diagnostics": diagnostics_to_json(diagnostics),
    }

    if emit_debug:
        file_index["scopeIntervals"] = structural_scan.scope_intervals
        file_index["structuralEvents"] = structural_events_to_json(all_events)
        file_index["functionBodyRanges"] = structural_scan.function_body_ranges

    return file_index

def build_and_save_file_index(
    *,
    path: Path,
    output: Path,
    project_root: Path | None = None,
    case_insensitive_paths: bool = True,
    blank_comments: bool = True,
    emit_debug: bool = False,
) -> dict[str, Any]:
    file_index = build_file_index(
        path=path,
        project_root=project_root,
        case_insensitive_paths=case_insensitive_paths,
        blank_comments=blank_comments,
        emit_debug=emit_debug,
    )

    save_json(output, file_index)
    return file_index


# ---------------------------------------------------------------------------
# Lightweight summary helpers for CLI output/tests
# ---------------------------------------------------------------------------

def summarize_file_index(file_index: dict[str, Any]) -> dict[str, Any]:
    module = file_index["module"]

    return {
        "file": file_index["relativePath"],
        "fileId": file_index["fileId"],
        "lineCount": file_index["lineCount"],
        "tokenCount": file_index.get("tokenCount", 0),
        "unitKind": module["unitKind"],
        "fullModuleName": module["fullModuleName"],
        "imports": len(file_index.get("imports", [])),
        "exports": len(file_index.get("exports", [])),
        "symbols": len(file_index.get("symbols", [])),
        "data": len(file_index.get("data", [])),
        "scopeIntervals": len(file_index.get("scopeIntervals", [])),
        "structuralEvents": len(file_index.get("structuralEvents", [])),
        "functionBodyRanges": len(file_index.get("functionBodyRanges", [])),
        "diagnostics": len(file_index.get("diagnostics", [])),
    }
