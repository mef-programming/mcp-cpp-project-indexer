from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any,Callable
from fnmatch import fnmatchcase

import os
import time
from concurrent.futures import ProcessPoolExecutor, as_completed

from cpp_file_index import build_file_index
from cpp_index_utils import save_json
from cpp_lexer import find_matching_token, tokenize_lines, token_values
from cpp_structural_scan import extract_function_name


DEFAULT_SOURCE_EXTENSIONS = {
    ".c",
    ".cc",
    ".cpp",
    ".cxx",
    ".h",
    ".hh",
    ".hpp",
    ".hxx",
    ".ixx",
    ".cppm",
}

DEFAULT_EXCLUDED_DIR_NAMES = {
    ".git",
    ".vs",
    ".vscode",
    "build",
    "out",
    "bin",
    "obj",
    "x64",
    "x86",
    "arm64",
    "Debug",
    "Release",
    "RelWithDebInfo",
    "MinSizeRel",
    "node_modules",
    "__pycache__",
}

PROJECT_INDEX_SCHEMA = "cpp.project_index.v1"


@dataclass(slots=True)
class ProjectIndexBuildResult:
    root: Path
    output_root: Path
    files_count: int
    symbols_count: int
    names_count: int
    data_count: int
    data_names_count: int
    modules_count: int
    diagnostics_count: int


# ---------------------------------------------------------------------------
# File discovery
# ---------------------------------------------------------------------------

def should_skip_dir(path: Path, excluded_dir_names: set[str]) -> bool:
    return any(part in excluded_dir_names for part in path.parts)


def discover_source_files(
    root: Path,
    *,
    extensions: set[str] | None = None,
    excluded_dir_names: set[str] | None = None,
) -> list[Path]:
    extensions = extensions or DEFAULT_SOURCE_EXTENSIONS
    excluded_dir_names = excluded_dir_names or DEFAULT_EXCLUDED_DIR_NAMES

    files: list[Path] = []

    for path in root.rglob("*"):
        if not path.is_file():
            continue

        try:
            relative = path.relative_to(root)
        except ValueError:
            relative = path

        if should_skip_dir(relative.parent, excluded_dir_names):
            continue

        if path.suffix not in extensions:
            continue

        files.append(path)

    files.sort(key=lambda item: item.as_posix().casefold())
    return files


# ---------------------------------------------------------------------------
# Name extraction from minimal runtime symbols
# ---------------------------------------------------------------------------

def _signature_tokens(signature: str):
    return tokenize_lines([signature])


def _extract_type_name_from_signature(signature: str, keyword: str) -> str | None:
    tokens = _signature_tokens(signature)
    values = token_values(tokens)

    if keyword not in values:
        return None

    index = values.index(keyword) + 1

    # enum class / enum struct
    if keyword == "enum" and index < len(tokens) and tokens[index].value in {"class", "struct"}:
        index += 1

    if index < len(tokens) and tokens[index].kind == "identifier":
        return tokens[index].value

    return None


def _extract_function_name_from_signature(signature: str) -> str | None:
    tokens = _signature_tokens(signature)
    candidates: list[int] = []

    for index, token in enumerate(tokens):
        if token.value != "(":
            continue

        if index == 0:
            continue

        close = find_matching_token(tokens, index, "(", ")")

        if close is None:
            continue

        previous = tokens[index - 1]

        if previous.value in {"decltype", "sizeof", "alignof", "noexcept", "requires"}:
            continue

        candidates.append(index)

    if not candidates:
        return None

    paren_index = candidates[-1]
    short_name, visible_name = extract_function_name(tokens, paren_index)

    return visible_name or short_name or None


def derive_short_name(symbol: dict[str, Any]) -> str | None:
    existing = symbol.get("shortName") or symbol.get("name")

    if isinstance(existing, str) and existing:
        return existing

    symbol_type = symbol.get("type", "")
    signature = str(symbol.get("signature", ""))

    if symbol_type in {"class", "class_declaration"}:
        return _extract_type_name_from_signature(signature, "class")

    if symbol_type in {"struct", "struct_declaration"}:
        return _extract_type_name_from_signature(signature, "struct")

    if symbol_type == "enum":
        return _extract_type_name_from_signature(signature, "enum")

    if symbol_type == "namespace":
        name = _extract_type_name_from_signature(signature, "namespace")
        return name or "<anonymous>"

    if symbol_type in {
        "function",
        "function_declaration",
        "method",
        "method_declaration",
        "constructor",
        "constructor_declaration",
        "destructor",
        "destructor_declaration",
        "operator",
        "operator_declaration",
    }:
        return _extract_function_name_from_signature(signature)

    return None


