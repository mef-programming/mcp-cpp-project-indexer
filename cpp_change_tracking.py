from __future__ import annotations

import fnmatch
import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cpp_project_index import LoadedProjectIndex


CHANGED_FILES_SCHEMA = "cpp.change_tracking.changed_files.v1"
REVISIONS_SCHEMA = "cpp.change_tracking.revisions.v1"
REVISION_SUMMARY_SCHEMA = "cpp.change_tracking.revision_summary.v1"
FILE_HUNKS_SCHEMA = "cpp.change_tracking.file_hunks.v1"


@dataclass(slots=True)
class ChangeTrackingAvailability:
    available: bool
    reason: str | None
    executable: Path | None
    worktree_root: Path | None


def detect_change_tracking(project_root: Path) -> ChangeTrackingAvailability:
    git_exe = shutil.which("git")

    if not git_exe:
        return ChangeTrackingAvailability(
            available=False,
            reason="git executable not found",
            executable=None,
            worktree_root=None,
        )

    inside = subprocess.run(
        [git_exe, "-C", str(project_root), "rev-parse", "--is-inside-work-tree"],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=10,
    )

    if inside.returncode != 0 or inside.stdout.strip().lower() != "true":
        return ChangeTrackingAvailability(
            available=False,
            reason="project root is not inside a worktree",
            executable=Path(git_exe),
            worktree_root=None,
        )

    root = subprocess.run(
        [git_exe, "-C", str(project_root), "rev-parse", "--show-toplevel"],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=10,
    )

    if root.returncode != 0 or not root.stdout.strip():
        return ChangeTrackingAvailability(
            available=False,
            reason="failed to resolve worktree root",
            executable=Path(git_exe),
            worktree_root=None,
        )

    return ChangeTrackingAvailability(
        available=True,
        reason=None,
        executable=Path(git_exe),
        worktree_root=Path(root.stdout.strip()),
    )


def change_tracking_tool_definitions() -> list[dict[str, Any]]:
    return [
        {
            "name": "list_changed_files",
            "description": (
                "[Change] List current changed files from the read-only change tracking layer. "
                "This returns change evidence only and does not modify the repository."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "scope": {
                        "type": "string",
                        "enum": ["working", "staged", "all"],
                        "default": "all",
                    },
                    "includeUntracked": {"type": "boolean", "default": True},
                    "filePattern": {"type": "string"},
                    "compact": {"type": "boolean", "default": True},
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 1000,
                        "default": 100,
                    },
                },
                "additionalProperties": False,
            },
        },
        {
            "name": "list_recent_revisions",
            "description": "[Change] List recent revisions from the read-only change tracking layer.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 100,
                        "default": 10,
                    },
                    "compact": {"type": "boolean", "default": True},
                },
                "additionalProperties": False,
            },
        },
        {
            "name": "get_revision_summary",
            "description": "[Change] Summarize files changed by one revision. This is read-only change evidence.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "revision": {"type": "string"},
                    "compact": {"type": "boolean", "default": True},
                    "includeMessage": {"type": "boolean", "default": True},
                    "includeFiles": {"type": "boolean", "default": True},
                    "filePattern": {"type": "string"},
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 1000,
                        "default": 100,
                    },
                },
                "required": ["revision"],
                "additionalProperties": False,
            },
        },
        {
            "name": "get_file_change_hunks",
            "description": (
                "[Change] Return structured change hunks for one file, optionally intersected with indexed "
                "symbol/data ranges. Hunks are routing evidence, not semantic analysis."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "file": {"type": "string"},
                    "scope": {
                        "type": "string",
                        "enum": ["working", "staged", "all"],
                        "default": "all",
                    },
                    "revision": {"type": "string"},
                    "symbolId": {
                        "type": "string",
                        "description": "Optional indexed symbolId. Return only hunks whose new-line range intersects that symbol.",
                    },
                    "dataId": {
                        "type": "string",
                        "description": "Optional indexed dataId. Return only hunks whose new-line range intersects that data declaration.",
                    },
                    "contextLines": {
                        "type": "integer",
                        "minimum": 0,
                        "maximum": 20,
                        "default": 1,
                    },
                    "includeSource": {"type": "boolean", "default": True},
                    "includeIndexedRanges": {"type": "boolean", "default": True},
                    "includeIndexedRangeSummary": {
                        "type": "boolean",
                        "default": False,
                        "description": (
                            "Include a compact summary grouped by intersecting indexed symbol/data range. "
                            "Useful with includeIndexedRanges:false for low-token changed-symbol routing."
                        ),
                    },
                    "indexedRangeSummaryLimit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 1000,
                        "default": 200,
                    },
                    "maxHunks": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 200,
                        "default": 20,
                    },
                    "maxLines": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 5000,
                        "default": 500,
                    },
                },
                "required": ["file"],
                "additionalProperties": False,
            },
        },
    ]


