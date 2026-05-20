from __future__ import annotations

import argparse
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import sys
import traceback
import urllib.parse
import uuid
import os
import re
import socket
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Callable

from cpp_change_tracking import (
    ChangeTracker,
    change_tracking_tool_definitions,
    detect_change_tracking,
)
from cpp_index_lock import (
    IndexFileLock,
    IndexLockError,
    index_http_server_lock,
    index_watcher_lock,
)
from cpp_project_index import LoadedProjectIndex, normalize_jobs
from indexer_control import process_stats
from watch_project_index import (
    SnapshotEntry,
    diff_snapshots,
    snapshot_source_files,
)


SERVER_NAME = "vs-project-indexer"
SERVER_VERSION = "0.1"
WATCH_UPDATE_SUMMARY_NAME = ".watch_update_summary.json"
DEFAULT_PROJECT_ROOT = Path(
    os.environ.get("MCP_CPP_PROJECT_ROOT", Path.cwd())
)

DEFAULT_INDEX_ROOT = Path(
    os.environ.get(
        "MCP_CPP_INDEX_ROOT",
        str(DEFAULT_PROJECT_ROOT / ".mcp-cpp-project-indexer"),
    )
)

def configure_stdio_encoding() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def path_mtime(path: Path) -> float | None:
    try:
        return path.stat().st_mtime
    except OSError:
        return None


def read_lock_owner(path: Path) -> dict[str, str] | None:
    try:
        raw = path.read_bytes()
    except OSError:
        return None

    text = raw[1:].decode("utf-8", errors="replace")
    result: dict[str, str] = {}

    for line in text.splitlines():
        if "=" not in line:
            continue

        key, value = line.split("=", 1)

        if key:
            result[key] = value

    return result or None

# ---------------------------------------------------------------------------
# Small MCP/JSON-RPC stdio server
# ---------------------------------------------------------------------------

