from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from cpp_index_lock import IndexLockError, index_update_lock


MODULE_MAP_SCHEMA = "cpp.module_map.v1"


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def normalize_module_name(name: str | None) -> str:
    if not name:
        return ""

    result = name.strip()
    result = result.replace(" :", ":")
    result = result.replace(": ", ":")
    result = result.replace(" . ", ".")
    return result


def split_module_name(full_module_name: str) -> dict[str, Any]:
    full_module_name = normalize_module_name(full_module_name)

    if ":" in full_module_name:
        primary_name, partition_name = full_module_name.split(":", 1)
    else:
        primary_name = full_module_name
        partition_name = None

    return {
        "fullModuleName": full_module_name,
        "primaryModuleName": primary_name,
        "partitionName": partition_name,
        "primaryParts": [part for part in primary_name.split(".") if part],
        "partitionParts": [part for part in partition_name.split(".") if part] if partition_name else [],
    }


def load_file_index(index_root: Path, file_id: str) -> dict[str, Any]:
    return load_json(index_root / "files" / f"{file_id}.json")


def module_entry_from_file_index(
    *,
    index_root: Path,
    file_item: dict[str, Any],
) -> tuple[str | None, dict[str, Any] | None]:
    file_id = file_item["fileId"]
    file_index = load_file_index(index_root, file_id)
    module = file_index.get("module", {})
    full_module_name = normalize_module_name(module.get("fullModuleName"))

    if not full_module_name:
        return None, None

    imports: list[dict[str, Any]] = []
    seen_imports: set[tuple[str, str, bool]] = set()

    for import_entry in file_index.get("imports", []):
        raw_module = normalize_module_name(import_entry.get("module"))
        resolved_module = normalize_module_name(
            import_entry.get("resolvedModule") or raw_module
        )

        if not resolved_module:
            continue

        key = (
            str(import_entry.get("kind") or ""),
            resolved_module,
            bool(import_entry.get("isExported")),
        )

        if key in seen_imports:
            continue

        seen_imports.add(key)
        imports.append(
            {
                "kind": import_entry.get("kind"),
                "module": raw_module,
                "resolvedModule": resolved_module,
                "isExported": bool(import_entry.get("isExported")),
                "fileId": file_id,
                "relativePath": file_item["relativePath"],
                "startLine": import_entry.get("range", {}).get("startLine"),
                "endLine": import_entry.get("range", {}).get("endLine"),
            }
        )

    imports.sort(
        key=lambda item: (
            str(item.get("resolvedModule") or "").casefold(),
            str(item.get("kind") or ""),
        )
    )

    split = split_module_name(full_module_name)

    entry = {
        "fullModuleName": full_module_name,
        "primaryModuleName": split["primaryModuleName"],
        "partitionName": split["partitionName"],
        "primaryParts": split["primaryParts"],
        "partitionParts": split["partitionParts"],
        "files": [
            {
                "fileId": file_id,
                "relativePath": file_item["relativePath"],
                "unitKind": module.get("unitKind"),
                "lineCount": file_item.get("lineCount"),
                "symbols": file_item.get("symbols"),
                "diagnostics": file_item.get("diagnostics"),
            }
        ],
        "imports": imports,
        "importedBy": [],
    }

    return full_module_name, entry


def merge_module_entry(target: dict[str, Any], source: dict[str, Any]) -> None:
    existing_file_ids = {item["fileId"] for item in target["files"]}

    for file_item in source["files"]:
        if file_item["fileId"] not in existing_file_ids:
            target["files"].append(file_item)
            existing_file_ids.add(file_item["fileId"])

    existing_import_keys = {
        (
            item.get("kind"),
            item.get("resolvedModule"),
            item.get("isExported"),
            item.get("startLine"),
        )
        for item in target["imports"]
    }

    for import_item in source["imports"]:
        key = (
            import_item.get("kind"),
            import_item.get("resolvedModule"),
            import_item.get("isExported"),
            import_item.get("startLine"),
        )

        if key not in existing_import_keys:
            target["imports"].append(import_item)
            existing_import_keys.add(key)

    target["files"].sort(key=lambda item: item["relativePath"].casefold())
    target["imports"].sort(
        key=lambda item: (
            str(item.get("resolvedModule") or "").casefold(),
            str(item.get("kind") or ""),
            int(item.get("startLine") or 0),
        )
    )


