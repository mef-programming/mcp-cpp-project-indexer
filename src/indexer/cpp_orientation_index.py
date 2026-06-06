from __future__ import annotations

import hashlib
import re

from pathlib import Path
from typing import Any


ORIENTATION_SCHEMA = "cpp.project_orientation.v1"
DEFAULT_ORIENTATION_FILES = (
    "README.md",
    "readme.md",
    "AGENTS.md",
    "TOPOLOGY.md",
    "topology.md",
    "SYSTEM_TOPOLOGY.md",
    "system-topology.md",
    "system_topology.md",
)
DEFAULT_ORIENTATION_EXCLUDED_DIRS = {
    ".git",
    ".mcp-cpp-project-indexer",
    ".mcp-ts-project-indexer",
    ".mcp-python-project-indexer",
    ".vs",
    ".vscode",
    "node_modules",
    "__pycache__",
    "dist",
    "build",
    "out",
}


def stable_orientation_id(relative_path: str) -> str:
    digest = hashlib.sha1(relative_path.encode("utf-8")).hexdigest()[:24]
    return f"doc_{digest}"


def normalize_doc_path(path: Path) -> str:
    return path.as_posix().replace("\\", "/")


def read_markdown(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def split_markdown_sections(text: str) -> tuple[str | None, dict[str, str]]:
    title: str | None = None
    sections: dict[str, list[str]] = {}
    current_heading = "__intro__"
    in_fence = False
    sections[current_heading] = []

    for line in text.splitlines():
        if re.match(r"^\s*```", line):
            in_fence = not in_fence
            sections.setdefault(current_heading, []).append(line.rstrip())
            continue

        match = None if in_fence else re.match(r"^(#{1,6})\s+(.+?)\s*$", line)

        if match:
            heading = match.group(2).strip()

            if title is None and len(match.group(1)) == 1:
                title = heading

            current_heading = heading
            sections.setdefault(current_heading, [])
            continue

        label = None if in_fence else label_section(line)

        if label is not None:
            current_heading, inline_body = label
            sections.setdefault(current_heading, [])

            if inline_body:
                sections[current_heading].append(inline_body)

            continue

        sections.setdefault(current_heading, []).append(line.rstrip())

    return title, {
        heading: "\n".join(lines).strip()
        for heading, lines in sections.items()
        if "\n".join(lines).strip()
    }


def section_by_names(sections: dict[str, str], names: tuple[str, ...]) -> str:
    wanted = set(names)

    for heading, body in sections.items():
        if heading in wanted:
            return body

    return ""


LABEL_SECTION_NAMES = (
    "Purpose",
    "Use this folder when the question is about",
    "Do not use this folder first when the question is about",
)


def label_section(line: str) -> tuple[str, str] | None:
    stripped = line.strip()
    match = re.match(r"^([A-Za-z0-9][A-Za-z0-9 /_-]{2,80}):\s*(.*)$", stripped)

    if not match:
        return None

    heading = match.group(1).strip()
    if heading not in LABEL_SECTION_NAMES:
        return None

    return heading, match.group(2).strip()


def markdown_bullets(text: str, *, limit: int = 30) -> list[str]:
    result: list[str] = []

    for line in text.splitlines():
        stripped = line.strip()
        match = re.match(r"^[-*]\s+(.+)$", stripped)

        if match:
            result.append(match.group(1).strip())

        if len(result) >= limit:
            break

    return result


def markdown_map_entries(text: str, *, limit: int = 80) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []

    for line in text.splitlines():
        stripped = line.strip()

        if not stripped or stripped.startswith("#"):
            continue

        match = re.match(r"^([A-Za-z0-9_./\\:-]+)\s{2,}(.+)$", stripped)

        if match:
            result.append(
                {
                    "path": match.group(1).strip(),
                    "description": match.group(2).strip(),
                }
            )

        if len(result) >= limit:
            break

    return result


def compact_text(text: str, *, max_chars: int) -> str:
    collapsed = re.sub(r"\s+", " ", text).strip()

    if len(collapsed) <= max_chars:
        return collapsed

    return collapsed[: max_chars - 1].rstrip() + "…"


def build_orientation_node(root: Path, doc_path: Path) -> dict[str, Any]:
    relative_path = normalize_doc_path(doc_path.relative_to(root))
    folder = normalize_doc_path(doc_path.parent.relative_to(root)) if doc_path.parent != root else "."
    text = read_markdown(doc_path)
    title, sections = split_markdown_sections(text)
    document_kind = "topology" if "topology" in doc_path.stem.casefold() or "topology" in (title or "").casefold() else "folder_orientation"
    purpose = section_by_names(sections, ("Purpose",))
    use_when = section_by_names(sections, ("Use this folder when the question is about",))
    do_not_use = section_by_names(sections, ("Do not use this folder first when the question is about",))
    map_section = section_by_names(sections, ("Map",))
    start_here = section_by_names(sections, ("Start Here",))
    boundaries = section_by_names(sections, ("Boundaries",))

    return {
        "orientationId": stable_orientation_id(relative_path),
        "kind": document_kind,
        "file": relative_path,
        "folder": folder,
        "title": title or doc_path.name,
        "purpose": compact_text(purpose, max_chars=1200),
        "useWhen": markdown_bullets(use_when),
        "doNotUseFirstWhen": markdown_bullets(do_not_use),
        "map": markdown_map_entries(map_section),
        "startHere": markdown_bullets(start_here),
        "boundaries": compact_text(boundaries, max_chars=1200),
        "headings": [heading for heading in sections.keys() if heading != "__intro__"],
        "lineCount": len(text.splitlines()),
        "contentHash": hashlib.sha1(text.encode("utf-8", errors="replace")).hexdigest(),
    }


def has_structured_orientation(node: dict[str, Any]) -> bool:
    if node.get("kind") == "topology":
        return True

    return bool(
        node.get("purpose")
        or node.get("useWhen")
        or node.get("doNotUseFirstWhen")
        or node.get("map")
        or node.get("startHere")
        or node.get("boundaries")
    )


def discover_orientation_documents(root: Path, *, doc_files: tuple[str, ...] = DEFAULT_ORIENTATION_FILES) -> list[Path]:
    doc_names = {name.casefold() for name in doc_files}
    result: list[Path] = []

    for path in root.rglob("*"):
        if not path.is_file():
            continue

        relative_parts = path.relative_to(root).parts

        if any(part in DEFAULT_ORIENTATION_EXCLUDED_DIRS for part in relative_parts[:-1]):
            continue

        if path.name.casefold() in doc_names or "topology" in path.stem.casefold():
            result.append(path)

    result.sort(key=lambda item: normalize_doc_path(item.relative_to(root)).casefold())
    return result


def build_orientation_index(root: Path) -> dict[str, Any]:
    nodes = [
        node
        for node in (build_orientation_node(root, path) for path in discover_orientation_documents(root))
        if has_structured_orientation(node)
    ]
    by_folder = {node["folder"]: node["orientationId"] for node in nodes}

    for node in nodes:
        folder = node["folder"]
        parent = normalize_doc_path(Path(folder).parent) if folder not in {".", ""} else None

        if parent == ".":
            parent = "."

        node["parentFolder"] = parent
        node["parentOrientationId"] = by_folder.get(parent or "")
        node["childFolders"] = sorted(
            child_folder
            for child_folder in by_folder
            if child_folder != folder and Path(child_folder).parent.as_posix() == folder
        )

    return {
        "schema": ORIENTATION_SCHEMA,
        "root": root.resolve().as_posix(),
        "counts": {"nodes": len(nodes)},
        "nodes": nodes,
    }