class McpError(Exception):
    def __init__(self, code: int, message: str, data: Any | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data


def json_dumps(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


def make_text_result(text: str, *, is_error: bool = False) -> dict[str, Any]:
    return {
        "content": [
            {
                "type": "text",
                "text": text,
            }
        ],
        "isError": is_error,
    }


def make_json_text_result(
    data: Any,
    *,
    is_error: bool = False,
    response_format: str = "pretty",
    omit_nulls: bool = False,
    omit_empty: bool = False,
) -> dict[str, Any]:
    payload = strip_json_values(
        data,
        omit_nulls=omit_nulls,
        omit_empty=omit_empty,
    )
    separators = (",", ":") if response_format == "minified" else None
    return make_text_result(
        json.dumps(
            payload,
            indent=None if response_format == "minified" else 2,
            separators=separators,
            ensure_ascii=False,
        ),
        is_error=is_error,
    )


def strip_json_values(
    value: Any,
    *,
    omit_nulls: bool,
    omit_empty: bool,
) -> Any:
    if isinstance(value, dict):
        result: dict[str, Any] = {}

        for key, item in value.items():
            stripped = strip_json_values(
                item,
                omit_nulls=omit_nulls,
                omit_empty=omit_empty,
            )

            if omit_nulls and stripped is None:
                continue

            if omit_empty and stripped in ({}, []):
                continue

            result[key] = stripped

        return result

    if isinstance(value, list):
        result = [
            strip_json_values(
                item,
                omit_nulls=omit_nulls,
                omit_empty=omit_empty,
            )
            for item in value
        ]

        if omit_empty:
            return [
                item
                for item in result
                if item not in ({}, [])
                and not (omit_nulls and item is None)
            ]

        return result

    return value


def write_message(message: dict[str, Any]) -> None:
    data = json_dumps(message) + "\n"
    sys.stdout.buffer.write(data.encode("utf-8"))
    sys.stdout.buffer.flush()


def read_messages():
    for line in sys.stdin:
        line = line.strip()

        if not line:
            continue

        try:
            yield json.loads(line)
        except json.JSONDecodeError as exc:
            write_message(
                {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {
                        "code": -32700,
                        "message": f"Parse error: {exc}",
                    },
                }
            )


# ---------------------------------------------------------------------------
# Tool schemas
# ---------------------------------------------------------------------------

PACKABLE_TOOL_NAMES = {
    "get_project_summary",
    "list_changed_files",
    "list_recent_revisions",
    "get_revision_summary",
    "get_file_change_hunks",
    "find_symbol",
    "find_declaration",
    "get_nearest_symbol_for_line",
    "list_file_symbols",
    "find_module",
    "list_module_files",
    "find_files",
    "find_symbols_glob",
    "search_modules",
    "get_module_map_summary",
    "get_module_info",
    "list_module_imports",
    "list_module_imported_by",
    "get_module_tree",
    "find_data",
    "list_type_members",
    "get_file_structure",
}

PACKING_SCHEMA_PROPERTIES = {
    "responseFormat": {
        "type": "string",
        "enum": ["pretty", "minified"],
        "default": "pretty",
        "description": "Format JSON tool responses. Minified reduces metadata-token overhead without changing data.",
    },
    "omitNulls": {
        "type": "boolean",
        "default": False,
        "description": "Omit null fields from JSON metadata responses.",
    },
    "omitEmpty": {
        "type": "boolean",
        "default": False,
        "description": "Omit empty arrays/objects from JSON metadata responses.",
    },
}


DATA_COMPACT_FIELDS = {
    "dataId",
    "declarationKind",
    "scopeKind",
    "name",
    "qualifiedName",
    "container",
    "typeText",
    "relativePath",
    "startLine",
    "endLine",
    "signature",
}

MODULE_FILE_COMPACT_FIELDS = {
    "moduleName",
    "fullModuleName",
    "fileId",
    "relativePath",
    "unitKind",
    "lineCount",
    "symbols",
    "diagnostics",
}

MODULE_IMPORT_COMPACT_FIELDS = {
    "kind",
    "module",
    "resolvedModule",
    "isExported",
    "relativePath",
    "startLine",
    "endLine",
}

MODULE_IMPORTED_BY_COMPACT_FIELDS = {
    "module",
    "relativePath",
    "kind",
    "isExported",
    "sourceLine",
}


def compact_dict(item: dict[str, Any], fields: set[str]) -> dict[str, Any]:
    return {
        key: item.get(key)
        for key in fields
        if key in item
    }


def compact_data_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [compact_dict(item, DATA_COMPACT_FIELDS) for item in items]


def compact_module_files(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [compact_dict(item, MODULE_FILE_COMPACT_FIELDS) for item in items]


def compact_module_imports(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [compact_dict(item, MODULE_IMPORT_COMPACT_FIELDS) for item in items]


def compact_module_imported_by(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [compact_dict(item, MODULE_IMPORTED_BY_COMPACT_FIELDS) for item in items]


def compact_module_entry(entry: dict[str, Any]) -> dict[str, Any]:
    return {
        "fullModuleName": entry.get("fullModuleName"),
        "primaryModuleName": entry.get("primaryModuleName"),
        "partitionName": entry.get("partitionName"),
        "files": compact_module_files(entry.get("files", [])),
        "imports": compact_module_imports(entry.get("imports", [])),
        "importedBy": compact_module_imported_by(entry.get("importedBy", [])),
    }


def add_response_packing_options(tools: list[dict[str, Any]]) -> None:
    for tool in tools:
        if tool.get("name") not in PACKABLE_TOOL_NAMES:
            continue

        schema = tool.get("inputSchema")

        if not isinstance(schema, dict):
            continue

        properties = schema.setdefault("properties", {})

        if isinstance(properties, dict):
            properties.update(PACKING_SCHEMA_PROPERTIES)


def tool_definitions() -> list[dict[str, Any]]:
    tools = [
        # Project/cache tools
        {
            "name": "get_project_summary",
            "description": "[Project] Return high-level counts for the loaded C++ routing index. This does not analyze code.",
            "inputSchema": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        },
        {
            "name": "reload_index_cache",
            "description": (
                "[Project] Reload the in-memory project index cache from index files on disk. "
                "This does not rebuild or update the index. "
                "Use only when the user explicitly asks to reload, or after the user says "
                "they rebuilt/updated the index and wants this MCP server to see the new data."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "reason": {
                        "type": "string",
                        "description": (
                            "Short reason for the reload, e.g. "
                            "'User rebuilt the index and asked to reload the MCP cache'."
                        ),
                    },
                },
                "required": ["reason"],
                "additionalProperties": False,
            },
        },

        # Symbol/source navigation tools
        {
            "name": "find_symbol",
            "description": (
                "[Symbol] Find C++ project symbols by name metadata. "
                "Use when the user names a function, method, type, namespace, operator, "
                "constructor, destructor, or declaration-like symbol. "
                "Use this for functions, methods, classes, structs, enums, constructors, "
                "destructors, operators, and namespaces. "
                "The required argument is 'query'. "
                "Searches symbol metadata only: shortName, qualifiedName/search aliases, "
                "and fallback signature substring. "
                "matchKind reports lookup quality: exact_qualified_name/exact_short_name "
                "are strong matches; substring, signature, and metadata matches are weaker routing candidates. "
                "It does not read source code, resolve overloads, analyze behavior, "
                "find references, or build call graphs. "
                "After selecting a result, call read_symbol(symbolId) to read the exact source range."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": (
                            "Symbol name or qualified name. Examples: "
                            "'_OnScroll', 'Editor::_OnScroll', "
                            "'SmartFTP::TextEditor::View::Controls::Editor::_OnScroll', "
                            "'operator=', 'GetHWND'."
                        ),
                    },
                    "name": {
                        "type": "string",
                        "description": (
                            "Compatibility alias for query. Prefer 'query'."
                        ),
                    },
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 500,
                        "default": 20,
                    },
                    "compact": {
                        "type": "boolean",
                        "default": False,
                        "description": "Return only compact routing fields instead of the full symbol metadata.",
                    },
                    "symbolTypes": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional list of symbol type filters, e.g. ['method', 'function', 'type_alias'].",
                    },
                    "container": {
                        "type": "string",
                        "description": "Optional containing class/struct/namespace filter, e.g. 'Editor' or 'Namespace::Editor'.",
                    },
                    "file": {
                        "type": "string",
                        "description": "Optional fileId or project-relative path filter.",
                    },
                    "filePattern": {
                        "type": "string",
                        "description": "Optional glob filter over project-relative file paths, e.g. 'DWrapper/Direct2D/*'.",
                    },
                    "exactOnly": {
                        "type": "boolean",
                        "default": False,
                        "description": "Return only exact short-name or qualified-name matches. Case-insensitive exact matches are included.",
                    },
                    "hideNamespaces": {
                        "type": "boolean",
                        "default": False,
                        "description": "Hide namespace reopening symbols from results.",
                    },
                },
                "additionalProperties": False,
            },
        },
        {
            "name": "find_declaration",
            "description": (
                "[Symbol] Find likely declaration/container symbols for a C++ symbol query. "
                "Use this when the user specifically asks for a declaration. "
                "The required argument is 'query'. "
                "This is still metadata-only and does not read source code. "
                "If multiple overloads are returned, read the candidate signatures/ranges "
                "and disambiguate from context."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": (
                            "Symbol name or qualified name. Examples: "
                            "'OnNotifyReflect', 'Editor::OnNotifyReflect'."
                        ),
                    },
                    "name": {
                        "type": "string",
                        "description": "Compatibility alias for query. Prefer 'query'.",
                    },
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 100,
                        "default": 20,
                    },
                },
                "additionalProperties": False,
            },
        },
        {
            "name": "read_symbol",
            "description": (
                "[Source] Read original source lines for a symbolId, with absolute line numbers. "
                "Use startOffset/endOffset to read only a slice of a large symbol body, "
                "for example startOffset:0,endOffset:20 for the first 21 lines. "
                "This is a read-only range operation."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "symbolId": {
                        "type": "string",
                        "description": "Symbol id returned by find_symbol/list_file_symbols.",
                    },
                    "maxLines": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 2000,
                        "default": 500,
                        "description": "Safety cap. If the symbol range is larger, the output is truncated.",
                    },
                    "startOffset": {
                        "type": "integer",
                        "minimum": 0,
                        "description": "Optional 0-based line offset relative to the symbol start line.",
                    },
                    "endOffset": {
                        "type": "integer",
                        "minimum": 0,
                        "description": "Optional 0-based inclusive line offset relative to the symbol start line.",
                    },
                    "startLine": {
                        "type": "integer",
                        "minimum": 1,
                        "description": "Optional absolute start line clamped to the symbol range.",
                    },
                    "endLine": {
                        "type": "integer",
                        "minimum": 1,
                        "description": "Optional absolute end line clamped to the symbol range.",
                    },
                },
                "required": ["symbolId"],
                "additionalProperties": False,
            },
        },
        {
            "name": "read_range",
            "description": (
                "[Source] Read original source lines from a fileId or project-relative path. "
                "Use startLine/endLine for explicit ranges, or line with beforeLines/afterLines "
                "for compact context around diagnostics, hunks, and search matches."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "file": {
                        "type": "string",
                        "description": "fileId or project-relative path.",
                    },
                    "startLine": {
                        "type": "integer",
                        "minimum": 1,
                    },
                    "endLine": {
                        "type": "integer",
                        "minimum": 1,
                    },
                    "line": {
                        "type": "integer",
                        "minimum": 1,
                        "description": "Optional center line for around-line reads. Cannot be combined with startLine/endLine.",
                    },
                    "beforeLines": {
                        "type": "integer",
                        "minimum": 0,
                        "maximum": 2000,
                        "default": 5,
                        "description": "Number of context lines before line when using around-line mode.",
                    },
                    "afterLines": {
                        "type": "integer",
                        "minimum": 0,
                        "maximum": 2000,
                        "default": 5,
                        "description": "Number of context lines after line when using around-line mode.",
                    },
                    "maxLines": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 2000,
                        "default": 500,
                    },
                },
                "required": ["file"],
                "additionalProperties": False,
            },
        },
        {
            "name": "get_nearest_symbol_for_line",
            "description": (
                "[Symbol] Return indexed symbol/data ranges that contain or are nearest to one file line. "
                "Use when a diagnostic, hunk, build output, Visual Studio location, or IDA note gives "
                "you a file and line number. This is metadata-only and intended for diagnostics, "
                "hunks, build output, and IDE/binary handoff."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "file": {
                        "type": "string",
                        "description": "fileId or project-relative path.",
                    },
                    "line": {
                        "type": "integer",
                        "minimum": 1,
                    },
                    "includeData": {
                        "type": "boolean",
                        "default": True,
                        "description": "Include indexed data/value declarations in addition to symbols.",
                    },
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 50,
                        "default": 10,
                    },
                },
                "required": ["file", "line"],
                "additionalProperties": False,
            },
        },

        # File navigation tools
        {
            "name": "list_file_symbols",
            "description": "[File] List routing symbols for one fileId or project-relative path. Does not read source code.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "file": {
                        "type": "string",
                        "description": "fileId or project-relative path.",
                    },
                    "compact": {
                        "type": "boolean",
                        "default": False,
                        "description": "Return only compact routing fields instead of full symbol metadata.",
                    },
                    "symbolTypes": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional list of symbol type filters, e.g. ['method', 'function', 'type_alias'].",
                    },
                    "container": {
                        "type": "string",
                        "description": "Optional containing class/struct/namespace filter, e.g. 'Editor' or 'Namespace::Editor'.",
                    },
                    "hideNamespaces": {
                        "type": "boolean",
                        "default": False,
                        "description": "Hide namespace reopening symbols from results.",
                    },
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 2000,
                        "default": 500,
                    },
                },
                "required": ["file"],
                "additionalProperties": False,
            },
        },

        # Module metadata tools
        {
            "name": "find_module",
            "description": (
                "[Module] Find files that define a C++20 module or module partition. "
                "Use when the user gives a C++20 module name and asks where it is defined. "
                "This is metadata-only; do not pass C++ namespaces with '::'."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "moduleName": {
                        "type": "string",
                        "description": "Full module name, e.g. uiframework.Elements:ElementImpl.",
                    },
                    "compact": {
                        "type": "boolean",
                        "default": False,
                        "description": "Return compact module/file routing fields only.",
                    },
                },
                "required": ["moduleName"],
                "additionalProperties": False,
            },
        },
        {
            "name": "list_module_files",
            "description": (
                "[Module] Return files that define a C++20 module or module partition. "
                "The input must be a module name using C++20 module syntax, e.g. "
                "'SmartFTP.TextEditor:View.Controls.Editor'. "
                "Do not pass C++ namespaces such as 'SmartFTP::TextEditor::View::Controls'. "
                "For namespaces/classes/functions, use find_symbol."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "moduleName": {
                        "type": "string",
                    },
                    "compact": {
                        "type": "boolean",
                        "default": False,
                        "description": "Return compact module/file routing fields only.",
                    },
                },
                "required": ["moduleName"],
                "additionalProperties": False,
            },
        },

        # File/symbol glob discovery tools
        {
            "name": "find_files",
            "description": (
                "[File] Find indexed files by glob pattern over project-relative paths. "
                "Use this when you know a filename or path pattern. "
                "This does not search source contents."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "Glob pattern, e.g. '*Editor*', '*/TextEditor/*.ixx'."
                    },
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 500,
                        "default": 100
                    }
                },
                "required": ["pattern"],
                "additionalProperties": False
            }
        },
        {
            "name": "find_symbols_glob",
            "description": (
                "[Symbol] Find symbols by glob pattern over shortName, qualifiedName, container, "
                "signature, and relativePath. This searches index metadata only, not source code."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "Glob pattern, e.g. '*OnNotify*', 'SmartFTP::*::Editor::*'."
                    },
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 500,
                        "default": 100
                    }
                },
                "required": ["pattern"],
                "additionalProperties": False
            }
        },

        # Module map/query tools
        {
            "name": "search_modules",
            "description": (
                "[Module] Find C++20 modules by glob pattern over module names. "
                "Use C++20 module syntax, e.g. '*.TextEditor:*'. "
                "Do not pass C++ namespaces with '::'."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "Glob pattern over module names, e.g. '*.TextEditor:*'."
                    },
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 500,
                        "default": 100
                    }
                },
                "required": ["pattern"],
                "additionalProperties": False
            }
        },
        {
            "name": "get_module_map_summary",
            "description": "[Module] Return summary counts for module_map.json. Metadata only; no source code is read.",
            "inputSchema": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        },
        {
            "name": "get_module_info",
            "description": (
                "[Module] Return module metadata for one exact C++20 module name, including files, "
                "direct imports, re-exports, and modules that directly import it. "
                "Use for module structure questions. Metadata only; do not pass C++ namespaces with '::'."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "moduleName": {
                        "type": "string",
                        "description": "Exact C++20 module name, e.g. SmartFTP.Shell.Browser:Impl.",
                    },
                    "compact": {
                        "type": "boolean",
                        "default": False,
                        "description": "Return compact files/imports/importedBy routing fields only.",
                    },
                },
                "required": ["moduleName"],
                "additionalProperties": False,
            },
        },
        {
            "name": "list_module_imports",
            "description": (
                "[Module] List direct outgoing imports of one exact C++20 module. "
                "Use for questions like 'What does module A import?'. Metadata only."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "moduleName": {
                        "type": "string",
                        "description": "Exact C++20 module name.",
                    },
                    "compact": {
                        "type": "boolean",
                        "default": False,
                        "description": "Return compact import routing fields only.",
                    },
                },
                "required": ["moduleName"],
                "additionalProperties": False,
            },
        },
        {
            "name": "list_module_imported_by",
            "description": (
                "[Module] List modules that directly import one exact C++20 module. "
                "Use for reverse questions like 'Who imports module B?'. Metadata only."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "moduleName": {
                        "type": "string",
                        "description": "Exact C++20 module name.",
                    },
                    "compact": {
                        "type": "boolean",
                        "default": False,
                        "description": "Return compact importing-module routing fields only.",
                    },
                },
                "required": ["moduleName"],
                "additionalProperties": False,
            },
        },
        {
            "name": "get_module_tree",
            "description": "[Module] Return a bounded C++20 module name tree from module_map.json. Metadata only.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "maxDepth": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 20,
                        "default": 4,
                    }
                },
                "additionalProperties": False,
            },
        },

        # Data/member tools
        {
            "name": "find_data",
            "description": (
                "[Data] Find indexed C++ data/value declarations by metadata. "
                "Use this for class/struct fields, static data members, globals, "
                "namespace constants, enum values, variable templates, and concepts. "
                "This is metadata-only and does not resolve types. "
                "typeText is source text, not resolved type information; use it only as "
                "a routing hint for further symbol/source lookup. "
                "After selecting a result, call read_data(dataId) to read the exact declaration range."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Data/member name or qualified name, e.g. '_ScrollBars', 'Widget::_state'.",
                    },
                    "name": {
                        "type": "string",
                        "description": "Compatibility alias for query. Prefer 'query'.",
                    },
                    "container": {
                        "type": "string",
                        "description": "Optional containing class/struct/namespace to narrow the search.",
                    },
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 500,
                        "default": 20,
                    },
                    "compact": {
                        "type": "boolean",
                        "default": False,
                        "description": "Return compact data routing fields only.",
                    },
                },
                "additionalProperties": False,
            },
        },
        {
            "name": "list_type_members",
            "description": (
                "[Data] List indexed data/value declarations directly contained by a class, struct, or namespace. "
                "Use this to inspect member fields/constants after reading a method body. "
                "Returns metadata only: name, typeText, signature and source range. "
                "typeText is source text, not resolved type information."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "container": {
                        "type": "string",
                        "description": "Containing type or namespace, e.g. 'Example::Widget' or just 'Widget'.",
                    },
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 1000,
                        "default": 500,
                    },
                    "compact": {
                        "type": "boolean",
                        "default": False,
                        "description": "Return compact data routing fields only.",
                    },
                },
                "required": ["container"],
                "additionalProperties": False,
            },
        },
        {
            "name": "read_data",
            "description": (
                "[Data] Read original source lines for an indexed data/value declaration by dataId. "
                "This is a read-only range operation."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "dataId": {
                        "type": "string",
                        "description": "Data declaration id returned by find_data/list_type_members.",
                    }
                },
                "required": ["dataId"],
                "additionalProperties": False,
            },
        },

        # Comment extraction tools
        {
            "name": "get_symbol_leading_comment",
            "description": (
                "[Comment] Extract the exact leading comment range immediately before an indexed symbol. "
                "This reads the original source file on demand and does not use a comment index. "
                "read_symbol remains clean and returns only the exact symbol range."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "symbolId": {
                        "type": "string",
                        "description": "Symbol id returned by find_symbol/list_file_symbols.",
                    },
                    "maxLines": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 200,
                        "default": 20,
                    },
                    "allowBlankGap": {
                        "type": "boolean",
                        "default": True,
                        "description": "Allow a small blank-line gap between the comment block and the symbol.",
                    },
                },
                "required": ["symbolId"],
                "additionalProperties": False,
            },
        },
        {
            "name": "get_data_leading_comment",
            "description": (
                "[Comment] Extract the exact leading comment range immediately before an indexed data/value declaration. "
                "Use this for fields, globals, enum values, variable templates, and concepts."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "dataId": {
                        "type": "string",
                        "description": "Data declaration id returned by find_data/list_type_members.",
                    },
                    "maxLines": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 200,
                        "default": 20,
                    },
                    "allowBlankGap": {
                        "type": "boolean",
                        "default": True,
                    },
                },
                "required": ["dataId"],
                "additionalProperties": False,
            },
        },
        {
            "name": "get_file_header_comment",
            "description": (
                "[Comment] Extract the initial file header comment from a file. "
                "This only inspects the start of the file and stops at the first code line."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "file": {
                        "type": "string",
                        "description": "fileId or project-relative path.",
                    },
                    "maxLines": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 500,
                        "default": 120,
                    },
                },
                "required": ["file"],
                "additionalProperties": False,
            },
        },
        {
            "name": "get_module_header_comment",
            "description": (
                "[Comment] Extract file-header comments from files that define a C++20 module or module partition. "
                "Returns one result per module file and does not guess a single canonical file when several exist."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "moduleName": {
                        "type": "string",
                        "description": "C++20 module name, e.g. Example.Module:Partition.",
                    },
                    "maxLines": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 500,
                        "default": 120,
                    },
                },
                "required": ["moduleName"],
                "additionalProperties": False,
            },
        },

        # File overview/search tools
        {
            "name": "get_file_structure",
            "description": (
                "[File] Return a structured overview of one indexed source file using index metadata only. "
                "Use for first-pass orientation in large files, with includeOutline:false when counts/sections are enough. "
                "This includes module metadata, symbol counts, data declaration counts, diagnostics, "
                "section ranges, and an ordered outline. This does not analyze code semantics."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "file": {
                        "type": "string",
                        "description": "fileId or project-relative path.",
                    },
                    "includeOutline": {
                        "type": "boolean",
                        "default": True,
                        "description": "Include ordered symbol/data outline items.",
                    },
                    "outlineLimit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 5000,
                        "default": 500,
                        "description": "Maximum number of outline items to return.",
                    },
                    "compactOutline": {
                        "type": "boolean",
                        "default": True,
                        "description": "Return compact outline items with routing fields only.",
                    },
                    "symbolTypes": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional symbol type filters for counts/outline, e.g. ['method', 'function', 'class'].",
                    },
                    "dataKinds": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional data declaration kind filters, e.g. ['field', 'global_variable', 'enumerator'].",
                    },
                    "includeData": {
                        "type": "boolean",
                        "default": True,
                        "description": "Include indexed data declarations in counts/sections/outline.",
                    },
                    "includeDiagnostics": {
                        "type": "boolean",
                        "default": True,
                        "description": "Include file diagnostics in the result.",
                    },
                    "hideNamespaces": {
                        "type": "boolean",
                        "default": False,
                        "description": "Hide namespace reopening symbols from counts/outline.",
                    },
                    "includeIndexerDiagnostics": {
                        "type": "boolean",
                        "default": False,
                        "description": "Include optional indexer/scanner diagnostic sections when built with --emit-diagnostic-file-indexes.",
                    },
                    "diagnosticKinds": {
                        "type": "array",
                        "items": {
                            "type": "string",
                            "enum": [
                                "diagnostics",
                                "structuralEvents",
                                "scopeIntervals",
                                "functionBodyRanges",
                            ],
                        },
                        "description": "Optional indexer/scanner diagnostic section filters.",
                    },
                    "diagnosticStartLine": {
                        "type": "integer",
                        "minimum": 1,
                        "description": "Optional start line for indexer/scanner diagnostic section filtering.",
                    },
                    "diagnosticEndLine": {
                        "type": "integer",
                        "minimum": 1,
                        "description": "Optional end line for indexer/scanner diagnostic section filtering.",
                    },
                    "diagnosticLimit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 5000,
                        "default": 200,
                    },
                    "compactDiagnostics": {
                        "type": "boolean",
                        "default": True,
                        "description": "Return compact indexer/scanner diagnostic items with routing fields only.",
                    },
                    "includeDebug": {
                        "type": "boolean",
                        "default": False,
                        "description": "Compatibility alias for includeIndexerDiagnostics. Prefer includeIndexerDiagnostics.",
                    },
                    "debugKinds": {
                        "type": "array",
                        "items": {
                            "type": "string",
                            "enum": [
                                "diagnostics",
                                "structuralEvents",
                                "scopeIntervals",
                                "functionBodyRanges",
                            ],
                        },
                        "description": "Compatibility alias for diagnosticKinds. Prefer diagnosticKinds.",
                    },
                    "debugStartLine": {
                        "type": "integer",
                        "minimum": 1,
                        "description": "Compatibility alias for diagnosticStartLine. Prefer diagnosticStartLine.",
                    },
                    "debugEndLine": {
                        "type": "integer",
                        "minimum": 1,
                        "description": "Compatibility alias for diagnosticEndLine. Prefer diagnosticEndLine.",
                    },
                    "debugLimit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 5000,
                        "default": 200,
                        "description": "Compatibility alias for diagnosticLimit. Prefer diagnosticLimit.",
                    },
                    "compactDebug": {
                        "type": "boolean",
                        "default": True,
                        "description": "Compatibility alias for compactDiagnostics. Prefer compactDiagnostics.",
                    },
                },
                "required": ["file"],
                "additionalProperties": False,
            },
        },
        {
            "name": "search_source",
            "description": (
                "[Search] Search raw source text in indexed files. This is a plain line-based text search, "
                "not semantic C++ reference resolution. It searches comments and strings too. "
                "Use when metadata lookup is not enough and you need lexical source-text occurrences. "
                "Use symbolId to search only inside one already-located symbol body; prefer symbolId, "
                "filePattern, or file to narrow broad queries."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Literal source text to search for, e.g. 'TMT_ATLASRECT' or 'g_AtlasCache'.",
                    },
                    "file": {
                        "type": "string",
                        "description": "Optional fileId or project-relative path to search in one file.",
                    },
                    "filePattern": {
                        "type": "string",
                        "description": "Optional glob pattern over project-relative paths, e.g. 'Shared/Windows/UXTheme/*'.",
                    },
                    "symbolId": {
                        "type": "string",
                        "description": "Optional symbol id to search only inside that symbol's source range.",
                    },
                    "caseSensitive": {
                        "type": "boolean",
                        "default": False,
                    },
                    "wholeWord": {
                        "type": "boolean",
                        "default": False,
                        "description": (
                            "Match only when the query is not adjacent to C/C++ identifier characters "
                            "[A-Za-z0-9_]. This is lexical text matching, not semantic identifier resolution."
                        ),
                    },
                    "useRegex": {
                        "type": "boolean",
                        "default": False,
                        "description": (
                            "Treat query as a Python regular expression. This is still raw source "
                            "text search, not semantic C++ reference resolution."
                        ),
                    },
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 1000,
                        "default": 100,
                    },
                    "contextLines": {
                        "type": "integer",
                        "minimum": 0,
                        "maximum": 20,
                        "default": 0,
                    },
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        },
    ]
    add_response_packing_options(tools)
    return tools