@dataclass(slots=True)
class ParsedHunk:
    header: str
    old_start: int
    old_lines: int
    new_start: int
    new_lines: int
    lines: list[str]


class ChangeTracker:
    def __init__(
        self,
        *,
        project_root: Path,
        index: LoadedProjectIndex,
        availability: ChangeTrackingAvailability,
    ) -> None:
        if not availability.available or availability.executable is None or availability.worktree_root is None:
            raise ValueError("change tracking is not available")

        self.project_root = project_root
        self.index = index
        self.executable = availability.executable
        self.worktree_root = availability.worktree_root

    def run(self, args: list[str], *, timeout: int = 20) -> str:
        completed = subprocess.run(
            [str(self.executable), "-C", str(self.project_root), *args],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )

        if completed.returncode != 0:
            message = completed.stderr.strip() or completed.stdout.strip() or "change tracking command failed"
            raise RuntimeError(message)

        return completed.stdout

    def normalize_relative_path(self, path: str) -> str:
        value = path.replace("\\", "/").strip()

        if not value:
            return value

        path_item = Path(value)

        if path_item.is_absolute():
            try:
                return path_item.relative_to(self.project_root).as_posix()
            except ValueError:
                return path_item.as_posix()

        return value

    def index_file_info(self, relative_path: str) -> dict[str, Any]:
        relative_path = self.normalize_relative_path(relative_path)
        file_id = self.index.file_id_by_relative_path.get(relative_path)

        if file_id is None:
            folded = relative_path.casefold()
            for candidate_path, candidate_id in self.index.file_id_by_relative_path.items():
                if candidate_path.casefold() == folded:
                    file_id = candidate_id
                    break

        return {
            "indexed": file_id is not None,
            "fileId": file_id,
        }

    def matches_pattern(self, relative_path: str, file_pattern: str | None) -> bool:
        if not file_pattern:
            return True

        return fnmatch.fnmatchcase(relative_path.casefold(), file_pattern.replace("\\", "/").casefold())

    def list_changed_files(
        self,
        *,
        scope: str,
        include_untracked: bool,
        file_pattern: str | None,
        compact: bool,
        limit: int,
    ) -> dict[str, Any]:
        entries = self._status_entries()
        files: list[dict[str, Any]] = []

        for entry in entries.values():
            if entry["untracked"] and not include_untracked:
                continue

            if scope == "working" and not (entry["unstaged"] or entry["untracked"]):
                continue

            if scope == "staged" and not entry["staged"]:
                continue

            if not self.matches_pattern(entry["relativePath"], file_pattern):
                continue

            info = self.index_file_info(entry["relativePath"])
            item = {
                "relativePath": entry["relativePath"],
                "changeKind": entry["changeKind"],
                "staged": entry["staged"],
                "unstaged": entry["unstaged"],
                "untracked": entry["untracked"],
                "indexed": info["indexed"],
                "fileId": info["fileId"],
            }

            if not compact:
                item["status"] = entry["status"]

            files.append(item)

        files.sort(key=lambda item: str(item["relativePath"]).casefold())
        truncated = len(files) > limit
        files = files[:limit]

        return {
            "schema": CHANGED_FILES_SCHEMA,
            "scope": scope,
            "filePattern": file_pattern,
            "returnedFiles": len(files),
            "truncated": truncated,
            "files": files,
        }

    def _status_entries(self) -> dict[str, dict[str, Any]]:
        output = self.run(["status", "--porcelain=v1", "-z", "--untracked-files=all"])
        parts = output.split("\0")
        entries: dict[str, dict[str, Any]] = {}
        index = 0

        while index < len(parts):
            raw = parts[index]
            index += 1

            if not raw:
                continue

            status = raw[:2]
            relative_path = self.normalize_relative_path(raw[3:])

            if status[0] in {"R", "C"} and index < len(parts):
                index += 1

            x_status = status[0]
            y_status = status[1]
            untracked = status == "??"
            staged = not untracked and x_status not in {" ", "?"}
            unstaged = not untracked and y_status not in {" ", "?"}

            entries[relative_path] = {
                "relativePath": relative_path,
                "status": status,
                "changeKind": self.change_kind_from_status(status),
                "staged": staged,
                "unstaged": unstaged,
                "untracked": untracked,
            }

        return entries

    @staticmethod
    def change_kind_from_status(status: str) -> str:
        if status == "??":
            return "untracked"

        if "D" in status:
            return "deleted"

        if "A" in status:
            return "added"

        if "R" in status:
            return "renamed"

        if "C" in status:
            return "copied"

        return "modified"

    def list_recent_revisions(self, *, limit: int, compact: bool) -> dict[str, Any]:
        output = self.run([
            "log",
            f"-n{limit}",
            "--format=%H%x1f%h%x1f%cI%x1f%an%x1f%s",
        ])
        revisions: list[dict[str, Any]] = []

        for line in output.splitlines():
            if not line:
                continue

            parts = line.split("\x1f", 4)

            if len(parts) != 5:
                continue

            revision, short_revision, date, author, subject = parts
            item = {
                "revision": revision,
                "shortRevision": short_revision,
                "date": date,
                "author": author,
                "subject": subject,
            }

            if not compact:
                item["worktreeRoot"] = self.worktree_root.as_posix()

            revisions.append(item)

        return {
            "schema": REVISIONS_SCHEMA,
            "returnedRevisions": len(revisions),
            "truncated": False,
            "revisions": revisions,
        }

    def get_revision_summary(
        self,
        *,
        revision: str,
        compact: bool,
        include_message: bool,
        include_files: bool,
        file_pattern: str | None,
        limit: int,
    ) -> dict[str, Any]:
        metadata = self.run([
            "log",
            "-1",
            "--format=%H%x1f%h%x1f%cI%x1f%an%x1f%s%x1f%B",
            revision,
        ])
        first_line = metadata.splitlines()[0] if metadata else ""
        parts = first_line.split("\x1f", 5)

        if len(parts) < 6:
            raise RuntimeError(f"revision not found: {revision}")

        full_revision, short_revision, date, author, subject, first_body = parts
        message = first_body

        if "\n" in metadata:
            message += "\n" + "\n".join(metadata.splitlines()[1:])

        result: dict[str, Any] = {
            "schema": REVISION_SUMMARY_SCHEMA,
            "revision": full_revision,
            "shortRevision": short_revision,
            "date": date,
            "author": author,
            "subject": subject,
        }

        if include_message:
            result["message"] = message.strip()

        files: list[dict[str, Any]] = []

        if include_files:
            files = self._revision_files(
                revision=revision,
                file_pattern=file_pattern,
            )
            truncated = len(files) > limit
            files = files[:limit]
            result.update(
                {
                    "filePattern": file_pattern,
                    "returnedFiles": len(files),
                    "truncated": truncated,
                    "files": files,
                }
            )
        else:
            result.update(
                {
                    "filePattern": file_pattern,
                    "returnedFiles": 0,
                    "truncated": False,
                    "files": [],
                }
            )

        if not compact:
            result["worktreeRoot"] = self.worktree_root.as_posix()

        return result

    def _revision_files(self, *, revision: str, file_pattern: str | None) -> list[dict[str, Any]]:
        name_status = self.run(["show", "--format=", "--name-status", "--find-renames", revision])
        numstat = self.run(["show", "--format=", "--numstat", "--find-renames", revision])
        stats: dict[str, tuple[int | None, int | None]] = {}

        for line in numstat.splitlines():
            parts = line.split("\t")

            if len(parts) < 3:
                continue

            added_text, deleted_text, path = parts[0], parts[1], parts[-1]
            added = int(added_text) if added_text.isdigit() else None
            deleted = int(deleted_text) if deleted_text.isdigit() else None
            stats[self.normalize_relative_path(path)] = (added, deleted)

        files: list[dict[str, Any]] = []

        for line in name_status.splitlines():
            parts = line.split("\t")

            if len(parts) < 2:
                continue

            status = parts[0]
            path = parts[-1]
            relative_path = self.normalize_relative_path(path)

            if not self.matches_pattern(relative_path, file_pattern):
                continue

            added, deleted = stats.get(relative_path, (None, None))
            info = self.index_file_info(relative_path)
            files.append(
                {
                    "relativePath": relative_path,
                    "changeKind": self.change_kind_from_name_status(status),
                    "addedLines": added,
                    "deletedLines": deleted,
                    "indexed": info["indexed"],
                    "fileId": info["fileId"],
                }
            )

        files.sort(key=lambda item: str(item["relativePath"]).casefold())
        return files

    @staticmethod
    def change_kind_from_name_status(status: str) -> str:
        first = status[0] if status else "M"
        return {
            "A": "added",
            "D": "deleted",
            "R": "renamed",
            "C": "copied",
            "M": "modified",
            "T": "modified",
        }.get(first, "modified")

    def get_file_change_hunks(
        self,
        *,
        file: str,
        scope: str,
        revision: str | None,
        symbol_id: str | None,
        data_id: str | None,
        context_lines: int,
        include_source: bool,
        include_indexed_ranges: bool,
        include_indexed_range_summary: bool,
        indexed_range_summary_limit: int,
        max_hunks: int,
        max_lines: int,
    ) -> dict[str, Any]:
        relative_path = self.normalize_relative_path(file)
        info = self.index_file_info(relative_path)
        diff_text = self.diff_for_file(
            relative_path=relative_path,
            scope=scope,
            revision=revision,
            context_lines=context_lines,
        )
        parsed_hunks = parse_unified_diff_hunks(diff_text)

        if not parsed_hunks and revision is None and self._is_untracked(relative_path):
            parsed_hunks = [self.untracked_file_hunk(relative_path)]

        target_range = self.change_target_range(
            symbol_id=symbol_id,
            data_id=data_id,
        )
        hunks: list[dict[str, Any]] = []
        summary_inputs: list[tuple[dict[str, Any], list[dict[str, Any]]]] = []
        total_lines = 0
        truncated = False

        for parsed in parsed_hunks:
            if target_range is not None and not hunk_intersects_target(parsed, target_range):
                continue

            if len(hunks) >= max_hunks:
                truncated = True
                break

            hunk_line_count = len(parsed.lines)

            if total_lines + hunk_line_count > max_lines:
                truncated = True
                break

            total_lines += hunk_line_count
            indexed_ranges = (
                self.indexed_ranges_for_hunk(
                    file_id=info["fileId"],
                    new_start=parsed.new_start,
                    new_lines=parsed.new_lines,
                )
                if include_indexed_ranges or include_indexed_range_summary
                else []
            )
            hunk_json = self.hunk_to_json(
                parsed,
                include_source=include_source,
                include_indexed_ranges=include_indexed_ranges,
                indexed_ranges=indexed_ranges,
            )
            hunks.append(hunk_json)
            summary_inputs.append((hunk_json, indexed_ranges))

        result = {
            "schema": FILE_HUNKS_SCHEMA,
            "relativePath": relative_path,
            "fileId": info["fileId"],
            "scope": None if revision is not None else scope,
            "revision": revision,
            "symbolId": symbol_id,
            "dataId": data_id,
            "contextLines": context_lines,
            "returnedHunks": len(hunks),
            "truncated": truncated,
            "hunks": hunks,
        }

        if include_indexed_range_summary:
            summary, summary_truncated = summarize_indexed_ranges_for_hunks(
                summary_inputs,
                limit=indexed_range_summary_limit,
            )
            result["summaryByIndexedRange"] = summary
            result["returnedIndexedRangeSummaries"] = len(summary)
            result["indexedRangeSummaryTruncated"] = summary_truncated

        return result

    def change_target_range(
        self,
        *,
        symbol_id: str | None,
        data_id: str | None,
    ) -> dict[str, Any] | None:
        if symbol_id is not None:
            symbol = self.index.symbol_by_id.get(symbol_id)

            if symbol is None:
                return None

            return {
                "kind": "symbol",
                "fileId": symbol.get("fileId"),
                "startLine": symbol.get("startLine"),
                "endLine": symbol.get("endLine"),
            }

        if data_id is not None:
            item = self.index.data_by_id.get(data_id)

            if item is None:
                return None

            return {
                "kind": "data",
                "fileId": item.get("fileId"),
                "startLine": item.get("startLine"),
                "endLine": item.get("endLine"),
            }

        return None

    def diff_for_file(
        self,
        *,
        relative_path: str,
        scope: str,
        revision: str | None,
        context_lines: int,
    ) -> str:
        if revision is not None:
            return self.run([
                "show",
                "--format=",
                f"--unified={context_lines}",
                revision,
                "--",
                relative_path,
            ])

        if scope == "staged":
            return self.run(["diff", "--cached", f"--unified={context_lines}", "--", relative_path])

        if scope == "working":
            return self.run(["diff", f"--unified={context_lines}", "--", relative_path])

        return self.run(["diff", "HEAD", f"--unified={context_lines}", "--", relative_path])

    def _is_untracked(self, relative_path: str) -> bool:
        status = self._status_entries().get(relative_path)
        return bool(status and status["untracked"])

    def untracked_file_hunk(self, relative_path: str) -> ParsedHunk:
        path = self.project_root / relative_path

        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            lines = []

        return ParsedHunk(
            header=f"@@ -0,0 +1,{len(lines)} @@",
            old_start=0,
            old_lines=0,
            new_start=1,
            new_lines=len(lines),
            lines=["+" + line for line in lines],
        )

    def hunk_to_json(
        self,
        hunk: ParsedHunk,
        *,
        include_source: bool,
        include_indexed_ranges: bool,
        indexed_ranges: list[dict[str, Any]],
    ) -> dict[str, Any]:
        added_line_numbers: list[int] = []
        removed_line_count = 0
        source_lines: list[str] = []
        old_line = hunk.old_start
        new_line = hunk.new_start

        for line in hunk.lines:
            if line.startswith("+") and not line.startswith("+++"):
                added_line_numbers.append(new_line)

                if include_source:
                    source_lines.append(f"+{new_line:04d}: {line[1:]}")

                new_line += 1
            elif line.startswith("-") and not line.startswith("---"):
                removed_line_count += 1

                if include_source:
                    source_lines.append(f"-{old_line:04d}: {line[1:]}")

                old_line += 1
            elif line.startswith(" "):
                if include_source:
                    source_lines.append(f" {new_line:04d}: {line[1:]}")

                old_line += 1
                new_line += 1

        result: dict[str, Any] = {
            "header": hunk.header,
            "oldStart": hunk.old_start,
            "oldLines": hunk.old_lines,
            "newStart": hunk.new_start,
            "newLines": hunk.new_lines,
            "addedLineNumbers": added_line_numbers,
            "removedLineCount": removed_line_count,
        }

        if include_source:
            result["source"] = "\n".join(source_lines)

        if include_indexed_ranges:
            result["indexedRanges"] = indexed_ranges

        return result

    def indexed_ranges_for_hunk(
        self,
        *,
        file_id: str | None,
        new_start: int,
        new_lines: int,
    ) -> list[dict[str, Any]]:
        if file_id is None:
            return []

        if new_lines <= 0:
            range_start = max(1, new_start)
            range_end = range_start
        else:
            range_start = max(1, new_start)
            range_end = new_start + new_lines - 1

        ranges: list[dict[str, Any]] = []

        for symbol in self.index.symbols:
            if symbol.get("fileId") != file_id:
                continue

            if ranges_intersect(range_start, range_end, int(symbol.get("startLine") or 0), int(symbol.get("endLine") or 0)):
                ranges.append(
                    {
                        "kind": "symbol",
                        "symbolId": symbol.get("symbolId"),
                        "type": symbol.get("type"),
                        "qualifiedName": symbol.get("qualifiedName") or symbol.get("shortName"),
                        "startLine": symbol.get("startLine"),
                        "endLine": symbol.get("endLine"),
                    }
                )

        for data_item in self.index.data:
            if data_item.get("fileId") != file_id:
                continue

            if ranges_intersect(range_start, range_end, int(data_item.get("startLine") or 0), int(data_item.get("endLine") or 0)):
                ranges.append(
                    {
                        "kind": "data",
                        "dataId": data_item.get("dataId"),
                        "declarationKind": data_item.get("declarationKind"),
                        "qualifiedName": data_item.get("qualifiedName") or data_item.get("name"),
                        "startLine": data_item.get("startLine"),
                        "endLine": data_item.get("endLine"),
                    }
                )

        ranges.sort(
            key=lambda item: (
                int(item.get("startLine") or 0),
                str(item.get("kind") or ""),
                str(item.get("qualifiedName") or ""),
            )
        )
        return ranges


