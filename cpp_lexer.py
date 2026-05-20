from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from cpp_index_model import Token
from cpp_index_utils import normalize_signature_spacing


# ---------------------------------------------------------------------------
# Stream helpers
# ---------------------------------------------------------------------------

def _is_identifier_start(ch: str) -> bool:
    return ch == "_" or ch.isalpha()


def _is_identifier_continue(ch: str) -> bool:
    return ch == "_" or ch.isalnum()


def _try_raw_string_start(line: str, index: int) -> tuple[str, int] | None:
    # C++ raw string literal:
    #   R"delimiter(raw text)delimiter"
    #
    # Supported encoding prefixes:
    #   R"..."
    #   LR"..."
    #   u8R"..."
    #   uR"..."
    #   UR"..."
    #
    # delimiter is at most 16 chars and cannot contain whitespace, backslash,
    # parentheses, or quotes. This function returns (terminator, after_open).
    prefixes = ("u8R", "LR", "uR", "UR", "R")

    matched_prefix = None

    for prefix in prefixes:
        if line.startswith(prefix + '"', index):
            matched_prefix = prefix
            break

    if matched_prefix is None:
        return None

    j = index + len(matched_prefix) + 1
    delimiter_chars: list[str] = []

    while j < len(line):
        ch = line[j]

        if ch == "(":
            delimiter = "".join(delimiter_chars)
            return f"){delimiter}\"", j + 1

        if ch.isspace() or ch in {"\\", ")", '"'}:
            return None

        delimiter_chars.append(ch)

        if len(delimiter_chars) > 16:
            return None

        j += 1

    return None


def _find_raw_terminator(line: str, start: int, terminator: str) -> int | None:
    pos = line.find(terminator, start)

    if pos < 0:
        return None

    return pos + len(terminator)


def _preprocessor_directive(line: str) -> str | None:
    stripped = line.lstrip()

    if not stripped.startswith("#"):
        return None

    text = stripped[1:].lstrip()

    if not text:
        return ""

    return text.split(None, 1)[0]


def _preprocessor_expression_is_zero(line: str) -> bool:
    stripped = line.lstrip()

    if not stripped.startswith("#"):
        return False

    text = stripped[1:].strip()
    return text == "if 0" or text.startswith("if 0 ")


def _current_preprocessor_active(stack: list[PreprocessorFrame]) -> bool:
    return all(frame.this_branch_active for frame in stack)


# ---------------------------------------------------------------------------
# Comment blanking
# ---------------------------------------------------------------------------

def blank_comments_preserve_lines(lines: list[str]) -> list[str]:
    """Return source lines with comments blanked, preserving line count.

    This is intentionally stream/state based, not regex based. Block comments and
    line comments are replaced by spaces so later token column positions remain
    stable. String, char, and raw string literals are preserved.
    """

    result: list[str] = []
    in_block_comment = False
    raw_terminator: str | None = None

    def preserve_trailing_continuation_backslashes(original: str, blanked: str) -> str:
        stripped = original.rstrip()

        if not stripped.endswith("\\"):
            return blanked

        chars = list(blanked)
        index = len(stripped) - 1

        while index >= 0 and original[index] == "\\":
            if index >= len(chars):
                chars.extend(" " for _ in range(index - len(chars) + 1))

            chars[index] = "\\"
            index -= 1

        return "".join(chars)

    for line in lines:
        out: list[str] = []
        index = 0
        in_string = False
        in_char = False
        escape = False

        while index < len(line):
            ch = line[index]
            next_ch = line[index + 1] if index + 1 < len(line) else ""

            if raw_terminator is not None:
                end = _find_raw_terminator(line, index, raw_terminator)

                if end is None:
                    out.append(line[index:])
                    index = len(line)
                    continue

                out.append(line[index:end])
                index = end
                raw_terminator = None
                continue

            if in_block_comment:
                if ch == "*" and next_ch == "/":
                    out.append(" ")
                    out.append(" ")
                    index += 2
                    in_block_comment = False
                    continue

                out.append("\t" if ch == "\t" else " ")
                index += 1
                continue

            raw_start = None

            if not in_string and not in_char:
                raw_start = _try_raw_string_start(line, index)

            if raw_start is not None:
                terminator, after_open = raw_start
                raw_terminator = terminator
                out.append(line[index:after_open])
                index = after_open
                continue

            if escape:
                out.append(ch)
                escape = False
                index += 1
                continue

            if ch == "\\" and (in_string or in_char):
                out.append(ch)
                escape = True
                index += 1
                continue

            if in_string:
                out.append(ch)

                if ch == '"':
                    in_string = False

                index += 1
                continue

            if in_char:
                out.append(ch)

                if ch == "'":
                    in_char = False

                index += 1
                continue

            if ch == '"':
                out.append(ch)
                in_string = True
                index += 1
                continue

            if ch == "'":
                out.append(ch)
                in_char = True
                index += 1
                continue

            if ch == "/" and next_ch == "/":
                # Preserve columns by blanking the rest of the physical line.
                out.append(" " * (len(line) - index))
                index = len(line)
                continue

            if ch == "/" and next_ch == "*":
                out.append(" ")
                out.append(" ")
                index += 2
                in_block_comment = True
                continue

            out.append(ch)
            index += 1

        result.append(preserve_trailing_continuation_backslashes(line, "".join(out)))

    return result