def _trim_tree(node: dict[str, Any], *, max_depth: int, depth: int = 0) -> dict[str, Any]:
    result = {
        "name": node.get("name", ""),
        "fullName": node.get("fullName", ""),
        "modules": node.get("modules", []),
    }

    if depth >= max_depth:
        result["childrenTruncated"] = len(node.get("children", []))
        return result

    result["children"] = [
        _trim_tree(child, max_depth=max_depth, depth=depth + 1)
        for child in node.get("children", [])
    ]

    return result


# ---------------------------------------------------------------------------
# Tool implementation
# ---------------------------------------------------------------------------

class ServerIndexWatcher:
    def __init__(
        self,
        *,
        tools: "CodeIndexTools",
        poll_interval: float,
        debounce: float,
        jobs: int,
        module_map: bool,
        emit_debug_file_indexes: bool,
        include_extensionless_headers: bool,
        use_git_ignore: bool,
    ) -> None:
        self.tools = tools
        self.poll_interval = max(0.1, poll_interval)
        self.debounce = max(0.1, debounce)
        self.jobs = jobs
        self.module_map = module_map
        self.emit_debug_file_indexes = emit_debug_file_indexes
        self.include_extensionless_headers = include_extensionless_headers
        self.use_git_ignore = use_git_ignore
        self.indexer_root = Path(__file__).resolve().parent
        self.watcher_lock: IndexFileLock | None = None
        self.status_lock = threading.Lock()
        self.started_at: str | None = None
        self.running = False
        self.lock_held = False
        self.last_scan_at: str | None = None
        self.last_change_at: str | None = None
        self.last_update_at: str | None = None
        self.last_update_result: str | None = None
        self.last_error: str | None = None
        self.last_added = 0
        self.last_modified = 0
        self.last_deleted = 0
        self.stop_event = threading.Event()
        self.thread = threading.Thread(
            target=self._run,
            name="mcp-cpp-project-indexer-watch",
            daemon=True,
        )

    def start(self) -> None:
        try:
            self.watcher_lock = index_watcher_lock(self.tools.index_root)
            self.watcher_lock.acquire()
            with self.status_lock:
                self.lock_held = True
        except IndexLockError as exc:
            self.watcher_lock = None
            with self.status_lock:
                self.running = False
                self.lock_held = False
                self.last_update_result = "watcher_lock_unavailable"
                self.last_error = str(exc)
            print(
                (
                    "[mcp-cpp-project-indexer] index watcher not started: "
                    f"{exc}. Another watcher is already active for this index root. "
                    "This MCP server will continue read-only without watcher updates."
                ),
                file=sys.stderr,
                flush=True,
            )
            return

        print(
            (
                "[mcp-cpp-project-indexer] starting index watcher "
                f"poll={self.poll_interval:.2f}s debounce={self.debounce:.2f}s "
                f"jobs={normalize_jobs(self.jobs)} module_map={self.module_map} "
                f"diagnostics={self.emit_debug_file_indexes}"
            ),
            file=sys.stderr,
            flush=True,
        )
        with self.status_lock:
            self.started_at = now_iso()
            self.running = True
            self.last_update_result = "watching"
            self.last_error = None
        self.thread.start()

    def stop(self, *, timeout: float = 5.0) -> None:
        self.stop_event.set()
        if self.thread.is_alive():
            self.thread.join(timeout=timeout)
        if self.watcher_lock is not None and not self.thread.is_alive():
            self.watcher_lock.release()
            self.watcher_lock = None
        with self.status_lock:
            self.running = False
            if self.watcher_lock is None:
                self.lock_held = False

    def status(self) -> dict[str, Any]:
        with self.status_lock:
            return {
                "configured": True,
                "running": self.running and self.thread.is_alive(),
                "lockHeld": self.lock_held,
                "startedAt": self.started_at,
                "pollIntervalSeconds": self.poll_interval,
                "debounceSeconds": self.debounce,
                "jobs": normalize_jobs(self.jobs),
                "moduleMap": self.module_map,
                "diagnosticFileIndexes": self.emit_debug_file_indexes,
                "includeExtensionlessHeaders": self.include_extensionless_headers,
                "useGitIgnore": self.use_git_ignore,
                "lastScanAt": self.last_scan_at,
                "lastChangeAt": self.last_change_at,
                "lastUpdateAt": self.last_update_at,
                "lastUpdateResult": self.last_update_result,
                "lastError": self.last_error,
                "lastAdded": self.last_added,
                "lastModified": self.last_modified,
                "lastDeleted": self.last_deleted,
            }

    def _snapshot(self) -> dict[str, SnapshotEntry]:
        return snapshot_source_files(
            root=self.tools.project_root,
            extensions=None,
            excluded_dir_names=None,
            include_extensionless_headers=self.include_extensionless_headers,
            use_git_ignore=self.use_git_ignore,
            case_insensitive_paths=True,
        )

    @staticmethod
    def _summary_has_index_changes(summary_path: Path) -> bool:
        try:
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return True

        if summary.get("structuralUnchanged") is True:
            return False

        return any(
            int(summary.get(key) or 0) > 0
            for key in ("added", "modified", "deleted")
        )

    def _run_update(self, *, known_files_only: bool, changed_files: list[Path]) -> tuple[int, bool]:
        summary_path = self.tools.index_root / WATCH_UPDATE_SUMMARY_NAME
        update_args = [
            sys.executable,
            str(self.indexer_root / "update_project_index.py"),
            "--root",
            str(self.tools.project_root),
            "--index-root",
            str(self.tools.index_root),
            "--jobs",
            str(self.jobs),
            "--summary-json-file",
            str(summary_path),
        ]

        if known_files_only:
            update_args.append("--known-files-only")

            for path in changed_files:
                try:
                    relative = path.relative_to(self.tools.project_root)
                except ValueError:
                    relative = path

                update_args.extend(["--changed-file", relative.as_posix()])

        if self.emit_debug_file_indexes:
            update_args.append("--emit-diagnostic-file-indexes")

        if self.include_extensionless_headers:
            update_args.append("--include-extensionless-headers")

        if not self.use_git_ignore:
            update_args.append("--no-git-ignore")

        print(
            "[mcp-cpp-project-indexer] watcher update: "
            + " ".join(update_args),
            file=sys.stderr,
            flush=True,
        )
        completed = subprocess.run(
            update_args,
            check=False,
            stdout=sys.stderr,
            stderr=sys.stderr,
        )

        if completed.returncode != 0:
            return completed.returncode, False

        if not self._summary_has_index_changes(summary_path):
            print(
                "[mcp-cpp-project-indexer] watcher no index changes after content-hash check",
                file=sys.stderr,
                flush=True,
            )
            return 0, False

        if not self.module_map:
            return 0, True

        module_args = [
            sys.executable,
            str(self.indexer_root / "build_module_map.py"),
            "--index-root",
            str(self.tools.index_root),
        ]
        print(
            "[mcp-cpp-project-indexer] watcher module map: "
            + " ".join(module_args),
            file=sys.stderr,
            flush=True,
        )
        module_result = subprocess.run(
            module_args,
            check=False,
            stdout=sys.stderr,
            stderr=sys.stderr,
        ).returncode
        return module_result, module_result == 0

    def _run(self) -> None:
        try:
            snapshot = self._snapshot()
            print(
                f"[mcp-cpp-project-indexer] watcher initial files: {len(snapshot)}",
                file=sys.stderr,
                flush=True,
            )

            while not self.stop_event.wait(self.poll_interval):
                current = self._snapshot()
                with self.status_lock:
                    self.last_scan_at = now_iso()
                diff = diff_snapshots(snapshot, current, root=self.tools.project_root)

                if not diff.changed:
                    continue

                pending_since = time.monotonic()
                pending_snapshot = current
                pending_diff = diff

                while not self.stop_event.wait(self.poll_interval):
                    current = self._snapshot()
                    next_diff = diff_snapshots(
                        pending_snapshot,
                        current,
                        root=self.tools.project_root,
                    )

                    if next_diff.changed:
                        pending_since = time.monotonic()
                        pending_snapshot = current
                        pending_diff = diff_snapshots(
                            snapshot,
                            current,
                            root=self.tools.project_root,
                        )

                    if time.monotonic() - pending_since >= self.debounce:
                        break

                if self.stop_event.is_set():
                    break

                print(
                    (
                        "[mcp-cpp-project-indexer] watcher changes "
                        f"added={len(pending_diff.added)} "
                        f"modified={len(pending_diff.modified)} "
                        f"deleted={len(pending_diff.deleted)}"
                    ),
                    file=sys.stderr,
                    flush=True,
                )
                with self.status_lock:
                    self.last_change_at = now_iso()
                    self.last_added = len(pending_diff.added)
                    self.last_modified = len(pending_diff.modified)
                    self.last_deleted = len(pending_diff.deleted)
                    self.last_update_result = "updating"
                result, index_changed = self._run_update(
                    known_files_only=not pending_diff.requires_full_discovery_update,
                    changed_files=pending_diff.modified,
                )

                if result != 0:
                    with self.status_lock:
                        self.last_update_at = now_iso()
                        self.last_update_result = "failed"
                        self.last_error = f"update exit code {result}"
                    print(
                        f"[mcp-cpp-project-indexer] watcher update failed: {result}",
                        file=sys.stderr,
                        flush=True,
                    )
                    continue

                snapshot = pending_snapshot
                with self.status_lock:
                    self.last_update_at = now_iso()
                    self.last_update_result = (
                        "updated" if index_changed else "no_index_changes"
                    )
                    self.last_error = None
                if index_changed:
                    self.tools.reload_index_cache_from_disk(
                        reason="Server index watcher updated the index on disk."
                    )
                    print(
                        "[mcp-cpp-project-indexer] watcher reloaded MCP cache",
                        file=sys.stderr,
                        flush=True,
                    )
        except Exception:  # noqa: BLE001 - watcher must not take down MCP server.
            with self.status_lock:
                self.running = False
                self.last_update_result = "exception"
                self.last_error = traceback.format_exc()
            print(
                "[mcp-cpp-project-indexer] watcher stopped after exception:",
                file=sys.stderr,
                flush=True,
            )
            traceback.print_exc(file=sys.stderr)
        finally:
            if self.watcher_lock is not None:
                self.watcher_lock.release()
                self.watcher_lock = None
            with self.status_lock:
                self.running = False
                self.lock_held = False


