from __future__ import annotations

from typing import Any


COMMENT_CONTEXT_SCHEMA = "cpp.comment_context.v1"


LINE_COMMENT_PREFIXES = ("//", "///", "//!")


def format_source_lines(lines: list[str], start_line: int, end_line: int) -> str:
    start_line = max(1, start_line)
    end_line = min(len(lines), end_line)

    if start_line > end_line:
        return ""

    return "\n".join(
        f"{line_no:04d}: {lines[line_no - 1]}"
        for line_no in range(start_line, end_line + 1)
    )


def comment_text_from_source(source: str) -> str:
    if not source:
        return ""

    result: list[str] = []

    for line in source.splitlines():
        # Strip the generated `0001: ` prefix if present.
        if len(line) >= 6 and line[:4].isdigit() and line[4:6] == ": ":
            result.append(line[6:])
        else:
            result.append(line)

    return "\n".join(result)


def _strip_bom(text: str) -> str:
    return text.removeprefix("\ufeff")


def _is_global_module_fragment_line(line: str) -> bool:
    return _strip_bom(line).strip() == "module;"


def _is_blank(line: str) -> bool:
    return not line.strip()


def _is_line_comment(line: str) -> bool:
    stripped = line.lstrip()
    return stripped.startswith("//")


def _looks_like_block_comment_line(line: str) -> bool:
    stripped = line.lstrip()
    return stripped.startswith("/*") or stripped.startswith("*") or "*/" in stripped


def _has_block_comment_end(line: str) -> bool:
    return "*/" in line


def _has_block_comment_start(line: str) -> bool:
    return "/*" in line


def _is_code_before_block_start(line: str) -> bool:
    index = line.find("/*")

    if index < 0:
        return False

    return bool(line[:index].strip())


def _is_code_after_block_end(line: str) -> bool:
    index = line.rfind("*/")

    if index < 0:
        return False

    return bool(line[index + 2 :].strip())


def _empty_leading_result(
    *,
    relative_path: str,
    target_start_line: int,
    target_end_line: int,
) -> dict[str, Any]:
    return {
        "schema": COMMENT_CONTEXT_SCHEMA,
        "relativePath": relative_path,
        "targetStartLine": target_start_line,
        "targetEndLine": target_end_line,
        "hasComment": False,
        "commentStartLine": None,
        "commentEndLine": None,
        "source": "",
        "text": "",
    }


def _empty_header_result(*, relative_path: str) -> dict[str, Any]:
    return {
        "schema": COMMENT_CONTEXT_SCHEMA,
        "relativePath": relative_path,
        "hasComment": False,
        "commentStartLine": None,
        "commentEndLine": None,
        "source": "",
        "text": "",
    }


def _collect_block_comment_ending_at(
    *,
    lines: list[str],
    end_index: int,
    min_index: int,
) -> tuple[int, int] | None:
    index = end_index

    while index >= min_index:
        line = lines[index]

        if _has_block_comment_start(line):
            # Leading comments must not be attached when the block starts after
            # real code on the same line, e.g. `int x; /* comment */`.
            if _is_code_before_block_start(line):
                return None

            # The line that closes the block must also not contain following
            # code. This keeps the extractor conservative.
            if _is_code_after_block_end(lines[end_index]):
                return None

            return index, end_index

        # Inside a block comment, middle lines usually start with `*` or are
        # blank. Anything that looks like real code means this is not a clean
        # leading block comment.
        if not _looks_like_block_comment_line(line) and not _is_blank(line):
            return None

        index -= 1

    return None


