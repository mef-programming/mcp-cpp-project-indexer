from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class TreeNode:
    name: str
    full_name: str = ""
    file_ids: list[str] = field(default_factory=list)
    children: dict[str, "TreeNode"] = field(default_factory=dict)


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def normalize_module_name(name: str | None) -> str:
    if not name:
        return ""

    result = name.strip()
    result = result.replace(" :", ":")
    result = result.replace(": ", ":")
    result = result.replace(" . ", ".")
    return result


def load_manifest(index_root: Path) -> dict[str, Any]:
    return load_json(index_root / "manifest.json")


def load_modules(index_root: Path) -> dict[str, list[str]]:
    raw = load_json(index_root / "modules.json")
    return {
        normalize_module_name(name): file_ids
        for name, file_ids in raw.items()
    }


def load_file_index(index_root: Path, file_id: str) -> dict[str, Any]:
    return load_json(index_root / "files" / f"{file_id}.json")


def module_parts(module_name: str) -> list[str]:
    module_name = normalize_module_name(module_name)

    if not module_name:
        return []

    if ":" in module_name:
        primary, partition = module_name.split(":", 1)
    else:
        primary, partition = module_name, ""

    parts = [part for part in primary.split(".") if part]

    if partition:
        parts.append(f":{partition}")

    return parts


def build_module_tree(modules: dict[str, list[str]]) -> TreeNode:
    root = TreeNode(name="<modules>", full_name="")

    for module_name, file_ids in sorted(modules.items(), key=lambda item: item[0].casefold()):
        parts = module_parts(module_name)

        if not parts:
            continue

        node = root
        full_parts: list[str] = []

        for part in parts:
            if part.startswith(":"):
                full_name = ".".join(full_parts) + part
            else:
                full_parts.append(part)
                full_name = ".".join(full_parts)

            child = node.children.get(part)

            if child is None:
                child = TreeNode(name=part, full_name=full_name)
                node.children[part] = child

            node = child

        node.file_ids.extend(file_ids)
        node.full_name = module_name

    return root


def format_file_list(
    *,
    file_ids: list[str],
    file_by_id: dict[str, dict[str, Any]],
    max_files: int,
) -> str:
    if not file_ids:
        return ""

    files = [
        file_by_id[file_id]["relativePath"]
        for file_id in file_ids
        if file_id in file_by_id
    ]

    if not files:
        return ""

    shown = files[:max_files]
    suffix = ""

    if len(files) > max_files:
        suffix = f", +{len(files) - max_files} more"

    return "  [" + ", ".join(shown) + suffix + "]"


def dump_tree(
    *,
    node: TreeNode,
    file_by_id: dict[str, dict[str, Any]],
    indent: str = "",
    max_depth: int | None = None,
    current_depth: int = 0,
    max_files: int = 1,
) -> list[str]:
    lines: list[str] = []

    if max_depth is not None and current_depth > max_depth:
        return lines

    children = sorted(node.children.values(), key=lambda item: item.name.casefold())

    for child in children:
        marker = "*" if child.file_ids else "+"
        file_text = format_file_list(
            file_ids=child.file_ids,
            file_by_id=file_by_id,
            max_files=max_files,
        )
        count_text = f" ({len(child.file_ids)} file)" if child.file_ids else ""
        lines.append(f"{indent}{marker} {child.name}{count_text}{file_text}")
        lines.extend(
            dump_tree(
                node=child,
                file_by_id=file_by_id,
                indent=indent + "  ",
                max_depth=max_depth,
                current_depth=current_depth + 1,
                max_files=max_files,
            )
        )

    return lines


def collect_module_imports(index_root: Path, file_ids: list[str]) -> list[str]:
    imports: list[str] = []
    seen: set[str] = set()

    for file_id in file_ids:
        file_index = load_file_index(index_root, file_id)

        for entry in file_index.get("imports", []):
            target = normalize_module_name(
                entry.get("resolvedModule") or entry.get("module")
            )

            if not target:
                continue

            if target not in seen:
                seen.add(target)
                imports.append(target)

    imports.sort(key=str.casefold)
    return imports


def dump_import_tree(
    *,
    index_root: Path,
    modules: dict[str, list[str]],
    module_name: str,
    max_depth: int,
    indent: str = "",
    depth: int = 0,
    seen: set[str] | None = None,
) -> list[str]:
    module_name = normalize_module_name(module_name)
    seen = seen or set()
    lines: list[str] = []

    if module_name in seen:
        lines.append(f"{indent}- {module_name}  [cycle]")
        return lines

    seen.add(module_name)

    file_ids = modules.get(module_name, [])
    status = "" if file_ids else "  [not indexed]"
    lines.append(f"{indent}- {module_name}{status}")

    if depth >= max_depth or not file_ids:
        return lines

    for imported in collect_module_imports(index_root, file_ids):
        lines.extend(
            dump_import_tree(
                index_root=index_root,
                modules=modules,
                module_name=imported,
                max_depth=max_depth,
                indent=indent + "  ",
                depth=depth + 1,
                seen=set(seen),
            )
        )

    return lines


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Dump module-oriented views from a cpp.project_index.v1 output. "
            "This is a diagnostic/readability tool; it does not analyze code."
        )
    )
    parser.add_argument(
        "--index-root",
        type=Path,
        default=Path.cwd() / ".mcp-cpp-project-indexer",
        help="Project index root containing manifest.json, modules.json and files/.",
    )
    parser.add_argument(
        "--imports",
        type=str,
        default=None,
        help="Dump import tree for the given full C++20 module name.",
    )
    parser.add_argument(
        "--max-depth",
        type=int,
        default=None,
        help="Maximum depth for the module name tree. For --imports, default is 4.",
    )
    parser.add_argument(
        "--max-files",
        type=int,
        default=1,
        help="Maximum file paths to show per module leaf in the name tree.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Optional output text file. Defaults to stdout.",
    )

    args = parser.parse_args()

    manifest = load_manifest(args.index_root)
    modules = load_modules(args.index_root)
    file_by_id = {
        item["fileId"]: item
        for item in manifest.get("files", [])
    }

    if args.imports:
        max_depth = args.max_depth if args.max_depth is not None else 4
        lines = dump_import_tree(
            index_root=args.index_root,
            modules=modules,
            module_name=args.imports,
            max_depth=max_depth,
        )
    else:
        tree = build_module_tree(modules)
        lines = [
            f"Module tree: {args.index_root}",
            f"Modules: {len(modules)}",
            "Legend: + namespace/group, * module leaf",
            "",
        ]
        lines.extend(
            dump_tree(
                node=tree,
                file_by_id=file_by_id,
                max_depth=args.max_depth,
                max_files=args.max_files,
            )
        )

    text = "\n".join(lines) + "\n"

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    else:
        print(text, end="")


if __name__ == "__main__":
    main()