class CodeIndexTools:
    def __init__(self, *, project_root: Path, index_root: Path) -> None:
        self.project_root = project_root
        self.index_root = index_root
        self.index = LoadedProjectIndex(index_root)
        self.module_map_path = index_root / "module_map.json"
        self.module_map: dict[str, Any] | None = None
        self.index_lock = threading.RLock()
        self.watcher: ServerIndexWatcher | None = None
        self.change_tracking_availability = detect_change_tracking(project_root)
        self.change_tracker: ChangeTracker | None = None

        if self.change_tracking_availability.available:
            self.change_tracker = ChangeTracker(
                project_root=project_root,
                index=self.index,
                availability=self.change_tracking_availability,
            )

        self._load_module_map()

    def json_result(
        self,
        arguments: dict[str, Any],
        data: Any,
        *,
        is_error: bool = False,
    ) -> dict[str, Any]:
        return make_json_text_result(
            data,
            is_error=is_error,
            **json_response_options(arguments),
        )

    def start_index_watcher(
        self,
        *,
        poll_interval: float,
        debounce: float,
        jobs: int,
        module_map: bool,
        emit_debug_file_indexes: bool,
        include_extensionless_headers: bool,
        use_git_ignore: bool,
    ) -> None:
        if self.watcher is not None:
            return

        self.watcher = ServerIndexWatcher(
            tools=self,
            poll_interval=poll_interval,
            debounce=debounce,
            jobs=jobs,
            module_map=module_map,
            emit_debug_file_indexes=emit_debug_file_indexes,
            include_extensionless_headers=include_extensionless_headers,
            use_git_ignore=use_git_ignore,
        )
        self.watcher.start()

    def stop_index_watcher(self) -> None:
        if self.watcher is None:
            return

        self.watcher.stop()
        self.watcher = None

    def _load_module_map(self) -> None:
        self.module_map = None

        if self.module_map_path.exists():
            self.module_map = json.loads(
                self.module_map_path.read_text(encoding="utf-8")
            )

    def require_module_map(self) -> dict[str, Any]:
        if self.module_map is None:
            raise McpError(
                -32001,
                (
                    "module_map.json not found. Build it first with: "
                    f"python build_module_map.py --index-root {self.index_root}"
                ),
            )

        return self.module_map

    def get_project_summary(self, arguments: dict[str, Any]) -> dict[str, Any]:
        counts = self.index.manifest.get("counts", {})
        return self.json_result(
            arguments,
            {
                "schema": self.index.manifest.get("schema"),
                "projectRoot": self.project_root.as_posix(),
                "indexRoot": self.index_root.as_posix(),
                "counts": counts,
            }
        )

    def status_snapshot(self) -> dict[str, Any]:
        manifest_path = self.index_root / "manifest.json"
        module_map_path = self.index_root / "module_map.json"
        diagnostics_path = self.index_root / "diagnostics.json"
        update_lock_path = self.index_root / ".update.lock"
        watcher_lock_path = self.index_root / ".watcher.lock"

        with self.index_lock:
            manifest = self.index.manifest
            counts = dict(manifest.get("counts", {}))
            stats = dict(manifest.get("stats", {}))
            watcher_status = (
                self.watcher.status()
                if self.watcher is not None
                else {"configured": False, "running": False, "lockHeld": False}
            )

        return {
            "project": {
                "root": self.project_root.as_posix(),
                "indexRoot": self.index_root.as_posix(),
            },
            "index": {
                "schema": manifest.get("schema"),
                "root": manifest.get("root"),
                "counts": counts,
                "stats": stats,
                "manifestMtime": path_mtime(manifest_path),
                "moduleMapMtime": path_mtime(module_map_path),
                "diagnosticsMtime": path_mtime(diagnostics_path),
            },
            "watcher": watcher_status,
            "locks": {
                "updateLockFileExists": update_lock_path.exists(),
                "updateLockOwner": read_lock_owner(update_lock_path),
                "watcherLockFileExists": watcher_lock_path.exists(),
                "watcherLockOwner": read_lock_owner(watcher_lock_path),
            },
            "changeTracking": {
                "available": self.change_tracking_availability.available,
                "reason": self.change_tracking_availability.reason,
            },
            "moduleMap": {
                "loaded": self.module_map is not None,
                "path": module_map_path.as_posix(),
            },
        }

    def reload_index_cache_from_disk(self, *, reason: str) -> dict[str, Any]:
        manifest_path = self.index_root / "manifest.json"

        with self.index_lock:
            before_counts = dict(self.index.manifest.get("counts", {}))
            before_root = self.index.manifest.get("root")
            before_manifest_mtime = (
                manifest_path.stat().st_mtime
                if manifest_path.exists()
                else None
            )
            before_module_map_loaded = self.module_map is not None

            self.index = LoadedProjectIndex(self.index_root)
            self._load_module_map()

            if self.change_tracker is not None:
                self.change_tracker.index = self.index

            after_counts = dict(self.index.manifest.get("counts", {}))
            after_root = self.index.manifest.get("root")
            after_manifest_mtime = (
                manifest_path.stat().st_mtime
                if manifest_path.exists()
                else None
            )

            return {
                "reloaded": True,
                "reason": reason,
                "indexRoot": self.index_root.as_posix(),
                "projectRoot": self.project_root.as_posix(),
                "manifestRootBefore": before_root,
                "manifestRootAfter": after_root,
                "manifestMtimeBefore": before_manifest_mtime,
                "manifestMtimeAfter": after_manifest_mtime,
                "countsBefore": before_counts,
                "countsAfter": after_counts,
                "moduleMapLoadedBefore": before_module_map_loaded,
                "moduleMapLoadedAfter": self.module_map is not None,
            }

    def reload_index_cache(self, arguments: dict[str, Any]) -> dict[str, Any]:
        reason = require_string(arguments, "reason")
        return make_json_text_result(
            self.reload_index_cache_from_disk(reason=reason)
        )

    def require_change_tracker(self) -> ChangeTracker:
        if self.change_tracker is None:
            raise McpError(-32601, "Change tracking tools are not available for this project.")

        return self.change_tracker

    def list_changed_files(self, arguments: dict[str, Any]) -> dict[str, Any]:
        scope = optional_enum(arguments, "scope", {"working", "staged", "all"}, "all")
        include_untracked = optional_bool(arguments, "includeUntracked", True)
        file_pattern = optional_string(arguments, "filePattern")
        compact = optional_bool(arguments, "compact", True)
        limit = clamp_int(arguments.get("limit", 100), minimum=1, maximum=1000)
        return self.json_result(
            arguments,
            self.require_change_tracker().list_changed_files(
                scope=scope,
                include_untracked=include_untracked,
                file_pattern=file_pattern,
                compact=compact,
                limit=limit,
            )
        )

    def list_recent_revisions(self, arguments: dict[str, Any]) -> dict[str, Any]:
        limit = clamp_int(arguments.get("limit", 10), minimum=1, maximum=100)
        compact = optional_bool(arguments, "compact", True)
        return self.json_result(
            arguments,
            self.require_change_tracker().list_recent_revisions(
                limit=limit,
                compact=compact,
            )
        )

    def get_revision_summary(self, arguments: dict[str, Any]) -> dict[str, Any]:
        revision = require_string(arguments, "revision")
        compact = optional_bool(arguments, "compact", True)
        include_message = optional_bool(arguments, "includeMessage", True)
        include_files = optional_bool(arguments, "includeFiles", True)
        file_pattern = optional_string(arguments, "filePattern")
        limit = clamp_int(arguments.get("limit", 100), minimum=1, maximum=1000)
        return self.json_result(
            arguments,
            self.require_change_tracker().get_revision_summary(
                revision=revision,
                compact=compact,
                include_message=include_message,
                include_files=include_files,
                file_pattern=file_pattern,
                limit=limit,
            )
        )

    def get_file_change_hunks(self, arguments: dict[str, Any]) -> dict[str, Any]:
        file = require_string(arguments, "file")
        revision = optional_string(arguments, "revision")
        scope_present = "scope" in arguments
        scope = optional_enum(arguments, "scope", {"working", "staged", "all"}, "all")

        if revision is not None and scope_present:
            raise McpError(-32602, "revision and scope must not both be set")

        symbol_id = optional_string(arguments, "symbolId")
        data_id = optional_string(arguments, "dataId")

        if symbol_id is not None and data_id is not None:
            raise McpError(-32602, "symbolId and dataId must not both be set")

        file_item = self.index.get_file_item(file)

        if symbol_id is not None:
            symbol = self.index.symbol_by_id.get(symbol_id)

            if symbol is None:
                return make_text_result(f"Symbol not found: {symbol_id}", is_error=True)

            if file_item is not None and symbol.get("fileId") != file_item.get("fileId"):
                raise McpError(-32602, "symbolId does not belong to file")

        if data_id is not None:
            item = self.index.data_by_id.get(data_id)

            if item is None:
                return make_text_result(f"Data declaration not found: {data_id}", is_error=True)

            if file_item is not None and item.get("fileId") != file_item.get("fileId"):
                raise McpError(-32602, "dataId does not belong to file")

        context_lines = clamp_int(arguments.get("contextLines", 1), minimum=0, maximum=20)
        include_source = optional_bool(arguments, "includeSource", True)
        include_indexed_ranges = optional_bool(arguments, "includeIndexedRanges", True)
        include_indexed_range_summary = optional_bool(arguments, "includeIndexedRangeSummary", False)
        indexed_range_summary_limit = clamp_int(
            arguments.get("indexedRangeSummaryLimit", 200),
            minimum=1,
            maximum=1000,
        )
        max_hunks = clamp_int(arguments.get("maxHunks", 20), minimum=1, maximum=200)
        max_lines = clamp_int(arguments.get("maxLines", 500), minimum=1, maximum=5000)
        return self.json_result(
            arguments,
            self.require_change_tracker().get_file_change_hunks(
                file=file,
                scope=scope,
                revision=revision,
                symbol_id=symbol_id,
                data_id=data_id,
                context_lines=context_lines,
                include_source=include_source,
                include_indexed_ranges=include_indexed_ranges,
                include_indexed_range_summary=include_indexed_range_summary,
                indexed_range_summary_limit=indexed_range_summary_limit,
                max_hunks=max_hunks,
                max_lines=max_lines,
            )
        )

    def find_symbol(self, arguments: dict[str, Any]) -> dict[str, Any]:
        query = require_query(arguments)
        limit = clamp_int(arguments.get("limit", 20), minimum=1, maximum=500)
        compact = optional_bool(arguments, "compact", False)
        exact_only = optional_bool(arguments, "exactOnly", False)
        hide_namespaces = optional_bool(arguments, "hideNamespaces", False)
        symbol_types = optional_string_set(arguments, "symbolTypes")
        container = optional_string(arguments, "container")
        file = optional_string(arguments, "file")
        file_pattern = optional_string(arguments, "filePattern")

        if file is not None and file_pattern is not None:
            raise McpError(-32602, "file cannot be combined with filePattern")

        results = self.index.find_symbol(
            query,
            limit=limit,
            symbol_types=symbol_types,
            container=container,
            file=file,
            file_pattern=file_pattern,
            exact_only=exact_only,
            hide_namespaces=hide_namespaces,
            compact=compact,
        )
        return self.json_result(arguments, results)

    def find_declaration(self, arguments: dict[str, Any]) -> dict[str, Any]:
        query = require_query(arguments)
        limit = clamp_int(arguments.get("limit", 20), minimum=1, maximum=100)
        candidates = self.index.find_symbol(query, limit=limit * 3)

        declaration_rank = {
            "class": 0,
            "struct": 0,
            "enum": 0,
            "namespace": 1,
            "class_declaration": 1,
            "struct_declaration": 1,
            "constructor_declaration": 1,
            "destructor_declaration": 1,
            "operator_declaration": 1,
            "method_declaration": 1,
            "function_declaration": 1,
            "type_alias": 1,
            "type_alias_template": 1,
            "typedef_declaration": 1,
            "constructor": 2,
            "destructor": 2,
            "operator": 2,
            "method": 2,
            "function": 2,
        }

        candidates.sort(
            key=lambda item: (
                declaration_rank.get(str(item.get("type")), 99),
                str(item.get("qualifiedName") or item.get("shortName") or ""),
                str(item.get("relativePath") or ""),
                int(item.get("startLine") or 0),
            )
        )
        return self.json_result(arguments, candidates[:limit])

    def read_symbol(self, arguments: dict[str, Any]) -> dict[str, Any]:
        symbol_id = require_string(arguments, "symbolId")
        max_lines = clamp_int(arguments.get("maxLines", 500), minimum=1, maximum=2000)
        start_offset = optional_int(arguments, "startOffset")
        end_offset = optional_int(arguments, "endOffset")
        requested_start_line = optional_int(arguments, "startLine")
        requested_end_line = optional_int(arguments, "endLine")
        symbol = self.index.symbol_by_id.get(symbol_id)

        if symbol is None:
            return make_text_result(f"Symbol not found: {symbol_id}", is_error=True)

        start_line = int(symbol["startLine"])
        end_line = int(symbol["endLine"])
        slice_start = start_line
        slice_end = end_line

        if start_offset is not None:
            slice_start = start_line + start_offset

        if end_offset is not None:
            slice_end = start_line + end_offset

        if requested_start_line is not None:
            slice_start = requested_start_line

        if requested_end_line is not None:
            slice_end = requested_end_line

        slice_start = max(start_line, min(end_line, slice_start))
        slice_end = max(start_line, min(end_line, slice_end))

        if slice_end < slice_start:
            raise McpError(-32602, "Requested symbol slice end must be >= start")

        effective_end = min(slice_end, slice_start + max_lines - 1)
        code = self.index.read_range(
            project_root=self.project_root,
            file=symbol["fileId"],
            start_line=slice_start,
            end_line=effective_end,
        )

        header = {
            "symbolId": symbol_id,
            "fileId": symbol["fileId"],
            "relativePath": symbol["relativePath"],
            "type": symbol["type"],
            "qualifiedName": symbol.get("qualifiedName"),
            "startLine": start_line,
            "endLine": end_line,
            "requestedStartLine": slice_start,
            "requestedEndLine": slice_end,
            "returnedStartLine": slice_start,
            "returnedEndLine": effective_end,
            "truncated": effective_end < slice_end,
        }

        return make_text_result(
            json.dumps(header, indent=2, ensure_ascii=False)
            + "\n\nSOURCE:\n"
            + code
        )

    def read_range(self, arguments: dict[str, Any]) -> dict[str, Any]:
        file = require_string(arguments, "file")
        start_line = optional_int(arguments, "startLine")
        end_line = optional_int(arguments, "endLine")
        line = optional_int(arguments, "line")
        before_lines = clamp_int(arguments.get("beforeLines", 5), minimum=0, maximum=2000)
        after_lines = clamp_int(arguments.get("afterLines", 5), minimum=0, maximum=2000)
        max_lines = clamp_int(arguments.get("maxLines", 500), minimum=1, maximum=2000)

        has_explicit_range = start_line is not None or end_line is not None
        has_around_line = line is not None

        if has_explicit_range and has_around_line:
            raise McpError(-32602, "line cannot be combined with startLine/endLine")

        if has_explicit_range:
            if start_line is None or end_line is None:
                raise McpError(-32602, "startLine and endLine must be provided together")
            if start_line < 1 or end_line < 1:
                raise McpError(-32602, "startLine and endLine must be >= 1")
            mode = "range"
        elif has_around_line:
            if line < 1:
                raise McpError(-32602, "line must be >= 1")
            start_line = max(1, line - before_lines)
            end_line = line + after_lines
            mode = "around_line"
        else:
            raise McpError(-32602, "Provide either startLine/endLine or line")

        if end_line < start_line:
            raise McpError(-32602, "endLine must be >= startLine")

        effective_end = min(end_line, start_line + max_lines - 1)
        code = self.index.read_range(
            project_root=self.project_root,
            file=file,
            start_line=start_line,
            end_line=effective_end,
        )

        header = {
            "file": file,
            "mode": mode,
            "requestedStartLine": start_line,
            "requestedEndLine": end_line,
            "returnedStartLine": start_line,
            "returnedEndLine": effective_end,
            "truncated": effective_end < end_line,
        }

        if mode == "around_line":
            header.update(
                {
                    "line": line,
                    "beforeLines": before_lines,
                    "afterLines": after_lines,
                }
            )

        return make_text_result(
            json.dumps(header, indent=2, ensure_ascii=False)
            + "\n\nSOURCE:\n"
            + code
        )

    def list_file_symbols(self, arguments: dict[str, Any]) -> dict[str, Any]:
        file = require_string(arguments, "file")
        limit = clamp_int(arguments.get("limit", 500), minimum=1, maximum=2000)
        compact = optional_bool(arguments, "compact", False)
        hide_namespaces = optional_bool(arguments, "hideNamespaces", False)
        symbol_types = optional_string_set(arguments, "symbolTypes")
        container = arguments.get("container")

        if container is not None and not isinstance(container, str):
            raise McpError(-32602, "container must be a string when provided")

        results = self.index.list_file_symbols(
            file,
            limit=limit,
            symbol_types=symbol_types,
            container=container,
            hide_namespaces=hide_namespaces,
            compact=compact,
        )
        return self.json_result(arguments, results)

    def get_nearest_symbol_for_line(self, arguments: dict[str, Any]) -> dict[str, Any]:
        file = require_string(arguments, "file")
        line = require_int(arguments, "line")
        include_data = optional_bool(arguments, "includeData", True)
        limit = clamp_int(arguments.get("limit", 10), minimum=1, maximum=50)
        file_item = self.index.get_file_item(file)

        if file_item is None:
            return make_text_result(f"File not found: {file}", is_error=True)

        file_id = str(file_item["fileId"])
        relative_path = str(file_item["relativePath"])
        containing: list[dict[str, Any]] = []
        nearest_before: list[dict[str, Any]] = []
        nearest_after: list[dict[str, Any]] = []

        def classify(item: dict[str, Any], *, kind: str) -> None:
            start_line = int(item.get("startLine") or 0)
            end_line = int(item.get("endLine") or start_line)

            if start_line <= line <= end_line:
                distance = 0
                relation = "containing"
            elif end_line < line:
                distance = line - end_line
                relation = "before"
            else:
                distance = start_line - line
                relation = "after"

            result = {
                "kind": kind,
                "relation": relation,
                "distance": distance,
                "relativePath": relative_path,
                "startLine": start_line,
                "endLine": end_line,
            }

            if kind == "symbol":
                result.update(
                    {
                        "symbolId": item.get("symbolId"),
                        "type": item.get("type"),
                        "qualifiedName": item.get("qualifiedName") or item.get("shortName"),
                        "signature": item.get("signature"),
                    }
                )
            else:
                result.update(
                    {
                        "dataId": item.get("dataId"),
                        "declarationKind": item.get("declarationKind"),
                        "qualifiedName": item.get("qualifiedName") or item.get("name"),
                        "signature": item.get("signature"),
                        "typeText": item.get("typeText"),
                    }
                )

            if relation == "containing":
                containing.append(result)
            elif relation == "before":
                nearest_before.append(result)
            else:
                nearest_after.append(result)

        for symbol in self.index.symbols:
            if symbol.get("fileId") == file_id:
                classify(symbol, kind="symbol")

        if include_data:
            for data_item in self.index.data:
                if data_item.get("fileId") == file_id:
                    classify(data_item, kind="data")

        containing.sort(
            key=lambda item: (
                int(item.get("endLine") or 0) - int(item.get("startLine") or 0),
                int(item.get("startLine") or 0),
                str(item.get("qualifiedName") or ""),
            )
        )
        nearest_before.sort(
            key=lambda item: (
                int(item.get("distance") or 0),
                -int(item.get("endLine") or 0),
                str(item.get("qualifiedName") or ""),
            )
        )
        nearest_after.sort(
            key=lambda item: (
                int(item.get("distance") or 0),
                int(item.get("startLine") or 0),
                str(item.get("qualifiedName") or ""),
            )
        )

        return self.json_result(
            arguments,
            {
                "schema": "cpp.nearest_symbol_for_line.v1",
                "fileId": file_id,
                "relativePath": relative_path,
                "line": line,
                "includeData": include_data,
                "containing": containing[:limit],
                "nearestBefore": nearest_before[:limit],
                "nearestAfter": nearest_after[:limit],
            }
        )

    def find_module(self, arguments: dict[str, Any]) -> dict[str, Any]:
        module_name = require_string(arguments, "moduleName")
        compact = optional_bool(arguments, "compact", False)

        if "::" in module_name:
            return self.json_result(
                arguments,
                {
                    "error": "namespace_passed_to_module_lookup",
                    "message": (
                        "This looks like a C++ namespace, not a C++20 module name. "
                        "Use find_symbol for namespaces/classes/functions, or pass "
                        "a real module name such as SmartFTP.TextEditor:View.Controls.Editor."
                    ),
                    "query": module_name,
                    "results": [],
                },
                is_error=False,
            )

        results = self.index.find_module(module_name)

        if compact:
            results = compact_module_files(results)

        return self.json_result(arguments, results)

    def list_module_files(self, arguments: dict[str, Any]) -> dict[str, Any]:
        return self.find_module(arguments)

    def find_files(self, arguments: dict[str, Any]) -> dict[str, Any]:
        pattern = require_string(arguments, "pattern")
        limit = clamp_int(arguments.get("limit", 100), minimum=1, maximum=500)
        return self.json_result(arguments, self.index.find_files(pattern, limit=limit))

    def find_symbols_glob(self, arguments: dict[str, Any]) -> dict[str, Any]:
        pattern = require_string(arguments, "pattern")
        limit = clamp_int(arguments.get("limit", 100), minimum=1, maximum=500)
        return self.json_result(arguments, self.index.find_symbols_glob(pattern, limit=limit))

    def search_modules(self, arguments: dict[str, Any]) -> dict[str, Any]:
        pattern = require_string(arguments, "pattern")

        if "::" in pattern:
            return self.json_result(
                arguments,
                {
                    "error": "namespace_passed_to_module_glob",
                    "message": (
                        "This looks like a C++ namespace pattern, not a C++20 module pattern. "
                        "Use find_symbols_glob for namespaces/classes/functions."
                    ),
                    "pattern": pattern,
                    "results": [],
                }
            )

        limit = clamp_int(arguments.get("limit", 100), minimum=1, maximum=500)
        return self.json_result(arguments, self.index.search_modules(pattern, limit=limit))

    def get_module_map_summary(self, arguments: dict[str, Any]) -> dict[str, Any]:
        module_map = self.require_module_map()

        return self.json_result(
            arguments,
            {
                "schema": module_map.get("schema"),
                "projectRoot": module_map.get("projectRoot"),
                "counts": module_map.get("counts", {}),
                "path": self.module_map_path.as_posix(),
            }
        )

    def get_module_info(self, arguments: dict[str, Any]) -> dict[str, Any]:
        module_name = require_string(arguments, "moduleName")
        compact = optional_bool(arguments, "compact", False)

        if "::" in module_name:
            return self.json_result(
                arguments,
                {
                    "error": "namespace_passed_to_module_lookup",
                    "message": (
                        "This looks like a C++ namespace, not a C++20 module name. "
                        "Use find_symbol/find_symbols_glob for namespaces/classes/functions."
                    ),
                    "query": module_name,
                    "result": None,
                }
            )

        module_map = self.require_module_map()
        modules = module_map.get("modules", {})
        result = modules.get(module_name)

        if result is None:
            return self.json_result(
                arguments,
                {
                    "query": module_name,
                    "result": None,
                }
            )

        if compact:
            result = compact_module_entry(result)

        return self.json_result(arguments, result)

    def list_module_imports(self, arguments: dict[str, Any]) -> dict[str, Any]:
        module_name = require_string(arguments, "moduleName")
        compact = optional_bool(arguments, "compact", False)
        module_map = self.require_module_map()
        entry = module_map.get("modules", {}).get(module_name)

        if entry is None:
            return self.json_result(
                arguments,
                {
                    "query": module_name,
                    "imports": [],
                    "found": False,
                }
            )

        imports = entry.get("imports", [])

        if compact:
            imports = compact_module_imports(imports)

        return self.json_result(
            arguments,
            {
                "moduleName": module_name,
                "imports": imports,
                "found": True,
            }
        )

    def list_module_imported_by(self, arguments: dict[str, Any]) -> dict[str, Any]:
        module_name = require_string(arguments, "moduleName")
        compact = optional_bool(arguments, "compact", False)
        module_map = self.require_module_map()
        entry = module_map.get("modules", {}).get(module_name)

        if entry is None:
            return self.json_result(
                arguments,
                {
                    "query": module_name,
                    "importedBy": [],
                    "found": False,
                }
            )

        imported_by = entry.get("importedBy", [])

        if compact:
            imported_by = compact_module_imported_by(imported_by)

        return self.json_result(
            arguments,
            {
                "moduleName": module_name,
                "importedBy": imported_by,
                "found": True,
            }
        )


    def get_module_tree(self, arguments: dict[str, Any]) -> dict[str, Any]:
        max_depth = clamp_int(arguments.get("maxDepth", 4), minimum=1, maximum=20)
        module_map = self.require_module_map()

        return self.json_result(
            arguments,
            {
                "maxDepth": max_depth,
                "tree": _trim_tree(
                    module_map.get("tree", {}),
                    max_depth=max_depth,
                ),
            }
        )

    def find_data(self, arguments: dict[str, Any]) -> dict[str, Any]:
        query = require_query(arguments)
        container = arguments.get("container")
        compact = optional_bool(arguments, "compact", False)

        if container is not None and not isinstance(container, str):
            raise McpError(-32602, "container must be a string when provided")

        limit = clamp_int(arguments.get("limit", 20), minimum=1, maximum=500)
        results = self.index.find_data(query, container=container, limit=limit)

        if compact:
            results = compact_data_items(results)

        return self.json_result(arguments, results)


    def list_type_members(self, arguments: dict[str, Any]) -> dict[str, Any]:
        container = require_string(arguments, "container")
        compact = optional_bool(arguments, "compact", False)
        limit = clamp_int(arguments.get("limit", 500), minimum=1, maximum=1000)
        results = self.index.list_type_members(container, limit=limit)

        if compact:
            results = compact_data_items(results)

        return self.json_result(arguments, results)


    def read_data(self, arguments: dict[str, Any]) -> dict[str, Any]:
        data_id = require_string(arguments, "dataId")
        item = self.index.data_by_id.get(data_id)

        if item is None:
            return make_text_result(f"Data declaration not found: {data_id}", is_error=True)

        code = self.index.read_data(
            project_root=self.project_root,
            data_id=data_id,
        )

        header = {
            "dataId": data_id,
            "fileId": item["fileId"],
            "relativePath": item["relativePath"],
            "declarationKind": item.get("declarationKind"),
            "scopeKind": item.get("scopeKind"),
            "name": item.get("name"),
            "qualifiedName": item.get("qualifiedName"),
            "container": item.get("container"),
            "typeText": item.get("typeText"),
            "startLine": item.get("startLine"),
            "endLine": item.get("endLine"),
        }

        return make_text_result(
            json.dumps(header, indent=2, ensure_ascii=False)
            + "\n\nSOURCE:\n"
            + code
        )


    def get_symbol_leading_comment(self, arguments: dict[str, Any]) -> dict[str, Any]:
        symbol_id = require_string(arguments, "symbolId")
        max_lines = clamp_int(arguments.get("maxLines", 20), minimum=1, maximum=200)
        allow_blank_gap = optional_bool(arguments, "allowBlankGap", True)

        result = self.index.get_symbol_leading_comment(
            project_root=self.project_root,
            symbol_id=symbol_id,
            max_lines=max_lines,
            allow_blank_gap=allow_blank_gap,
        )

        if result is None:
            return make_text_result(f"Symbol not found: {symbol_id}", is_error=True)

        return make_json_text_result(result)


    def get_data_leading_comment(self, arguments: dict[str, Any]) -> dict[str, Any]:
        data_id = require_string(arguments, "dataId")
        max_lines = clamp_int(arguments.get("maxLines", 20), minimum=1, maximum=200)
        allow_blank_gap = optional_bool(arguments, "allowBlankGap", True)

        result = self.index.get_data_leading_comment(
            project_root=self.project_root,
            data_id=data_id,
            max_lines=max_lines,
            allow_blank_gap=allow_blank_gap,
        )

        if result is None:
            return make_text_result(f"Data declaration not found: {data_id}", is_error=True)

        return make_json_text_result(result)


    def get_file_header_comment(self, arguments: dict[str, Any]) -> dict[str, Any]:
        file = require_string(arguments, "file")
        max_lines = clamp_int(arguments.get("maxLines", 120), minimum=1, maximum=500)

        try:
            result = self.index.get_file_header_comment(
                project_root=self.project_root,
                file=file,
                max_lines=max_lines,
            )
        except FileNotFoundError:
            return make_text_result(f"File not found: {file}", is_error=True)

        return make_json_text_result(result)


    def get_module_header_comment(self, arguments: dict[str, Any]) -> dict[str, Any]:
        module_name = require_string(arguments, "moduleName")

        if "::" in module_name:
            return make_json_text_result(
                {
                    "error": "namespace_passed_to_module_header_lookup",
                    "message": (
                        "This looks like a C++ namespace, not a C++20 module name. "
                        "Use get_file_header_comment for files or symbol/data tools for C++ entities."
                    ),
                    "query": module_name,
                    "results": [],
                },
                is_error=False,
            )

        max_lines = clamp_int(arguments.get("maxLines", 120), minimum=1, maximum=500)
        result = self.index.get_module_header_comment(
            project_root=self.project_root,
            module_name=module_name,
            max_lines=max_lines,
        )
        return make_json_text_result(result)


    def get_file_structure(self, arguments: dict[str, Any]) -> dict[str, Any]:
        file = require_string(arguments, "file")
        include_outline = optional_bool(arguments, "includeOutline", True)
        outline_limit = clamp_int(arguments.get("outlineLimit", 500), minimum=1, maximum=5000)
        compact_outline = optional_bool(arguments, "compactOutline", True)
        symbol_types = optional_string_set(arguments, "symbolTypes")
        data_kinds = optional_string_set(arguments, "dataKinds")
        include_data = optional_bool(arguments, "includeData", True)
        include_diagnostics = optional_bool(arguments, "includeDiagnostics", True)
        hide_namespaces = optional_bool(arguments, "hideNamespaces", False)
        include_debug, used_legacy_include_debug = optional_bool_alias(
            arguments,
            "includeIndexerDiagnostics",
            "includeDebug",
            False,
        )
        debug_kinds, used_legacy_debug_kinds = optional_string_set_alias(
            arguments,
            "diagnosticKinds",
            "debugKinds",
        )
        debug_start_line, used_legacy_debug_start = optional_int_alias(
            arguments,
            "diagnosticStartLine",
            "debugStartLine",
        )
        debug_end_line, used_legacy_debug_end = optional_int_alias(
            arguments,
            "diagnosticEndLine",
            "debugEndLine",
        )
        debug_limit, used_legacy_debug_limit = clamp_int_alias(
            arguments,
            "diagnosticLimit",
            "debugLimit",
            default=200,
            minimum=1,
            maximum=5000,
        )
        compact_debug, used_legacy_compact_debug = optional_bool_alias(
            arguments,
            "compactDiagnostics",
            "compactDebug",
            True,
        )
        used_legacy_debug_arguments = any(
            [
                used_legacy_include_debug,
                used_legacy_debug_kinds,
                used_legacy_debug_start,
                used_legacy_debug_end,
                used_legacy_debug_limit,
                used_legacy_compact_debug,
            ]
        )

        allowed_debug_kinds = {
            "diagnostics",
            "structuralEvents",
            "scopeIntervals",
            "functionBodyRanges",
        }

        if debug_kinds is not None and not debug_kinds <= allowed_debug_kinds:
            invalid = sorted(debug_kinds - allowed_debug_kinds)
            raise McpError(-32602, f"Invalid diagnosticKinds: {', '.join(invalid)}")

        if debug_start_line is not None and debug_end_line is not None and debug_end_line < debug_start_line:
            raise McpError(-32602, "diagnosticEndLine must be >= diagnosticStartLine")

        result = self.index.get_file_structure(
            file,
            include_outline=include_outline,
            outline_limit=outline_limit,
            compact_outline=compact_outline,
            symbol_types=symbol_types,
            data_kinds=data_kinds,
            include_data=include_data,
            include_diagnostics=include_diagnostics,
            hide_namespaces=hide_namespaces,
            include_debug=include_debug,
            debug_kinds=debug_kinds,
            debug_start_line=debug_start_line,
            debug_end_line=debug_end_line,
            debug_limit=debug_limit,
            compact_debug=compact_debug,
        )

        if result is None:
            return make_text_result(f"File not found: {file}", is_error=True)

        if include_debug and "debug" in result and not used_legacy_debug_arguments:
            result["indexerDiagnostics"] = result.pop("debug")

            filters = result.get("filters")

            if isinstance(filters, dict):
                filters["includeIndexerDiagnostics"] = filters.pop("includeDebug", include_debug)
                filters["diagnosticKinds"] = filters.pop(
                    "debugKinds",
                    sorted(debug_kinds) if debug_kinds else None,
                )
                filters["diagnosticStartLine"] = filters.pop("debugStartLine", debug_start_line)
                filters["diagnosticEndLine"] = filters.pop("debugEndLine", debug_end_line)
                filters["diagnosticLimit"] = filters.pop("debugLimit", debug_limit)
                filters["compactDiagnostics"] = filters.pop("compactDebug", compact_debug)

        return self.json_result(arguments, result)

    def search_source(self, arguments: dict[str, Any]) -> dict[str, Any]:
        query = require_string(arguments, "query")
        file = arguments.get("file")
        file_pattern = arguments.get("filePattern")
        symbol_id = arguments.get("symbolId")

        if file is not None and not isinstance(file, str):
            raise McpError(-32602, "file must be a string when provided")

        if file_pattern is not None and not isinstance(file_pattern, str):
            raise McpError(-32602, "filePattern must be a string when provided")

        if symbol_id is not None and not isinstance(symbol_id, str):
            raise McpError(-32602, "symbolId must be a string when provided")

        start_line = None
        end_line = None

        if symbol_id is not None:
            if file is not None or file_pattern is not None:
                raise McpError(-32602, "symbolId cannot be combined with file or filePattern")

            symbol = self.index.symbol_by_id.get(symbol_id)

            if symbol is None:
                return make_text_result(f"Symbol not found: {symbol_id}", is_error=True)

            file = str(symbol["fileId"])
            start_line = int(symbol["startLine"])
            end_line = int(symbol["endLine"])

        case_sensitive = optional_bool(arguments, "caseSensitive", False)
        whole_word = optional_bool(arguments, "wholeWord", False)
        use_regex = optional_bool(arguments, "useRegex", False)
        limit = clamp_int(arguments.get("limit", 100), minimum=1, maximum=1000)
        context_lines = clamp_int(arguments.get("contextLines", 0), minimum=0, maximum=20)

        try:
            result = self.index.search_source(
            project_root=self.project_root,
            query=query,
            file=file,
            file_pattern=file_pattern,
            start_line=start_line,
            end_line=end_line,
            case_sensitive=case_sensitive,
            whole_word=whole_word,
            use_regex=use_regex,
            limit=limit,
            context_lines=context_lines,
        )
        except re.error as exc:
            raise McpError(-32602, f"Invalid regular expression: {exc}") from exc

        if symbol_id is not None:
            result["symbolId"] = symbol_id
            result["symbolStartLine"] = start_line
            result["symbolEndLine"] = end_line

        return make_json_text_result(result)