def qualify_name(container: str | None, short_name: str | None) -> str | None:
    if not short_name:
        return None

    if "::" in short_name:
        return short_name

    if container:
        return f"{container}::{short_name}"

    return short_name


def search_aliases_for_symbol(symbol: dict[str, Any]) -> list[str]:
    short_name = derive_short_name(symbol)
    container = symbol.get("container")
    qualified_name = qualify_name(container, short_name)

    aliases: list[str] = []

    if short_name:
        aliases.append(short_name)

    if qualified_name and qualified_name not in aliases:
        aliases.append(qualified_name)

    # Anonymous namespace convenience alias:
    #   UI::<anonymous@25>::Helper -> UI::Helper
    # This is for search only. The runtime symbol keeps the real container.
    if qualified_name and "::<anonymous" in qualified_name:
        simplified = qualified_name
        while "::<anonymous" in simplified:
            prefix, rest = simplified.split("::<anonymous", 1)
            after = rest.split(">", 1)
            if len(after) != 2:
                break
            simplified = prefix + after[1]

        if simplified and simplified not in aliases:
            aliases.append(simplified)

    return aliases


def data_ref_from_file_data(
    *,
    file_index: dict[str, Any],
    data_item: dict[str, Any],
) -> dict[str, Any]:
    return {
        "dataId": data_item["dataId"],
        "fileId": file_index["fileId"],
        "relativePath": file_index["relativePath"],
        "declarationKind": data_item["declarationKind"],
        "scopeKind": data_item["scopeKind"],
        "name": data_item["name"],
        "qualifiedName": data_item.get("qualifiedName"),
        "container": data_item.get("container"),
        "startLine": data_item["startLine"],
        "endLine": data_item["endLine"],
        "signature": data_item["signature"],
        "typeText": data_item.get("typeText", ""),
        "storage": data_item.get("storage", []),
        "initializerKind": data_item.get("initializerKind", "unknown"),
    }


def search_aliases_for_data(data_item: dict[str, Any]) -> list[str]:
    aliases: list[str] = []

    name = str(data_item.get("name") or "")
    qualified_name = str(data_item.get("qualifiedName") or "")

    if name:
        aliases.append(name)

    if qualified_name and qualified_name not in aliases:
        aliases.append(qualified_name)

    # Anonymous namespace convenience alias, same idea as symbol aliases.
    if qualified_name and "::<anonymous" in qualified_name:
        simplified = qualified_name
        while "::<anonymous" in simplified:
            prefix, rest = simplified.split("::<anonymous", 1)
            after = rest.split(">", 1)
            if len(after) != 2:
                break
            simplified = prefix + after[1]

        if simplified and simplified not in aliases:
            aliases.append(simplified)

    return aliases


def normalize_jobs(jobs: int | None) -> int:
    if jobs is None:
        return 1

    if jobs < 0:
        return 1

    if jobs == 0:
        # Auto mode. Keep this conservative because each worker has to import
        # the scanner modules and can create substantial temporary Python data.
        return min(8, max(1, (os.cpu_count() or 2) - 1))

    return max(1, jobs)


# ---------------------------------------------------------------------------
# Project index build
# ---------------------------------------------------------------------------