def extract_leading_comment(
    *,
    lines: list[str],
    relative_path: str,
    target_start_line: int,
    target_end_line: int,
    max_lines: int = 20,
    allow_blank_gap: bool = True,
    max_blank_gap: int = 2,
) -> dict[str, Any]:
    """Extract the exact leading comment range before a known source range.

    This intentionally does not index comments. It only inspects the original
    source lines immediately before a symbol/data declaration on demand.

    V1 is conservative:
    - line-comment blocks are collected when directly above the target
    - block comments are collected when the block ends directly above the target
    - optional blank gap is allowed only between comment and target
    - comments separated from the target by real code are not attached
    """

    if target_start_line <= 1 or not lines:
        return _empty_leading_result(
            relative_path=relative_path,
            target_start_line=target_start_line,
            target_end_line=target_end_line,
        )

    max_lines = max(1, max_lines)
    index = min(len(lines), target_start_line - 1) - 1
    blank_gap = 0

    if allow_blank_gap:
        while index >= 0 and _is_blank(lines[index]) and blank_gap < max_blank_gap:
            blank_gap += 1
            index -= 1

    if index < 0:
        return _empty_leading_result(
            relative_path=relative_path,
            target_start_line=target_start_line,
            target_end_line=target_end_line,
        )

    min_index = max(0, index - max_lines + 1)

    if _is_line_comment(lines[index]):
        end_index = index

        while index >= min_index and _is_line_comment(lines[index]):
            index -= 1

        start_index = index + 1
        source = format_source_lines(lines, start_index + 1, end_index + 1)

        return {
            "schema": COMMENT_CONTEXT_SCHEMA,
            "relativePath": relative_path,
            "targetStartLine": target_start_line,
            "targetEndLine": target_end_line,
            "hasComment": True,
            "commentStartLine": start_index + 1,
            "commentEndLine": end_index + 1,
            "source": source,
            "text": comment_text_from_source(source),
        }

    if _has_block_comment_end(lines[index]) and _looks_like_block_comment_line(lines[index]):
        block_range = _collect_block_comment_ending_at(
            lines=lines,
            end_index=index,
            min_index=min_index,
        )

        if block_range is not None:
            start_index, end_index = block_range
            source = format_source_lines(lines, start_index + 1, end_index + 1)

            return {
                "schema": COMMENT_CONTEXT_SCHEMA,
                "relativePath": relative_path,
                "targetStartLine": target_start_line,
                "targetEndLine": target_end_line,
                "hasComment": True,
                "commentStartLine": start_index + 1,
                "commentEndLine": end_index + 1,
                "source": source,
                "text": comment_text_from_source(source),
            }

    if _has_block_comment_start(lines[index]) and _has_block_comment_end(lines[index]):
        if not _is_code_before_block_start(lines[index]) and not _is_code_after_block_end(lines[index]):
            source = format_source_lines(lines, index + 1, index + 1)

            return {
                "schema": COMMENT_CONTEXT_SCHEMA,
                "relativePath": relative_path,
                "targetStartLine": target_start_line,
                "targetEndLine": target_end_line,
                "hasComment": True,
                "commentStartLine": index + 1,
                "commentEndLine": index + 1,
                "source": source,
                "text": comment_text_from_source(source),
            }

    return _empty_leading_result(
        relative_path=relative_path,
        target_start_line=target_start_line,
        target_end_line=target_end_line,
    )


def _collect_line_comment_header(
    *,
    lines: list[str],
    start_index: int,
    max_index: int,
) -> tuple[int, int]:
    index = start_index

    while index <= max_index and _is_line_comment(lines[index]):
        index += 1

    return start_index, index - 1


def _collect_block_comment_header(
    *,
    lines: list[str],
    start_index: int,
    max_index: int,
) -> tuple[int, int] | None:
    if not _has_block_comment_start(lines[start_index]):
        return None

    if _is_code_before_block_start(lines[start_index]):
        return None

    index = start_index

    while index <= max_index:
        if _has_block_comment_end(lines[index]):
            if _is_code_after_block_end(lines[index]):
                return None
            return start_index, index

        index += 1

    return None


def extract_file_header_comment(
    *,
    lines: list[str],
    relative_path: str,
    max_lines: int = 120,
) -> dict[str, Any]:
    """Extract a file-header comment from the beginning of a file.

    V1 returns only the first initial comment block. It skips leading blank
    lines, then stops at the first non-comment code line. A blank line after the
    first comment block terminates the header.
    """

    if not lines:
        return _empty_header_result(relative_path=relative_path)

    max_index = min(len(lines), max(1, max_lines)) - 1
    index = 0

    while index <= max_index and _is_blank(lines[index]):
        index += 1

    # C++20 module implementation/interface files often start with a global
    # module fragment:
    #
    #     module;
    #     // file-level/module-level rationale comment
    #     #include "stdafx.h"
    #
    # Treat `module;` as a permitted prefix for file-header extraction. The
    # returned header range still contains only the comment lines, not `module;`.
    if index <= max_index and _is_global_module_fragment_line(lines[index]):
        index += 1

        while index <= max_index and _is_blank(lines[index]):
            index += 1

    if index > max_index:
        return _empty_header_result(relative_path=relative_path)

    if _is_line_comment(lines[index]):
        start_index, end_index = _collect_line_comment_header(
            lines=lines,
            start_index=index,
            max_index=max_index,
        )
        source = format_source_lines(lines, start_index + 1, end_index + 1)

        return {
            "schema": COMMENT_CONTEXT_SCHEMA,
            "relativePath": relative_path,
            "hasComment": True,
            "commentStartLine": start_index + 1,
            "commentEndLine": end_index + 1,
            "source": source,
            "text": comment_text_from_source(source),
        }

    if _has_block_comment_start(lines[index]):
        block_range = _collect_block_comment_header(
            lines=lines,
            start_index=index,
            max_index=max_index,
        )

        if block_range is not None:
            start_index, end_index = block_range
            source = format_source_lines(lines, start_index + 1, end_index + 1)

            return {
                "schema": COMMENT_CONTEXT_SCHEMA,
                "relativePath": relative_path,
                "hasComment": True,
                "commentStartLine": start_index + 1,
                "commentEndLine": end_index + 1,
                "source": source,
                "text": comment_text_from_source(source),
            }

    return _empty_header_result(relative_path=relative_path)