# ---------------------------------------------------------------------------
# Argument validation
# ---------------------------------------------------------------------------

def require_string(arguments: dict[str, Any], key: str) -> str:
    value = arguments.get(key)

    if not isinstance(value, str) or not value:
        raise McpError(-32602, f"Missing or invalid string argument: {key}")

    return value


def require_int(arguments: dict[str, Any], key: str) -> int:
    value = arguments.get(key)

    if not isinstance(value, int):
        raise McpError(-32602, f"Missing or invalid integer argument: {key}")

    return value


def optional_int(arguments: dict[str, Any], key: str) -> int | None:
    value = arguments.get(key)

    if value is None:
        return None

    if not isinstance(value, int):
        raise McpError(-32602, f"Invalid integer argument: {key}")

    return value


def require_query(arguments: dict[str, Any]) -> str:
    value = arguments.get("query")

    if value is None:
        value = arguments.get("name")

    if not isinstance(value, str) or not value:
        raise McpError(
            -32602,
            "Missing or invalid symbol query. Use argument 'query'. "
            "'name' is accepted only as a compatibility alias.",
        )

    return value


def clamp_int(value: Any, *, minimum: int, maximum: int) -> int:
    if not isinstance(value, int):
        value = minimum

    return max(minimum, min(maximum, value))