# ---------------------------------------------------------------------------
# Tokenization
# ---------------------------------------------------------------------------

_MULTI_CHAR_SYMBOLS_3 = {
    "<=>",
    "...",
}

_MULTI_CHAR_SYMBOLS_2 = {
    "::",
    "->",
    ".*",
    "->*",
    "==",
    "!=",
    "<=",
    ">=",
    "&&",
    "||",
    "++",
    "--",
    "+=",
    "-=",
    "*=",
    "/=",
    "%=",
    "&=",
    "|=",
    "^=",
    "<<",
    ">>",
}

_SINGLE_CHAR_SYMBOLS = set("{}()[];,:<>*=~&|+-/!?.#%^")

def is_preprocessor_continuation_line(line: str) -> bool:
    stripped = line.rstrip()

    # Count trailing backslashes. An odd number means line continuation.
    count = 0

    for ch in reversed(stripped):
        if ch == "\\":
            count += 1
        else:
            break

    return (count % 2) == 1

@dataclass(slots=True)
class PreprocessorFrame:
    parent_active: bool
    this_branch_active: bool
    any_branch_taken: bool
    
def tokenize_lines(lines: list[str]) -> list[Token]:
    """Tokenize C++ source with a small stream scanner.

    The scanner skips comment/string/char/raw-string contents. It is not a full
    C++ lexer; it only emits tokens needed by the routing indexer.
    """

    pp_stack: list[PreprocessorFrame] = []
    in_preprocessor = False

    tokens: list[Token] = []
    raw_terminator: str | None = None
    in_block_comment = False

    for line_no, line in enumerate(lines, start=1):
        if in_preprocessor:
            in_preprocessor = is_preprocessor_continuation_line(line)
            continue

        directive = _preprocessor_directive(line)

        if directive in {"if", "ifdef", "ifndef"}:
            parent_active = _current_preprocessor_active(pp_stack)

            if directive == "if" and _preprocessor_expression_is_zero(line):
                branch_active = False
            else:
                # V1: assume unknown #if/#ifdef/#ifndef branch is active.
                # This avoids hiding real source unless it is explicit #if 0.
                branch_active = True

            pp_stack.append(
                PreprocessorFrame(
                    parent_active=parent_active,
                    this_branch_active=parent_active and branch_active,
                    any_branch_taken=parent_active and branch_active,
                )
            )
            continue

        if directive in {"else", "elif"}:
            if pp_stack:
                frame = pp_stack[-1]

                # V1 rule:
                # Unknown #if/#ifdef/#ifndef conditions are not evaluated.
                # Therefore do not hide #else/#elif branches for unknown conditions.
                # Only explicit #if 0 suppresses its first branch; after #else/#elif
                # the branch becomes visible again.
                frame.this_branch_active = frame.parent_active
                frame.any_branch_taken = True

            continue

        if directive == "endif":
            if pp_stack:
                pp_stack.pop()
            continue

        if not _current_preprocessor_active(pp_stack):
            continue

        if directive is not None:
            in_preprocessor = is_preprocessor_continuation_line(line)
            continue

        index = 0
        in_string = False
        in_char = False
        escape = False

        while index < len(line):
            ch = line[index]
            next_ch = line[index + 1] if index + 1 < len(line) else ""

            if raw_terminator is not None:
                end = _find_raw_terminator(line, index, raw_terminator)

                if end is None:
                    break

                index = end
                raw_terminator = None
                continue

            if in_block_comment:
                if ch == "*" and next_ch == "/":
                    index += 2
                    in_block_comment = False
                    continue

                index += 1
                continue

            raw_start = None

            if not in_string and not in_char:
                raw_start = _try_raw_string_start(line, index)

            if raw_start is not None:
                terminator, after_open = raw_start
                raw_terminator = terminator
                index = after_open
                continue

            if escape:
                escape = False
                index += 1
                continue

            if ch == "\\" and (in_string or in_char):
                escape = True
                index += 1
                continue

            if in_string:
                if ch == '"':
                    in_string = False

                index += 1
                continue

            if in_char:
                if ch == "'":
                    in_char = False

                index += 1
                continue

            if ch == '"':
                in_string = True
                index += 1
                continue

            if ch == "'":
                in_char = True
                index += 1
                continue

            if ch == "/" and next_ch == "/":
                break

            if ch == "/" and next_ch == "*":
                index += 2
                in_block_comment = True
                continue

            if ch.isspace():
                index += 1
                continue

            if _is_identifier_start(ch):
                start = index
                index += 1

                while index < len(line) and _is_identifier_continue(line[index]):
                    index += 1

                tokens.append(
                    Token(
                        value=line[start:index],
                        kind="identifier",
                        line=line_no,
                        col0=start,
                    )
                )
                continue

            if ch.isdigit():
                start = index
                index += 1

                while index < len(line):
                    current = line[index]

                    if current.isalnum() or current in {"_", ".", "'"}:
                        index += 1
                        continue

                    break

                tokens.append(
                    Token(
                        value=line[start:index],
                        kind="number",
                        line=line_no,
                        col0=start,
                    )
                )
                continue

            three = line[index : index + 3]
            two = line[index : index + 2]

            if three in _MULTI_CHAR_SYMBOLS_3:
                tokens.append(Token(value=three, kind="symbol", line=line_no, col0=index))
                index += 3
                continue

            if two in _MULTI_CHAR_SYMBOLS_2:
                tokens.append(Token(value=two, kind="symbol", line=line_no, col0=index))
                index += 2
                continue

            if ch in _SINGLE_CHAR_SYMBOLS:
                tokens.append(Token(value=ch, kind="symbol", line=line_no, col0=index))
                index += 1
                continue

            tokens.append(Token(value=ch, kind="symbol", line=line_no, col0=index))
            index += 1

    return tokens