def summarize_indexed_ranges_for_hunks(
    hunk_ranges: list[tuple[dict[str, Any], list[dict[str, Any]]]],
    *,
    limit: int,
) -> tuple[list[dict[str, Any]], bool]:
    grouped: dict[str, dict[str, Any]] = {}

    for hunk_index, (hunk, ranges) in enumerate(hunk_ranges):
        new_start = int(hunk.get("newStart") or 0)
        new_lines = int(hunk.get("newLines") or 0)
        changed_start = max(1, new_start)
        changed_end = changed_start if new_lines <= 0 else new_start + new_lines - 1

        for item in ranges:
            key = indexed_range_summary_key(item)
            summary = grouped.get(key)

            if summary is None:
                summary = {
                    "kind": item.get("kind"),
                    "startLine": item.get("startLine"),
                    "endLine": item.get("endLine"),
                    "qualifiedName": item.get("qualifiedName"),
                    "hunkCount": 0,
                    "hunkIndexes": [],
                    "changedLineRanges": [],
                    "addedLineCount": 0,
                    "removedLineCount": 0,
                }

                if item.get("kind") == "symbol":
                    summary["symbolId"] = item.get("symbolId")
                    summary["type"] = item.get("type")
                else:
                    summary["dataId"] = item.get("dataId")
                    summary["declarationKind"] = item.get("declarationKind")

                grouped[key] = summary

            summary["hunkCount"] = int(summary["hunkCount"]) + 1
            summary["hunkIndexes"].append(hunk_index)
            summary["changedLineRanges"].append(
                {
                    "startLine": changed_start,
                    "endLine": changed_end,
                }
            )
            summary["addedLineCount"] = (
                int(summary["addedLineCount"]) + len(hunk.get("addedLineNumbers") or [])
            )
            summary["removedLineCount"] = (
                int(summary["removedLineCount"]) + int(hunk.get("removedLineCount") or 0)
            )

    summaries = sorted(
        grouped.values(),
        key=lambda item: (
            int(item.get("startLine") or 0),
            str(item.get("kind") or ""),
            str(item.get("qualifiedName") or ""),
        ),
    )
    truncated = len(summaries) > limit
    return summaries[:limit], truncated