def optional_bool(arguments: dict[str, Any], key: str, default: bool) -> bool:
    value = arguments.get(key, default)

    if not isinstance(value, bool):
        raise McpError(-32602, f"Invalid boolean argument: {key}")

    return value


def optional_bool_alias(
    arguments: dict[str, Any],
    key: str,
    legacy_key: str,
    default: bool,
) -> tuple[bool, bool]:
    if key in arguments:
        return optional_bool(arguments, key, default), False

    if legacy_key in arguments:
        return optional_bool(arguments, legacy_key, default), True

    return default, False


def optional_string(arguments: dict[str, Any], key: str) -> str | None:
    value = arguments.get(key)

    if value is None:
        return None

    if not isinstance(value, str) or not value:
        raise McpError(-32602, f"Invalid string argument: {key}")

    return value


def optional_enum(
    arguments: dict[str, Any],
    key: str,
    allowed: set[str],
    default: str,
) -> str:
    value = arguments.get(key, default)

    if not isinstance(value, str) or value not in allowed:
        allowed_text = ", ".join(sorted(allowed))
        raise McpError(-32602, f"Invalid value for {key}; expected one of: {allowed_text}")

    return value


def optional_string_set(arguments: dict[str, Any], key: str) -> set[str] | None:
    value = arguments.get(key)

    if value is None:
        return None

    if not isinstance(value, list):
        raise McpError(-32602, f"{key} must be an array of strings")

    result: set[str] = set()

    for item in value:
        if not isinstance(item, str) or not item:
            raise McpError(-32602, f"{key} must be an array of non-empty strings")

        result.add(item)

    return result