# ---------------------------------------------------------------------------
# Token formatting helpers
# ---------------------------------------------------------------------------

def token_values(tokens: Iterable[Token]) -> list[str]:
    return [token.value for token in tokens]


def tokens_to_text(tokens: list[Token]) -> str:
    if not tokens:
        return ""

    parts: list[str] = []
    previous = ""

    no_space_before = {
        ",",
        ";",
        ")",
        "]",
        ">",
        "::",
        ".",
        "->",
    }
    no_space_after = {
        "(",
        "[",
        "<",
        "::",
        ".",
        "->",
        "~",
    }

    for token in tokens:
        value = token.value

        if not parts:
            parts.append(value)
        elif value in no_space_before or previous in no_space_after:
            parts.append(value)
        else:
            parts.append(" ")
            parts.append(value)

        previous = value

    return normalize_signature_spacing("".join(parts))


def first_identifier(tokens: list[Token]) -> Token | None:
    for token in tokens:
        if token.kind == "identifier":
            return token

    return None


def previous_token(tokens: list[Token], before_index: int) -> Token | None:
    index = before_index - 1

    if index < 0:
        return None

    return tokens[index]


def find_matching_token(
    tokens: list[Token],
    open_index: int,
    open_value: str,
    close_value: str,
) -> int | None:
    depth = 0

    for index in range(open_index, len(tokens)):
        value = tokens[index].value

        if value == open_value:
            depth += 1
        elif value == close_value:
            depth -= 1

            if depth == 0:
                return index

    return None


