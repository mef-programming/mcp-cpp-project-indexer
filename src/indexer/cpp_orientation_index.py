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

LABEL_SECTION_NAMES = (
    "Purpose",
    "Use this folder when the question is about",
    "Do not use this folder first when the question is about",
)
STRUCTURED_SECTION_NAMES = (
    "Purpose",
    "Use this folder when the question is about",
    "Do not use this folder first when the question is about",
    "Map",
    "Start Here",
    "Boundaries",
)


def stable_orientation_id(relative_path: str) -> str:
    digest = hashlib.sha1(relative_path.encode("utf-8")).hexdigest()[:24]
    return f"doc_{digest}"


def normalize_doc_path(path: Path) -> str:
    return path.as_posix().replace("\\", "/")


def normalize_root_relative(value: str) -> str:
    normalized = value.replace("\\", "/").lstrip("/")
    if normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized or "."


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


def section_by_name(sections: dict[str, str], name: str) -> str:
    return sections.get(name, "")


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


def fenced_text_blocks(text: str) -> list[str]:
    blocks: list[str] = []
    current: list[str] = []
    in_fence = False
    accepts_fence = False

    for line in text.splitlines():
        fence = re.match(r"^\s*```\s*([^`]*)\s*$", line)
        if fence:
            if not in_fence:
                language = fence.group(1).strip().casefold()
                accepts_fence = language in {"", "text", "txt"}
                current = []
                in_fence = True
            else:
                if accepts_fence:
                    blocks.append("\n".join(current))
                current = []
                accepts_fence = False
                in_fence = False
            continue

        if in_fence and accepts_fence:
            current.append(line)

    return blocks


def normalize_map_name(name: str) -> str:
    return name.strip().strip("`").replace("\\", "/")


def resolve_map_target(root: Path, folder: str, name: str) -> tuple[str, str | None]:
    normalized_name = normalize_map_name(name)
    if not normalized_name or re.match(r"^https?://", normalized_name, re.IGNORECASE):
        return "unresolved", None

    folder_prefix = "" if folder == "." else f"{folder}/"
    candidates = [
        normalize_root_relative(f"{folder_prefix}{normalized_name}"),
        normalize_root_relative(normalized_name),
    ]
    root_resolved = root.resolve()

    for candidate in dict.fromkeys(candidates):
        absolute = (root / candidate).resolve()
        try:
            absolute.relative_to(root_resolved)
        except ValueError:
            continue
        if absolute.exists():
            return "resolved", normalize_root_relative(candidate)

    return "unresolved", None


def markdown_map_entries(root: Path, folder: str, text: str, *, limit: int = 80) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    map_text = "\n".join(fenced_text_blocks(text))
    if not map_text.strip():
        return result

    for line in map_text.splitlines():
        stripped = line.strip()

        if not stripped or stripped.startswith("#"):
            continue

        match = re.match(r"^(.+?)\s{2,}(.+)$", stripped)
        if not match:
            continue

        name = normalize_map_name(match.group(1))
        description = match.group(2).strip()
        if not name or not description:
            continue

        path_status, target = resolve_map_target(root, folder, name)
        entry = {
            "name": name,
            "description": description,
            "pathStatus": path_status,
        }
        if target:
            entry["targetRootRelativePath"] = target
        result.append(entry)

        if len(result) >= limit:
            break

    return result


def compact_text(text: str, *, max_chars: int) -> str:
    collapsed = re.sub(r"\s+", " ", text).strip()

    if len(collapsed) <= max_chars:
        return collapsed

    return collapsed[: max_chars - 1].rstrip() + "..."


def is_topology_document(doc_path: Path, title: str | None) -> bool:
    return "topology" in doc_path.stem.casefold() or "topology" in (title or "").casefold()


def has_exact_orientation_block(sections: dict[str, str]) -> bool:
    return any(bool(section_by_name(sections, name)) for name in STRUCTURED_SECTION_NAMES)


def build_orientation_node(root: Path, doc_path: Path) -> dict[str, Any] | None:
    relative_path = normalize_doc_path(doc_path.relative_to(root))
    folder = normalize_doc_path(doc_path.parent.relative_to(root)) if doc_path.parent != root else "."
    text = read_markdown(doc_path)
    title, sections = split_markdown_sections(text)
    document_kind = "topology" if is_topology_document(doc_path, title) else "folder_orientation"

    if document_kind != "topology" and not has_exact_orientation_block(sections):
        return None

    purpose = section_by_name(sections, "Purpose")
    use_when = section_by_name(sections, "Use this folder when the question is about")
    do_not_use = section_by_name(sections, "Do not use this folder first when the question is about")
    map_section = section_by_name(sections, "Map")
    start_here = section_by_name(sections, "Start Here")
    boundaries = section_by_name(sections, "Boundaries")

    return {
        "orientationId": stable_orientation_id(relative_path),
        "kind": document_kind,
        "file": relative_path,
        "folder": folder,
        "rootRelativeFile": relative_path,
        "rootRelativeFolder": folder,
        "title": title or doc_path.name,
        "purpose": compact_text(purpose, max_chars=1200),
        "useWhen": markdown_bullets(use_when),
        "doNotUseFirstWhen": markdown_bullets(do_not_use),
        "map": markdown_map_entries(root, folder, map_section),
        "startHere": markdown_bullets(start_here),
        "boundaries": compact_text(boundaries, max_chars=1200),
        "headings": [heading for heading in sections.keys() if heading != "__intro__"],
        "lineCount": len(text.splitlines()),
        "contentHash": hashlib.sha1(text.encode("utf-8", errors="replace")).hexdigest(),
    }


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
        if node is not None
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