def optional_string_set_alias(
    arguments: dict[str, Any],
    key: str,
    legacy_key: str,
) -> tuple[set[str] | None, bool]:
    if key in arguments:
        return optional_string_set(arguments, key), False

    if legacy_key in arguments:
        return optional_string_set(arguments, legacy_key), True

    return None, False


def optional_int_alias(
    arguments: dict[str, Any],
    key: str,
    legacy_key: str,
) -> tuple[int | None, bool]:
    if key in arguments:
        return optional_int(arguments, key), False

    if legacy_key in arguments:
        return optional_int(arguments, legacy_key), True

    return None, False


def clamp_int_alias(
    arguments: dict[str, Any],
    key: str,
    legacy_key: str,
    *,
    default: int,
    minimum: int,
    maximum: int,
) -> tuple[int, bool]:
    if key in arguments:
        return clamp_int(arguments.get(key, default), minimum=minimum, maximum=maximum), False

    if legacy_key in arguments:
        return clamp_int(arguments.get(legacy_key, default), minimum=minimum, maximum=maximum), True

    return default, False


def json_response_options(arguments: dict[str, Any]) -> dict[str, Any]:
    return {
        "response_format": optional_enum(
            arguments,
            "responseFormat",
            {"pretty", "minified"},
            "pretty",
        ),
        "omit_nulls": optional_bool(arguments, "omitNulls", False),
        "omit_empty": optional_bool(arguments, "omitEmpty", False),
    }

# ---------------------------------------------------------------------------
# MCP dispatcher
# ---------------------------------------------------------------------------

class McpServer:
    def __init__(self, tools: CodeIndexTools) -> None:
        self.tools = tools
        self.started_at = now_iso()
        self.transport = "stdio"
        self.tool_handlers: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
            # Project/cache tools
            "get_project_summary": self.tools.get_project_summary,
            "reload_index_cache": self.tools.reload_index_cache,

            # Symbol/source navigation tools
            "find_symbol": self.tools.find_symbol,
            "find_declaration": self.tools.find_declaration,
            "read_symbol": self.tools.read_symbol,
            "read_range": self.tools.read_range,
            "get_nearest_symbol_for_line": self.tools.get_nearest_symbol_for_line,

            # File navigation tools
            "list_file_symbols": self.tools.list_file_symbols,
            "find_files": self.tools.find_files,
            "find_symbols_glob": self.tools.find_symbols_glob,

            # Data/member tools
            "find_data": self.tools.find_data,
            "list_type_members": self.tools.list_type_members,
            "read_data": self.tools.read_data,

            # Module metadata tools
            "search_modules": self.tools.search_modules,
            "get_module_map_summary": self.tools.get_module_map_summary,
            "get_module_info": self.tools.get_module_info,
            "find_module": self.tools.find_module,
            "list_module_files": self.tools.list_module_files,
            "list_module_imports": self.tools.list_module_imports,
            "list_module_imported_by": self.tools.list_module_imported_by,
            "get_module_tree": self.tools.get_module_tree,

            # File overview/comment/search tools
            "get_file_structure": self.tools.get_file_structure,
            "get_symbol_leading_comment": self.tools.get_symbol_leading_comment,
            "get_data_leading_comment": self.tools.get_data_leading_comment,
            "get_file_header_comment": self.tools.get_file_header_comment,
            "get_module_header_comment": self.tools.get_module_header_comment,
            "search_source": self.tools.search_source,
        }

        if self.tools.change_tracker is not None:
            self.tool_handlers.update(
                {
                    # Change tracking tools
                    "list_changed_files": self.tools.list_changed_files,
                    "list_recent_revisions": self.tools.list_recent_revisions,
                    "get_revision_summary": self.tools.get_revision_summary,
                    "get_file_change_hunks": self.tools.get_file_change_hunks,
                }
            )

    def status_snapshot(self) -> dict[str, Any]:
        return {
            "server": {
                "name": SERVER_NAME,
                "version": SERVER_VERSION,
                "transport": self.transport,
                "startedAt": self.started_at,
                "pid": os.getpid(),
                "process": process_stats(),
            },
            **self.tools.status_snapshot(),
        }

    def handle_request(self, request: dict[str, Any]) -> dict[str, Any] | None:
        method = request.get("method")
        request_id = request.get("id")
        params = request.get("params") or {}

        # Notifications have no id. For initialized/shutdown notifications we
        # should not send a response.
        if request_id is None and method in {"notifications/initialized", "notifications/cancelled"}:
            return None

        try:
            result = self.dispatch(method, params)
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": result,
            }
        except McpError as exc:
            error: dict[str, Any] = {
                "code": exc.code,
                "message": exc.message,
            }

            if exc.data is not None:
                error["data"] = exc.data

            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": error,
            }
        except Exception as exc:  # noqa: BLE001 - keep server alive and surface error to host.
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {
                    "code": -32603,
                    "message": str(exc),
                    "data": traceback.format_exc(),
                },
            }

    def dispatch(self, method: str, params: dict[str, Any]) -> Any:
        if method == "initialize":
            requested_protocol = params.get("protocolVersion") or "2024-11-05"
            return {
                "protocolVersion": requested_protocol,
                "capabilities": {
                    "tools": {},
                },
                "serverInfo": {
                    "name": SERVER_NAME,
                    "version": SERVER_VERSION,
                },
            }

        if method == "ping":
            return {}

        if method == "tools/list":
            tools = tool_definitions()

            if self.tools.change_tracker is not None:
                tools.extend(change_tracking_tool_definitions())

            add_response_packing_options(tools)
            return {
                "tools": tools,
            }

        if method == "tools/call":
            tool_name = params.get("name")
            arguments = params.get("arguments") or {}

            if not isinstance(tool_name, str):
                raise McpError(-32602, "tools/call requires string params.name")

            if not isinstance(arguments, dict):
                raise McpError(-32602, "tools/call requires object params.arguments")

            handler = self.tool_handlers.get(tool_name)

            if handler is None:
                raise McpError(-32601, f"Unknown tool: {tool_name}")

            return handler(arguments)

        # Keep optional MCP surfaces empty but valid.
        if method in {"resources/list", "prompts/list"}:
            key = "resources" if method == "resources/list" else "prompts"
            return {key: []}

        raise McpError(-32601, f"Method not found: {method}")

    def run(self) -> None:
        for request in read_messages():
            response = self.handle_request(request)

            if response is not None:
                write_message(response)


