from __future__ import annotations

import argparse
import json
import sys
import traceback
import os
import re
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
from cpp_project_index import LoadedProjectIndex, normalize_jobs
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


def make_json_text_result(data: Any, *, is_error: bool = False) -> dict[str, Any]:
    return make_text_result(
        json.dumps(data, indent=2, ensure_ascii=False),
        is_error=is_error,
    )


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

def tool_definitions() -> list[dict[str, Any]]:
    return [
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
                "Use this for functions, methods, classes, structs, enums, constructors, "
                "destructors, operators, and namespaces. "
                "The required argument is 'query'. "
                "Searches symbol metadata only: shortName, qualifiedName/search aliases, "
                "and fallback signature substring. "
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
            "description": "[Source] Read original source lines for a symbolId, with absolute line numbers. This is a read-only range operation.",
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
                },
                "required": ["symbolId"],
                "additionalProperties": False,
            },
        },
        {
            "name": "read_range",
            "description": "[Source] Read original source lines from a fileId or project-relative path, with absolute line numbers.",
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
                    "maxLines": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 2000,
                        "default": 500,
                    },
                },
                "required": ["file", "startLine", "endLine"],
                "additionalProperties": False,
            },
        },
        {
            "name": "get_nearest_symbol_for_line",
            "description": (
                "[Symbol] Return indexed symbol/data ranges that contain or are nearest to one file line. "
                "This is metadata-only and intended for diagnostics, hunks, build output, and IDE/binary handoff."
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
            "description": "[Module] Find files that define a C++20 module or module partition.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "moduleName": {
                        "type": "string",
                        "description": "Full module name, e.g. uiframework.Elements:ElementImpl.",
                    }
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
                    }
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
                "imports and importedBy. Do not pass C++ namespaces with '::'."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "moduleName": {
                        "type": "string",
                        "description": "Exact C++20 module name, e.g. SmartFTP.Shell.Browser:Impl.",
                    }
                },
                "required": ["moduleName"],
                "additionalProperties": False,
            },
        },
        {
            "name": "list_module_imports",
            "description": "[Module] List direct imports of one exact C++20 module. Metadata only.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "moduleName": {
                        "type": "string",
                        "description": "Exact C++20 module name.",
                    }
                },
                "required": ["moduleName"],
                "additionalProperties": False,
            },
        },
        {
            "name": "list_module_imported_by",
            "description": "[Module] List modules that directly import one exact C++20 module. Metadata only.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "moduleName": {
                        "type": "string",
                        "description": "Exact C++20 module name.",
                    }
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
                },
                "additionalProperties": False,
            },
        },
        {
            "name": "list_type_members",
            "description": (
                "[Data] List indexed data/value declarations directly contained by a class, struct, or namespace. "
                "Use this to inspect member fields/constants after reading a method body. "
                "Returns metadata only: name, typeText, signature and source range."
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
                "Use filePattern or file to narrow broad queries."
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
    ) -> None:
        self.tools = tools
        self.poll_interval = max(0.1, poll_interval)
        self.debounce = max(0.1, debounce)
        self.jobs = jobs
        self.module_map = module_map
        self.indexer_root = Path(__file__).resolve().parent
        self.thread = threading.Thread(
            target=self._run,
            name="mcp-cpp-project-indexer-watch",
            daemon=True,
        )

    def start(self) -> None:
        print(
            (
                "[mcp-cpp-project-indexer] starting index watcher "
                f"poll={self.poll_interval:.2f}s debounce={self.debounce:.2f}s "
                f"jobs={normalize_jobs(self.jobs)} module_map={self.module_map}"
            ),
            file=sys.stderr,
            flush=True,
        )
        self.thread.start()

    def _snapshot(self) -> dict[str, SnapshotEntry]:
        return snapshot_source_files(
            root=self.tools.project_root,
            extensions=None,
            excluded_dir_names=None,
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

            while True:
                time.sleep(self.poll_interval)
                current = self._snapshot()
                diff = diff_snapshots(snapshot, current, root=self.tools.project_root)

                if not diff.changed:
                    continue

                pending_since = time.monotonic()
                pending_snapshot = current
                pending_diff = diff

                while True:
                    time.sleep(self.poll_interval)
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
                result, index_changed = self._run_update(
                    known_files_only=not pending_diff.requires_full_discovery_update,
                    changed_files=pending_diff.modified,
                )

                if result != 0:
                    print(
                        f"[mcp-cpp-project-indexer] watcher update failed: {result}",
                        file=sys.stderr,
                        flush=True,
                    )
                    continue

                snapshot = pending_snapshot
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
            print(
                "[mcp-cpp-project-indexer] watcher stopped after exception:",
                file=sys.stderr,
                flush=True,
            )
            traceback.print_exc(file=sys.stderr)


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

    def start_index_watcher(
        self,
        *,
        poll_interval: float,
        debounce: float,
        jobs: int,
        module_map: bool,
    ) -> None:
        if self.watcher is not None:
            return

        self.watcher = ServerIndexWatcher(
            tools=self,
            poll_interval=poll_interval,
            debounce=debounce,
            jobs=jobs,
            module_map=module_map,
        )
        self.watcher.start()

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
        return make_json_text_result(
            {
                "schema": self.index.manifest.get("schema"),
                "projectRoot": self.project_root.as_posix(),
                "indexRoot": self.index_root.as_posix(),
                "counts": counts,
            }
        )

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
        return make_json_text_result(
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
        return make_json_text_result(
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
        return make_json_text_result(
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

        context_lines = clamp_int(arguments.get("contextLines", 1), minimum=0, maximum=20)
        include_source = optional_bool(arguments, "includeSource", True)
        include_indexed_ranges = optional_bool(arguments, "includeIndexedRanges", True)
        max_hunks = clamp_int(arguments.get("maxHunks", 20), minimum=1, maximum=200)
        max_lines = clamp_int(arguments.get("maxLines", 500), minimum=1, maximum=5000)
        return make_json_text_result(
            self.require_change_tracker().get_file_change_hunks(
                file=file,
                scope=scope,
                revision=revision,
                context_lines=context_lines,
                include_source=include_source,
                include_indexed_ranges=include_indexed_ranges,
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

        results = self.index.find_symbol(
            query,
            limit=limit,
            symbol_types=symbol_types,
            exact_only=exact_only,
            hide_namespaces=hide_namespaces,
            compact=compact,
        )
        return make_json_text_result(results)

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
        return make_json_text_result(candidates[:limit])

    def read_symbol(self, arguments: dict[str, Any]) -> dict[str, Any]:
        symbol_id = require_string(arguments, "symbolId")
        max_lines = clamp_int(arguments.get("maxLines", 500), minimum=1, maximum=2000)
        symbol = self.index.symbol_by_id.get(symbol_id)

        if symbol is None:
            return make_text_result(f"Symbol not found: {symbol_id}", is_error=True)

        start_line = int(symbol["startLine"])
        end_line = int(symbol["endLine"])
        effective_end = min(end_line, start_line + max_lines - 1)
        code = self.index.read_range(
            project_root=self.project_root,
            file=symbol["fileId"],
            start_line=start_line,
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
            "returnedStartLine": start_line,
            "returnedEndLine": effective_end,
            "truncated": effective_end < end_line,
        }

        return make_text_result(
            json.dumps(header, indent=2, ensure_ascii=False)
            + "\n\nSOURCE:\n"
            + code
        )

    def read_range(self, arguments: dict[str, Any]) -> dict[str, Any]:
        file = require_string(arguments, "file")
        start_line = require_int(arguments, "startLine")
        end_line = require_int(arguments, "endLine")
        max_lines = clamp_int(arguments.get("maxLines", 500), minimum=1, maximum=2000)

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
            "requestedStartLine": start_line,
            "requestedEndLine": end_line,
            "returnedStartLine": start_line,
            "returnedEndLine": effective_end,
            "truncated": effective_end < end_line,
        }

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
        return make_json_text_result(results)

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

        return make_json_text_result(
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

        if "::" in module_name:
            return make_json_text_result(
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

        return make_json_text_result(self.index.find_module(module_name))

    def list_module_files(self, arguments: dict[str, Any]) -> dict[str, Any]:
        return self.find_module(arguments)

    def find_files(self, arguments: dict[str, Any]) -> dict[str, Any]:
        pattern = require_string(arguments, "pattern")
        limit = clamp_int(arguments.get("limit", 100), minimum=1, maximum=500)
        return make_json_text_result(self.index.find_files(pattern, limit=limit))

    def find_symbols_glob(self, arguments: dict[str, Any]) -> dict[str, Any]:
        pattern = require_string(arguments, "pattern")
        limit = clamp_int(arguments.get("limit", 100), minimum=1, maximum=500)
        return make_json_text_result(self.index.find_symbols_glob(pattern, limit=limit))

    def search_modules(self, arguments: dict[str, Any]) -> dict[str, Any]:
        pattern = require_string(arguments, "pattern")

        if "::" in pattern:
            return make_json_text_result(
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
        return make_json_text_result(self.index.search_modules(pattern, limit=limit))

    def get_module_map_summary(self, arguments: dict[str, Any]) -> dict[str, Any]:
        module_map = self.require_module_map()

        return make_json_text_result(
            {
                "schema": module_map.get("schema"),
                "projectRoot": module_map.get("projectRoot"),
                "counts": module_map.get("counts", {}),
                "path": self.module_map_path.as_posix(),
            }
        )

    def get_module_info(self, arguments: dict[str, Any]) -> dict[str, Any]:
        module_name = require_string(arguments, "moduleName")

        if "::" in module_name:
            return make_json_text_result(
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
            return make_json_text_result(
                {
                    "query": module_name,
                    "result": None,
                }
            )

        return make_json_text_result(result)

    def list_module_imports(self, arguments: dict[str, Any]) -> dict[str, Any]:
        module_name = require_string(arguments, "moduleName")
        module_map = self.require_module_map()
        entry = module_map.get("modules", {}).get(module_name)

        if entry is None:
            return make_json_text_result(
                {
                    "query": module_name,
                    "imports": [],
                    "found": False,
                }
            )

        return make_json_text_result(
            {
                "moduleName": module_name,
                "imports": entry.get("imports", []),
                "found": True,
            }
        )

    def list_module_imported_by(self, arguments: dict[str, Any]) -> dict[str, Any]:
        module_name = require_string(arguments, "moduleName")
        module_map = self.require_module_map()
        entry = module_map.get("modules", {}).get(module_name)

        if entry is None:
            return make_json_text_result(
                {
                    "query": module_name,
                    "importedBy": [],
                    "found": False,
                }
            )

        return make_json_text_result(
            {
                "moduleName": module_name,
                "importedBy": entry.get("importedBy", []),
                "found": True,
            }
        )


    def get_module_tree(self, arguments: dict[str, Any]) -> dict[str, Any]:
        max_depth = clamp_int(arguments.get("maxDepth", 4), minimum=1, maximum=20)
        module_map = self.require_module_map()

        return make_json_text_result(
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

        if container is not None and not isinstance(container, str):
            raise McpError(-32602, "container must be a string when provided")

        limit = clamp_int(arguments.get("limit", 20), minimum=1, maximum=500)
        results = self.index.find_data(query, container=container, limit=limit)
        return make_json_text_result(results)


    def list_type_members(self, arguments: dict[str, Any]) -> dict[str, Any]:
        container = require_string(arguments, "container")
        limit = clamp_int(arguments.get("limit", 500), minimum=1, maximum=1000)
        results = self.index.list_type_members(container, limit=limit)
        return make_json_text_result(results)


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
        )

        if result is None:
            return make_text_result(f"File not found: {file}", is_error=True)

        return make_json_text_result(result)

    def search_source(self, arguments: dict[str, Any]) -> dict[str, Any]:
        query = require_string(arguments, "query")
        file = arguments.get("file")
        file_pattern = arguments.get("filePattern")

        if file is not None and not isinstance(file, str):
            raise McpError(-32602, "file must be a string when provided")

        if file_pattern is not None and not isinstance(file_pattern, str):
            raise McpError(-32602, "filePattern must be a string when provided")

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
            case_sensitive=case_sensitive,
            whole_word=whole_word,
            use_regex=use_regex,
            limit=limit,
            context_lines=context_lines,
        )
        except re.error as exc:
            raise McpError(-32602, f"Invalid regular expression: {exc}") from exc

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

# ---------------------------------------------------------------------------
# MCP dispatcher
# ---------------------------------------------------------------------------

class McpServer:
    def __init__(self, tools: CodeIndexTools) -> None:
        self.tools = tools
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
        )

    server = McpServer(tools)
    server.run()


if __name__ == "__main__":
    main()