def split_top_level_commas(tokens: list[Token]) -> list[list[Token]]:
    parts: list[list[Token]] = []
    current: list[Token] = []
    angle_depth = 0
    paren_depth = 0
    bracket_depth = 0

    for token in tokens:
        value = token.value

        if value == "<":
            angle_depth += 1
        elif value == ">":
            angle_depth = max(0, angle_depth - 1)
        elif value == "(":
            paren_depth += 1
        elif value == ")":
            paren_depth = max(0, paren_depth - 1)
        elif value == "[":
            bracket_depth += 1
        elif value == "]":
            bracket_depth = max(0, bracket_depth - 1)

        if (
            value == ","
            and angle_depth == 0
            and paren_depth == 0
            and bracket_depth == 0
        ):
            if current:
                parts.append(current)
                current = []
            continue

        current.append(token)

    if current:
        parts.append(current)

    return parts


def iter_code_chars(lines: list[str]):
    """Yield code characters outside comments/string/char/raw-string regions.

    Useful for lightweight depth scans. Returns tuples:
      (line_no, col0, ch)
    """

    raw_terminator: str | None = None
    in_block_comment = False
    pp_stack: list[PreprocessorFrame] = []
    in_preprocessor = False

    for line_no, line in enumerate(lines, start=1):
        if in_preprocessor:
            in_preprocessor = is_preprocessor_continuation_line(line)
            continue

        directive = _preprocessor_directive(line)

        if directive in {"if", "ifdef", "ifndef"}:
            parent_active = _current_preprocessor_active(pp_stack)

            if directive == "if" and _preprocessor_expression_is_zero(line):
                branch_active = False
            else:
                branch_active = True

            pp_stack.append(
                PreprocessorFrame(
                    parent_active=parent_active,
                    this_branch_active=parent_active and branch_active,
                    any_branch_taken=parent_active and branch_active,
                )
            )
            continue

        if directive in {"else", "elif"}:
            if pp_stack:
                frame = pp_stack[-1]
                frame.this_branch_active = frame.parent_active
                frame.any_branch_taken = True
            continue

        if directive == "endif":
            if pp_stack:
                pp_stack.pop()
            continue

        if not _current_preprocessor_active(pp_stack):
            continue

        if directive is not None:
            in_preprocessor = is_preprocessor_continuation_line(line)
            continue

        index = 0
        in_string = False
        in_char = False
        escape = False

        while index < len(line):
            ch = line[index]
            next_ch = line[index + 1] if index + 1 < len(line) else ""

            if raw_terminator is not None:
                end = _find_raw_terminator(line, index, raw_terminator)

                if end is None:
                    break

                index = end
                raw_terminator = None
                continue

            if in_block_comment:
                if ch == "*" and next_ch == "/":
                    index += 2
                    in_block_comment = False
                    continue

                index += 1
                continue

            raw_start = None

            if not in_string and not in_char:
                raw_start = _try_raw_string_start(line, index)

            if raw_start is not None:
                terminator, after_open = raw_start
                raw_terminator = terminator
                index = after_open
                continue

            if escape:
                escape = False
                index += 1
                continue

            if ch == "\\" and (in_string or in_char):
                escape = True
                index += 1
                continue

            if in_string:
                if ch == '"':
                    in_string = False

                index += 1
                continue

            if in_char:
                if ch == "'":
                    in_char = False

                index += 1
                continue

            if ch == '"':
                in_string = True
                index += 1
                continue

            if ch == "'":
                in_char = True
                index += 1
                continue

            if ch == "/" and next_ch == "/":
                break

            if ch == "/" and next_ch == "*":
                index += 2
                in_block_comment = True
                continue

            yield line_no, index, ch
            index += 1