def indexed_range_summary_key(item: dict[str, Any]) -> str:
    if item.get("kind") == "symbol":
        return f"symbol:{item.get('symbolId')}"

    if item.get("kind") == "data":
        return f"data:{item.get('dataId')}"

    return (
        f"{item.get('kind')}:{item.get('qualifiedName')}:"
        f"{item.get('startLine')}:{item.get('endLine')}"
    )


HUNK_HEADER_RE = re.compile(
    r"^@@ -(?P<old_start>\d+)(?:,(?P<old_lines>\d+))? "
    r"\+(?P<new_start>\d+)(?:,(?P<new_lines>\d+))? @@"
)


def parse_unified_diff_hunks(diff_text: str) -> list[ParsedHunk]:
    hunks: list[ParsedHunk] = []
    current: ParsedHunk | None = None

    for line in diff_text.splitlines():
        match = HUNK_HEADER_RE.match(line)

        if match:
            if current is not None:
                hunks.append(current)

            current = ParsedHunk(
                header=line,
                old_start=int(match.group("old_start")),
                old_lines=int(match.group("old_lines") or "1"),
                new_start=int(match.group("new_start")),
                new_lines=int(match.group("new_lines") or "1"),
                lines=[],
            )
            continue

        if current is None:
            continue

        if line.startswith((" ", "+", "-", "\\")):
            current.lines.append(line)

    if current is not None:
        hunks.append(current)

    return hunks


def ranges_intersect(left_start: int, left_end: int, right_start: int, right_end: int) -> bool:
    return left_start <= right_end and right_start <= left_end


def hunk_intersects_target(hunk: ParsedHunk, target_range: dict[str, Any]) -> bool:
    if hunk.new_lines <= 0:
        range_start = max(1, hunk.new_start)
        range_end = range_start
    else:
        range_start = max(1, hunk.new_start)
        range_end = hunk.new_start + hunk.new_lines - 1

    return ranges_intersect(
        range_start,
        range_end,
        int(target_range.get("startLine") or 0),
        int(target_range.get("endLine") or 0),
    )