class McpHttpHandler(BaseHTTPRequestHandler):
    server_version = "McpCppProjectIndexerHTTP/0.1"
    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:
        self._request_body_bytes = 0
        path = self._request_path()

        if path in {"", "/health"}:
            self._write_json(
                HTTPStatus.OK,
                {
                    "status": "ok",
                    "server": SERVER_NAME,
                    "version": SERVER_VERSION,
                },
            )
            return

        if path == "/status":
            mcp_server: McpServer = self.server.mcp_server  # type: ignore[attr-defined]
            self._write_json(HTTPStatus.OK, mcp_server.status_snapshot())
            return

        if path in {"/mcp", "/sse"}:
            self._write_sse_endpoint()
            return

        self.send_error(HTTPStatus.NOT_FOUND, "Not found")

    def do_OPTIONS(self) -> None:
        self._request_body_bytes = 0
        self.send_response(HTTPStatus.NO_CONTENT)
        self._write_common_headers(content_length=0)
        self.end_headers()

    def do_POST(self) -> None:
        if self._request_path() not in {"", "/mcp", "/rpc", "/messages"}:
            self.send_error(HTTPStatus.NOT_FOUND, "Not found")
            return

        try:
            body = self._read_request_body()
            self._request_body_bytes = len(body)
            payload = json.loads(body.decode("utf-8"))
            self._mcp_request_detail = self._describe_mcp_payload(payload)
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            self._mcp_request_detail = "parse_error"
            self._write_json(
                HTTPStatus.OK,
                {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {
                        "code": -32700,
                        "message": f"Parse error: {exc}",
                    },
                },
            )
            return

        mcp_server: McpServer = self.server.mcp_server  # type: ignore[attr-defined]

        if isinstance(payload, list):
            responses = [
                response
                for item in payload
                if isinstance(item, dict)
                for response in [mcp_server.handle_request(item)]
                if response is not None
            ]
            self._write_json(HTTPStatus.OK, responses)
            return

        if not isinstance(payload, dict):
            self._write_json(
                HTTPStatus.OK,
                {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {
                        "code": -32600,
                        "message": "Invalid Request",
                    },
                },
            )
            return

        response = mcp_server.handle_request(payload)

        if response is None:
            self._write_empty(HTTPStatus.ACCEPTED)
            return

        self._write_json(HTTPStatus.OK, response)

    def log_message(self, format: str, *args: Any) -> None:
        if self._request_path() == "/status":
            return

        request_bytes = getattr(self, "_request_body_bytes", 0)
        response_bytes = getattr(self, "_response_body_bytes", 0)
        mcp_detail = getattr(self, "_mcp_request_detail", "-")
        print(
            (
                "[mcp-cpp-project-indexer-http] "
                + format % args
                + f" mcp={mcp_detail}"
                + f" requestBytes={request_bytes} responseBytes={response_bytes}"
            ),
            file=sys.stderr,
            flush=True,
        )

    def _request_path(self) -> str:
        return urllib.parse.urlparse(self.path).path.rstrip("/")

    def _read_request_body(self) -> bytes:
        content_length = self.headers.get("Content-Length")

        if content_length is not None:
            return self.rfile.read(int(content_length))

        if self.headers.get("Transfer-Encoding", "").lower() == "chunked":
            chunks: list[bytes] = []

            while True:
                size_line = self.rfile.readline().split(b";", 1)[0].strip()
                if not size_line:
                    raise ValueError("Missing chunk size")

                size = int(size_line, 16)

                if size == 0:
                    while self.rfile.readline().strip():
                        pass
                    break

                chunks.append(self.rfile.read(size))
                self.rfile.read(2)

            return b"".join(chunks)

        raise ValueError("Missing Content-Length")

    def _describe_mcp_payload(self, payload: Any) -> str:
        if isinstance(payload, list):
            items = [
                self._describe_mcp_request(item)
                for item in payload
                if isinstance(item, dict)
            ]
            if not items:
                return "batch(empty)"
            preview = ",".join(items[:5])
            suffix = f",+{len(items) - 5}" if len(items) > 5 else ""
            return f"batch[{len(items)}]({preview}{suffix})"

        if isinstance(payload, dict):
            return self._describe_mcp_request(payload)

        return type(payload).__name__

    @staticmethod
    def _describe_mcp_request(request: dict[str, Any]) -> str:
        method = request.get("method")
        if not isinstance(method, str):
            return "invalid"

        params = request.get("params")
        if method == "tools/call" and isinstance(params, dict):
            tool_name = params.get("name")
            arguments = params.get("arguments")
            argument_keys = []
            if isinstance(arguments, dict):
                argument_keys = sorted(str(key) for key in arguments.keys())
            keys_text = ",".join(argument_keys[:6])
            if len(argument_keys) > 6:
                keys_text += ",..."
            return f"tools/call:{tool_name or '?'}({keys_text})"

        return method

    def _write_empty(self, status: HTTPStatus) -> None:
        self._response_body_bytes = 0
        self.send_response(status)
        self._write_common_headers(content_length=0)
        self.end_headers()

    def _write_json(self, status: HTTPStatus, data: Any) -> None:
        body = json_dumps(data).encode("utf-8")
        self._response_body_bytes = len(body)
        self.send_response(status)
        self._write_common_headers(
            content_length=len(body),
            content_type="application/json; charset=utf-8",
        )
        self.end_headers()
        self.wfile.write(body)

    def _write_sse_endpoint(self) -> None:
        session_id = self.server.mcp_session_id  # type: ignore[attr-defined]
        endpoint_payload = (
            "event: endpoint\r\n"
            f"data: /messages?sessionId={session_id}\r\n\r\n"
        ).encode("utf-8")
        self._response_body_bytes = len(endpoint_payload)
        self.send_response(HTTPStatus.OK)
        self._write_sse_headers()
        self.end_headers()
        self.wfile.write(endpoint_payload)
        self.wfile.flush()

        while True:
            try:
                time.sleep(15)
                self.wfile.write(b": keepalive\r\n\r\n")
                self.wfile.flush()
            except (ConnectionError, OSError):
                return

    def _write_sse_headers(self) -> None:
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache, no-transform")
        self.send_header("Connection", "keep-alive")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Expose-Headers", "Mcp-Session-Id")
        self.send_header(
            "Mcp-Session-Id",
            self.server.mcp_session_id,  # type: ignore[attr-defined]
        )

    def _write_common_headers(
        self,
        *,
        content_length: int,
        content_type: str | None = None,
    ) -> None:
        if content_type is not None:
            self.send_header("Content-Type", content_type)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header(
            "Access-Control-Allow-Headers",
            "Content-Type, Accept, Authorization, x-api-key, "
            "Mcp-Session-Id, Last-Event-ID, MCP-Protocol-Version",
        )
        self.send_header("Access-Control-Expose-Headers", "Mcp-Session-Id")
        self.send_header(
            "Mcp-Session-Id",
            self.server.mcp_session_id,  # type: ignore[attr-defined]
        )
        self.send_header("Content-Length", str(content_length))


class ExclusiveThreadingHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = False

    def server_bind(self) -> None:
        if os.name == "nt" and hasattr(self.socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(
                socket.SOL_SOCKET,
                socket.SO_EXCLUSIVEADDRUSE,  # type: ignore[attr-defined]
                1,
            )

        super().server_bind()

    def handle_error(self, request: Any, client_address: Any) -> None:
        exc = sys.exc_info()[1]

        if isinstance(exc, (BrokenPipeError, ConnectionResetError, ConnectionAbortedError)):
            print(
                f"[mcp-cpp-project-indexer-http] client disconnected: {client_address}",
                file=sys.stderr,
                flush=True,
            )
            return

        if isinstance(exc, OSError) and getattr(exc, "winerror", None) in {10053, 10054}:
            print(
                f"[mcp-cpp-project-indexer-http] client disconnected: {client_address}",
                file=sys.stderr,
                flush=True,
            )
            return

        super().handle_error(request, client_address)


def run_http_server(
    server: McpServer,
    *,
    host: str,
    port: int,
) -> None:
    server.transport = "http"
    with index_http_server_lock(server.tools.index_root, host=host, port=port):
        httpd = ExclusiveThreadingHTTPServer((host, port), McpHttpHandler)
        httpd.mcp_server = server  # type: ignore[attr-defined]
        httpd.mcp_session_id = uuid.uuid4().hex  # type: ignore[attr-defined]
        print(
            f"[mcp-cpp-project-indexer] HTTP JSON-RPC listening on http://{host}:{port}/mcp",
            file=sys.stderr,
            flush=True,
        )
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print(
                "[mcp-cpp-project-indexer] HTTP server shutdown requested",
                file=sys.stderr,
                flush=True,
            )
        finally:
            httpd.server_close()
            server.tools.stop_index_watcher()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    configure_stdio_encoding()

    parser = argparse.ArgumentParser(
        description=(
            "MCP stdio server for vs-project-indexer. "
            "Provides routing/read tools only; it does not analyze code."
        )
    )
    parser.add_argument(
        "--project-root",
        type=Path,
        default=DEFAULT_PROJECT_ROOT,
        help="Project root used for read_range/read_symbol.",
    )
    parser.add_argument(
        "--index-root",
        type=Path,
        default=DEFAULT_INDEX_ROOT,
        help="Directory containing manifest.json, names.json, modules.json, symbols.jsonl and files/.",
    )
    parser.add_argument(
        "--watch-index",
        action=argparse.BooleanOptionalAction,
        default=False,
        help=(
            "Start a background source watcher. After changes settle, the server "
            "updates index files on disk and reloads its in-memory cache."
        ),
    )
    parser.add_argument(
        "--watch-poll-interval",
        type=float,
        default=1.0,
        help="Seconds between watcher source tree scans.",
    )
    parser.add_argument(
        "--watch-debounce",
        type=float,
        default=1.5,
        help="Seconds to wait for changes to settle before running an index update.",
    )
    parser.add_argument(
        "--watch-jobs",
        type=int,
        default=1,
        help="Worker process count for watcher-triggered index updates. Use 0 for auto.",
    )
    parser.add_argument(
        "--watch-module-map",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Rebuild module_map.json after watcher-triggered index updates.",
    )
    parser.add_argument(
        "--watch-emit-diagnostic-file-indexes",
        "--watch-emit-debug-file-indexes",
        dest="watch_emit_debug_file_indexes",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Pass diagnostic emission to watcher-triggered index updates.",
    )
    parser.add_argument(
        "--watch-include-extensionless-headers",
        action=argparse.BooleanOptionalAction,
        default=False,
        help=(
            "Let the built-in watcher discover extensionless files that look "
            "like C/C++ headers."
        ),
    )
    parser.add_argument(
        "--watch-git-ignore",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Filter watcher discovery through git check-ignore when available. Default: true.",
    )
    parser.add_argument(
        "--transport",
        choices=["stdio", "http"],
        default="stdio",
        help="MCP transport. stdio is default; http exposes JSON-RPC over HTTP.",
    )
    parser.add_argument(
        "--http-host",
        default="127.0.0.1",
        help="HTTP bind host when --transport http is used. Default: 127.0.0.1.",
    )
    parser.add_argument(
        "--http-port",
        type=int,
        default=8765,
        help="HTTP bind port when --transport http is used. Default: 8765.",
    )
    args = parser.parse_args()

    if not args.project_root.exists():
        raise SystemExit(f"Project root not found: {args.project_root}")

    if not (args.index_root / "manifest.json").exists():
        raise SystemExit(
            "Index not found. Build it first, for example:\n"
            f"python build_project_index.py --root {args.project_root} --output-root {args.index_root}"
        )

    tools = CodeIndexTools(
        project_root=args.project_root,
        index_root=args.index_root,
    )

    if args.watch_index:
        tools.start_index_watcher(
            poll_interval=args.watch_poll_interval,
            debounce=args.watch_debounce,
            jobs=args.watch_jobs,
            module_map=args.watch_module_map,
            emit_debug_file_indexes=args.watch_emit_debug_file_indexes,
            include_extensionless_headers=args.watch_include_extensionless_headers,
            use_git_ignore=args.watch_git_ignore,
        )

    server = McpServer(tools)

    if args.transport == "http":
        run_http_server(server, host=args.http_host, port=args.http_port)
    else:
        server.run()


if __name__ == "__main__":
    main()