def build_tree_nodes(modules: dict[str, dict[str, Any]]) -> dict[str, Any]:
    root: dict[str, Any] = {
        "name": "",
        "fullName": "",
        "children": {},
        "modules": [],
    }

    for full_module_name in sorted(modules, key=str.casefold):
        split = split_module_name(full_module_name)
        parts = list(split["primaryParts"])

        if split["partitionName"]:
            parts.append(":" + split["partitionName"])

        node = root
        full_parts: list[str] = []

        for part in parts:
            if part.startswith(":"):
                node_full_name = ".".join(full_parts) + part
            else:
                full_parts.append(part)
                node_full_name = ".".join(full_parts)

            children = node["children"]

            if part not in children:
                children[part] = {
                    "name": part,
                    "fullName": node_full_name,
                    "children": {},
                    "modules": [],
                }

            node = children[part]

        node["modules"].append(full_module_name)

    return root


def compress_tree(node: dict[str, Any]) -> dict[str, Any]:
    children = node.get("children", {})

    return {
        "name": node["name"],
        "fullName": node["fullName"],
        "modules": node.get("modules", []),
        "children": [
            compress_tree(child)
            for _, child in sorted(children.items(), key=lambda item: item[0].casefold())
        ],
    }


def add_reverse_imports(modules: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    unresolved: list[dict[str, Any]] = []

    for source_module_name, source_entry in modules.items():
        for import_entry in source_entry.get("imports", []):
            target_module_name = normalize_module_name(import_entry.get("resolvedModule"))

            if not target_module_name:
                continue

            target_entry = modules.get(target_module_name)

            if target_entry is None:
                unresolved.append(
                    {
                        "sourceModule": source_module_name,
                        "targetModule": target_module_name,
                        "kind": import_entry.get("kind"),
                        "sourceLine": import_entry.get("startLine"),
                    }
                )
                continue

            target_entry["importedBy"].append(
                {
                    "module": source_module_name,
                    "fileId": import_entry.get("fileId"),
                    "relativePath": import_entry.get("relativePath"),
                    "kind": import_entry.get("kind"),
                    "isExported": import_entry.get("isExported", False),
                    "sourceLine": import_entry.get("startLine"),
                }
            )

    for entry in modules.values():
        entry["importedBy"].sort(
            key=lambda item: (
                str(item.get("module") or "").casefold(),
                int(item.get("sourceLine") or 0),
            )
        )

    unresolved.sort(
        key=lambda item: (
            str(item.get("sourceModule") or "").casefold(),
            str(item.get("targetModule") or "").casefold(),
        )
    )
    return unresolved


def build_module_map(index_root: Path) -> dict[str, Any]:
    manifest = load_json(index_root / "manifest.json")
    modules: dict[str, dict[str, Any]] = {}

    for file_item in manifest.get("files", []):
        module_name, entry = module_entry_from_file_index(
            index_root=index_root,
            file_item=file_item,
        )

        if not module_name or entry is None:
            continue

        existing = modules.get(module_name)

        if existing is None:
            modules[module_name] = entry
        else:
            merge_module_entry(existing, entry)

    unresolved_imports = add_reverse_imports(modules)
    tree = compress_tree(build_tree_nodes(modules))

    module_map = {
        "schema": MODULE_MAP_SCHEMA,
        "projectRoot": manifest.get("root"),
        "counts": {
            "modules": len(modules),
            "unresolvedImports": len(unresolved_imports),
        },
        "modules": dict(sorted(modules.items(), key=lambda item: item[0].casefold())),
        "tree": tree,
        "unresolvedImports": unresolved_imports,
    }

    return module_map


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Build a JSON module map from a cpp.project_index.v1 output. "
            "The map is metadata only: modules, files, imports, importedBy and tree."
        )
    )
    parser.add_argument(
        "--index-root",
        type=Path,
        default=Path.cwd() / ".mcp-cpp-project-indexer",
        help="Project index root containing manifest.json and files/. Defaults to ./.mcp-cpp-project-indexer.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output JSON path. Defaults to <index-root>/module_map.json.",
    )
    parser.add_argument(
        "--print-summary-json",
        action="store_true",
        help="Print only a compact summary JSON to stdout.",
    )

    args = parser.parse_args()
    output = args.output or (args.index_root / "module_map.json")

    try:
        with index_update_lock(args.index_root):
            module_map = build_module_map(args.index_root)
            save_json(output, module_map)
    except IndexLockError as exc:
        raise SystemExit(str(exc)) from exc

    summary = {
        "schema": module_map["schema"],
        "projectRoot": module_map["projectRoot"],
        "output": output.as_posix(),
        "modules": module_map["counts"]["modules"],
        "unresolvedImports": module_map["counts"]["unresolvedImports"],
    }

    if args.print_summary_json:
        print(json.dumps(summary, indent=2, ensure_ascii=False))
        return

    print("Built cpp.module_map.v1")
    print("Output:", summary["output"])
    print("Modules:", summary["modules"])
    print("Unresolved imports:", summary["unresolvedImports"])


if __name__ == "__main__":
    main()