def build_file_indexes_for_project(
    *,
    source_files: list[Path],
    root: Path,
    output_root: Path,
    emit_debug_file_indexes: bool,
    case_insensitive_paths: bool,
    blank_comments: bool,
    jobs: int = 1,
    progress_callback: Callable[[int, int, Path], None] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    files_dir = output_root / "files"
    files_dir.mkdir(parents=True, exist_ok=True)

    jobs = normalize_jobs(jobs)
    total = len(source_files)
    completed = 0
    file_indexes: list[dict[str, Any]] = []
    diagnostics: list[dict[str, Any]] = []

    if total == 0:
        return file_indexes, diagnostics

    if jobs <= 1:
        for path in source_files:
            completed += 1

            if progress_callback is not None:
                progress_callback(completed, total, path)

            try:
                file_index = build_file_index(
                    path=path,
                    project_root=root,
                    case_insensitive_paths=case_insensitive_paths,
                    blank_comments=blank_comments,
                    emit_debug=emit_debug_file_indexes,
                )
            except Exception as exc:  # noqa: BLE001 - build must continue.
                diagnostics.append(
                    {
                        "severity": "error",
                        "code": "file_index_failed",
                        "message": str(exc),
                        "relativePath": path.relative_to(root).as_posix()
                        if path.is_relative_to(root)
                        else path.as_posix(),
                    }
                )
                continue

            save_json(file_index_output_path(files_dir, file_index["fileId"]), file_index)
            file_indexes.append(file_index)

        file_indexes.sort(key=lambda item: item["relativePath"].casefold())
        return file_indexes, diagnostics

    worker_args = [
        {
            "path": path.as_posix(),
            "root": root.as_posix(),
            "emit_debug_file_indexes": emit_debug_file_indexes,
            "case_insensitive_paths": case_insensitive_paths,
            "blank_comments": blank_comments,
        }
        for path in source_files
    ]

    with ProcessPoolExecutor(max_workers=jobs) as executor:
        future_to_path = {
            executor.submit(build_file_index_worker, payload): Path(payload["path"])
            for payload in worker_args
        }

        for future in as_completed(future_to_path):
            completed += 1
            path = future_to_path[future]

            if progress_callback is not None:
                progress_callback(completed, total, path)

            try:
                result = future.result()
            except Exception as exc:  # noqa: BLE001 - defensive, worker should return errors.
                diagnostics.append(
                    {
                        "severity": "error",
                        "code": "file_index_failed",
                        "message": str(exc),
                        "relativePath": path.relative_to(root).as_posix()
                        if path.is_relative_to(root)
                        else path.as_posix(),
                    }
                )
                continue

            if not result.get("ok"):
                failed_path = Path(str(result.get("path") or path.as_posix()))
                diagnostics.append(
                    {
                        "severity": "error",
                        "code": "file_index_failed",
                        "message": str(result.get("error") or "unknown error"),
                        "relativePath": failed_path.relative_to(root).as_posix()
                        if failed_path.is_relative_to(root)
                        else failed_path.as_posix(),
                    }
                )
                continue

            file_index = result["fileIndex"]
            save_json(file_index_output_path(files_dir, file_index["fileId"]), file_index)
            file_indexes.append(file_index)

    file_indexes.sort(key=lambda item: item["relativePath"].casefold())
    return file_indexes, diagnostics

def build_file_index_worker(payload: dict[str, Any]) -> dict[str, Any]:
    path = Path(payload["path"])
    root = Path(payload["root"])

    try:
        file_index = build_file_index(
            path=path,
            project_root=root,
            case_insensitive_paths=bool(payload["case_insensitive_paths"]),
            blank_comments=bool(payload["blank_comments"]),
            emit_debug=bool(payload["emit_debug_file_indexes"]),
        )

        return {
            "ok": True,
            "path": path.as_posix(),
            "fileIndex": file_index,
        }
    except Exception as exc:  # noqa: BLE001 - parent keeps indexing other files.
        return {
            "ok": False,
            "path": path.as_posix(),
            "error": str(exc),
        }

def file_index_output_path(files_dir: Path, file_id: str) -> Path:
    return files_dir / f"{file_id}.json"


def symbol_ref_from_file_symbol(
    *,
    file_index: dict[str, Any],
    symbol: dict[str, Any],
) -> dict[str, Any]:
    short_name = symbol.get("shortName") or derive_short_name(symbol)
    qualified_name = symbol.get("qualifiedName") or qualify_name(symbol.get("container"), short_name)

    return {
        "symbolId": symbol["symbolId"],
        "fileId": file_index["fileId"],
        "relativePath": file_index["relativePath"],
        "type": symbol["type"],
        "shortName": short_name,
        "qualifiedName": qualified_name,
        "container": symbol.get("container"),
        "startLine": symbol["startLine"],
        "endLine": symbol["endLine"],
        "signature": symbol["signature"],
    }


def build_project_index(
    *,
    root: Path,
    output_root: Path,
    extensions: set[str] | None = None,
    excluded_dir_names: set[str] | None = None,
    emit_debug_file_indexes: bool = False,
    case_insensitive_paths: bool = True,
    blank_comments: bool = True,
    progress_callback: Callable[[int, int, Path], None] | None = None,
    jobs: int = 1,
) -> ProjectIndexBuildResult:
    output_root.mkdir(parents=True, exist_ok=True)
    files_dir = output_root / "files"
    files_dir.mkdir(parents=True, exist_ok=True)

    source_files = discover_source_files(
        root,
        extensions=extensions,
        excluded_dir_names=excluded_dir_names,
    )

    file_indexes, file_index_failed_diagnostics = build_file_indexes_for_project(
        source_files=source_files,
        root=root,
        output_root=output_root,
        emit_debug_file_indexes=emit_debug_file_indexes,
        case_insensitive_paths=case_insensitive_paths,
        blank_comments=blank_comments,
        jobs=jobs,
        progress_callback=progress_callback,
    )

    manifest_files: list[dict[str, Any]] = []
    symbols: list[dict[str, Any]] = []
    names: dict[str, list[str]] = defaultdict(list)
    modules: dict[str, list[str]] = defaultdict(list)
    data_items: list[dict[str, Any]] = []
    data_names: dict[str, list[str]] = defaultdict(list)
    diagnostics: list[dict[str, Any]] = [*file_index_failed_diagnostics]

    for file_index in file_indexes:
        file_id = file_index["fileId"]
        save_json(file_index_output_path(files_dir, file_id), file_index)

        manifest_files.append(
            {
                "fileId": file_id,
                "relativePath": file_index["relativePath"],
                "contentHash": file_index["contentHash"],
                "lineCount": file_index["lineCount"],
                "unitKind": file_index["module"]["unitKind"],
                "fullModuleName": file_index["module"].get("fullModuleName"),
                "symbols": len(file_index.get("symbols", [])),
                "data": len(file_index.get("data", [])),
                "diagnostics": len(file_index.get("diagnostics", [])),
            }
        )

        full_module_name = file_index["module"].get("fullModuleName")

        if full_module_name:
            modules[full_module_name].append(file_id)

        for diagnostic in file_index.get("diagnostics", []):
            diagnostics.append(
                {
                    "fileId": file_id,
                    "relativePath": file_index["relativePath"],
                    **diagnostic,
                }
            )

        for symbol in file_index.get("symbols", []):
            ref = symbol_ref_from_file_symbol(
                file_index=file_index,
                symbol=symbol,
            )
            symbols.append(ref)

            for alias in search_aliases_for_symbol(symbol):
                if ref["symbolId"] not in names[alias]:
                    names[alias].append(ref["symbolId"])

        for data_item in file_index.get("data", []):
            ref = data_ref_from_file_data(
                file_index=file_index,
                data_item=data_item,
            )
            data_items.append(ref)

            for alias in search_aliases_for_data(data_item):
                if ref["dataId"] not in data_names[alias]:
                    data_names[alias].append(ref["dataId"])

    symbols.sort(
        key=lambda item: (
            item["qualifiedName"] or item["shortName"] or "",
            item["relativePath"],
            item["startLine"],
            item["endLine"],
        )
    )
    manifest_files.sort(key=lambda item: item["relativePath"].casefold())

    data_items.sort(
        key=lambda item: (
            item["qualifiedName"] or item["name"] or "",
            item["relativePath"],
            item["startLine"],
            item["endLine"],
        )
    )

    manifest = {
        "schema": PROJECT_INDEX_SCHEMA,
        "root": root.resolve().as_posix(),
        "filesDir": "files",
        "files": manifest_files,
        "counts": {
            "files": len(manifest_files),
            "symbols": len(symbols),
            "names": len(names),
            "data": len(data_items),
            "dataNames": len(data_names),
            "modules": len(modules),
            "diagnostics": len(diagnostics),
        },
    }

    save_json(output_root / "manifest.json", manifest)
    save_json(output_root / "names.json", dict(sorted(names.items(), key=lambda item: item[0].casefold())))
    save_json(output_root / "data_names.json", dict(sorted(data_names.items(), key=lambda item: item[0].casefold())))
    save_json(output_root / "modules.json", dict(sorted(modules.items(), key=lambda item: item[0].casefold())))
    save_json(output_root / "diagnostics.json", diagnostics)

    with (output_root / "symbols.jsonl").open("w", encoding="utf-8") as handle:
        for symbol in symbols:
            handle.write(json.dumps(symbol, ensure_ascii=False) + "\n")

    with (output_root / "data.jsonl").open("w", encoding="utf-8") as handle:
        for data_item in data_items:
            handle.write(json.dumps(data_item, ensure_ascii=False) + "\n")

    return ProjectIndexBuildResult(
        root=root,
        output_root=output_root,
        files_count=len(manifest_files),
        symbols_count=len(symbols),
        names_count=len(names),
        data_count=len(data_items),
        data_names_count=len(data_names),
        modules_count=len(modules),
        diagnostics_count=len(diagnostics),
    )


def normalize_glob_pattern(pattern: str) -> str:
    pattern = pattern.strip()

    if not pattern:
        return "*"

    return pattern

    
# ---------------------------------------------------------------------------
# Runtime loader/query helpers used by the server
# ---------------------------------------------------------------------------

class LoadedProjectIndex:
    def __init__(self, index_root: Path) -> None:
        self.index_root = index_root
        self.files_dir = index_root / "files"
        self.manifest = json.loads((index_root / "manifest.json").read_text(encoding="utf-8"))
        self.names: dict[str, list[str]] = json.loads((index_root / "names.json").read_text(encoding="utf-8"))
        self.modules: dict[str, list[str]] = json.loads((index_root / "modules.json").read_text(encoding="utf-8"))
        self.symbols = self._load_symbols(index_root / "symbols.jsonl")
        self.symbol_by_id = {symbol["symbolId"]: symbol for symbol in self.symbols}
        self.file_by_id = {item["fileId"]: item for item in self.manifest["files"]}
        self.file_id_by_relative_path = {item["relativePath"]: item["fileId"] for item in self.manifest["files"]}
        self.data = self._load_jsonl_if_exists(index_root / "data.jsonl")
        self.data_names: dict[str, list[str]] = self._load_json_if_exists(index_root / "data_names.json", {})
        self.data_by_id = {item["dataId"]: item for item in self.data}

    @staticmethod
    def _load_symbols(path: Path) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []

        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()

                if not line:
                    continue

                result.append(json.loads(line))

        return result

    @staticmethod
    def _load_jsonl_if_exists(path: Path) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []

        if not path.exists():
            return result

        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()

                if not line:
                    continue

                result.append(json.loads(line))

        return result


    @staticmethod
    def _load_json_if_exists(path: Path, default: Any) -> Any:
        if not path.exists():
            return default

        return json.loads(path.read_text(encoding="utf-8"))

    def load_file_index(self, file_id: str) -> dict[str, Any]:
        path = self.files_dir / f"{file_id}.json"
        return json.loads(path.read_text(encoding="utf-8"))

    def find_symbol(self, query: str, *, limit: int = 20) -> list[dict[str, Any]]:
        direct_ids = self.names.get(query, [])
        results: list[dict[str, Any]] = []
        seen: set[str] = set()

        for symbol_id in direct_ids:
            symbol = self.symbol_by_id.get(symbol_id)

            if symbol is None or symbol_id in seen:
                continue

            seen.add(symbol_id)
            results.append(symbol)

            if len(results) >= limit:
                return results

        query_folded = query.casefold()

        for symbol in self.symbols:
            symbol_id = symbol["symbolId"]

            if symbol_id in seen:
                continue

            haystacks = [
                str(symbol.get("shortName") or ""),
                str(symbol.get("qualifiedName") or ""),
                str(symbol.get("signature") or ""),
            ]

            if any(query_folded in value.casefold() for value in haystacks):
                seen.add(symbol_id)
                results.append(symbol)

                if len(results) >= limit:
                    break

        return results

    def list_file_symbols(self, file: str) -> list[dict[str, Any]]:
        file_id = file

        if file_id not in self.file_by_id:
            file_id = self.file_id_by_relative_path.get(file, "")

        if not file_id:
            return []

        return [
            symbol
            for symbol in self.symbols
            if symbol["fileId"] == file_id
        ]

    def find_module(self, module_name: str) -> list[dict[str, Any]]:
        file_ids = self.modules.get(module_name, [])

        return [
            self.file_by_id[file_id]
            for file_id in file_ids
            if file_id in self.file_by_id
        ]

    def read_range(self, *, project_root: Path, file: str, start_line: int, end_line: int) -> str:
        file_id = file

        if file_id in self.file_by_id:
            relative_path = self.file_by_id[file_id]["relativePath"]
        else:
            relative_path = file

        path = project_root / relative_path
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        start_line = max(1, start_line)
        end_line = min(len(lines), end_line)

        if start_line > end_line:
            return ""

        return "\n".join(
            f"{line_no:04d}: {lines[line_no - 1]}"
            for line_no in range(start_line, end_line + 1)
        )

    def read_symbol(self, *, project_root: Path, symbol_id: str) -> str:
        symbol = self.symbol_by_id.get(symbol_id)

        if symbol is None:
            return ""

        return self.read_range(
            project_root=project_root,
            file=symbol["fileId"],
            start_line=symbol["startLine"],
            end_line=symbol["endLine"],
        )

    def find_files(self, pattern: str, *, limit: int = 100) -> list[dict[str, Any]]:
        pattern = normalize_glob_pattern(pattern).casefold()
        results: list[dict[str, Any]] = []

        for file_item in self.manifest["files"]:
            relative_path = str(file_item.get("relativePath") or "")
            haystack = relative_path.casefold()

            if fnmatchcase(haystack, pattern):
                results.append(file_item)

                if len(results) >= limit:
                    break

        return results

    def find_symbols_glob(
        self,
        pattern: str,
        *,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        pattern = normalize_glob_pattern(pattern).casefold()
        results: list[dict[str, Any]] = []

        for symbol in self.symbols:
            haystacks = [
                str(symbol.get("shortName") or ""),
                str(symbol.get("qualifiedName") or ""),
                str(symbol.get("container") or ""),
                str(symbol.get("signature") or ""),
                str(symbol.get("relativePath") or ""),
            ]

            if any(fnmatchcase(value.casefold(), pattern) for value in haystacks):
                results.append(symbol)

                if len(results) >= limit:
                    break

        return results

    def search_modules(self, pattern: str, *, limit: int = 100) -> list[dict[str, Any]]:
        pattern = normalize_glob_pattern(pattern).casefold()
        results: list[dict[str, Any]] = []
        seen_file_ids: set[str] = set()

        for module_name, file_ids in self.modules.items():
            if not fnmatchcase(module_name.casefold(), pattern):
                continue

            for file_id in file_ids:
                if file_id in seen_file_ids:
                    continue

                file_item = self.file_by_id.get(file_id)

                if file_item is None:
                    continue

                seen_file_ids.add(file_id)

                results.append(
                    {
                        "moduleName": module_name,
                        **file_item,
                    }
                )

                if len(results) >= limit:
                    return results

        return results

    def find_data(
        self,
        query: str,
        *,
        container: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        direct_ids = self.data_names.get(query, [])
        results: list[dict[str, Any]] = []
        seen: set[str] = set()

        def matches_container(item: dict[str, Any]) -> bool:
            if not container:
                return True

            item_container = str(item.get("container") or "")
            item_qualified_name = str(item.get("qualifiedName") or "")
            container_folded = container.casefold()

            return (
                item_container.casefold() == container_folded
                or item_container.casefold().endswith("::" + container_folded)
                or item_qualified_name.casefold().startswith(container_folded + "::")
            )

        for data_id in direct_ids:
            item = self.data_by_id.get(data_id)

            if item is None or data_id in seen:
                continue

            if not matches_container(item):
                continue

            seen.add(data_id)
            results.append(item)

            if len(results) >= limit:
                return results

        query_folded = query.casefold()

        for item in self.data:
            data_id = item["dataId"]

            if data_id in seen:
                continue

            if not matches_container(item):
                continue

            haystacks = [
                str(item.get("name") or ""),
                str(item.get("qualifiedName") or ""),
                str(item.get("container") or ""),
                str(item.get("signature") or ""),
                str(item.get("typeText") or ""),
            ]

            if any(query_folded in value.casefold() for value in haystacks):
                seen.add(data_id)
                results.append(item)

                if len(results) >= limit:
                    break

        return results


    def list_type_members(self, container: str, *, limit: int = 500) -> list[dict[str, Any]]:
        container_folded = container.casefold()
        results: list[dict[str, Any]] = []

        for item in self.data:
            item_container = str(item.get("container") or "")

            if (
                item_container.casefold() == container_folded
                or item_container.casefold().endswith("::" + container_folded)
            ):
                results.append(item)

                if len(results) >= limit:
                    break

        results.sort(
            key=lambda item: (
                int(item.get("startLine") or 0),
                str(item.get("name") or ""),
            )
        )
        return results


    def read_data(self, *, project_root: Path, data_id: str) -> str:
        item = self.data_by_id.get(data_id)

        if item is None:
            return ""

        return self.read_range(
            project_root=project_root,
            file=item["fileId"],
            start_line=int(item["startLine"]),
            end_line=int(item["endLine"]),
        )