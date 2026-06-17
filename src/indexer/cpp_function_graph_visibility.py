from __future__ import annotations

from typing import Any

from cpp_function_graph_model import FunctionAstExtract, FunctionSourceSlice, FunctionVisibilityContext
from cpp_project_index import CODE_ENTITY_CALLABLE_SYMBOL_TYPES, CODE_ENTITY_TYPE_SYMBOL_TYPES


VISIBILITY_CONTEXT_VERSION = "cpp-function-graph-visibility-v0.1"


def build_function_visibility_context(
    *,
    index: Any,
    source: FunctionSourceSlice,
    ast_extract: FunctionAstExtract | None = None,
) -> FunctionVisibilityContext:
    function_symbol = _symbol_by_id(index, source.symbol_id)
    file_symbols = _symbols_for_file(index, source.file_id)
    file_data = _data_for_file(index, source.file_id)
    current_class_name = _current_class_name(function_symbol)
    current_namespace = _namespace_parts(function_symbol, current_class_name=current_class_name)
    current_class_symbol = _find_current_class_symbol(file_symbols, current_class_name)
    imported_modules = _module_names_for_file(index, source.file_id)
    visible_exported_symbols = _visible_module_symbols(index, imported_modules, source.file_id)
    member_data = _member_data(file_data, current_class_name)
    using_declarations = _scope_items_for_file(index, "using_declarations", source.file_id, source.start_line)
    using_directives = _scope_items_for_file(index, "using_directives", source.file_id, source.start_line)
    namespace_aliases = _scope_items_for_file(index, "namespace_aliases", source.file_id, source.start_line)

    return FunctionVisibilityContext(
        file_id=source.file_id,
        file_path=source.relative_path,
        function_symbol_id=source.symbol_id,
        current_namespace=tuple(current_namespace),
        current_class_symbol_id=(
            str(current_class_symbol.get("symbolId"))
            if current_class_symbol is not None and current_class_symbol.get("symbolId") is not None
            else None
        ),
        current_class_name=current_class_name,
        imported_modules=tuple(imported_modules),
        visible_exported_symbols=tuple(_compact_symbol(item) for item in visible_exported_symbols),
        same_file_symbols=tuple(_compact_symbol(item) for item in file_symbols),
        same_file_data=tuple(_compact_data(item) for item in file_data),
        member_data=tuple(_compact_data(item) for item in member_data),
        using_declarations=tuple(_compact_scope_item(item) for item in using_declarations),
        using_directives=tuple(_compact_scope_item(item) for item in using_directives),
        namespace_aliases=tuple(_compact_scope_item(item) for item in namespace_aliases),
        local_declarations=tuple(ast_extract.local_declarations) if ast_extract is not None else (),
    )


def _symbol_by_id(index: Any, symbol_id: str) -> dict[str, Any]:
    symbol = index.symbol_by_id.get(symbol_id)
    return dict(symbol) if symbol is not None else {}


def _symbols_for_file(index: Any, file_id: str) -> list[dict[str, Any]]:
    if getattr(index, "uses_sqlite", False) and hasattr(index, "sqlite_symbols_for_file"):
        return [dict(item) for item in index.sqlite_symbols_for_file(file_id)]

    return [
        dict(item)
        for item in getattr(index, "symbols", [])
        if item.get("fileId") == file_id
    ]


def _data_for_file(index: Any, file_id: str) -> list[dict[str, Any]]:
    if getattr(index, "uses_sqlite", False) and hasattr(index, "sqlite_data_for_file"):
        return [dict(item) for item in index.sqlite_data_for_file(file_id)]

    return [
        dict(item)
        for item in getattr(index, "data", [])
        if item.get("fileId") == file_id
    ]


def _current_class_name(function_symbol: dict[str, Any]) -> str | None:
    container = str(function_symbol.get("container") or "")
    if not container:
        qualified_name = str(function_symbol.get("qualifiedName") or "")
        if "::" not in qualified_name:
            return None
        container = qualified_name.rsplit("::", 1)[0]

    return container or None


def _namespace_parts(function_symbol: dict[str, Any], *, current_class_name: str | None) -> list[str]:
    container = str(function_symbol.get("container") or "")
    if not container and current_class_name:
        container = current_class_name

    if not container:
        return []

    parts = [part for part in container.split("::") if part]
    if current_class_name and parts and container == current_class_name:
        return parts[:-1]
    return parts


def _find_current_class_symbol(
    file_symbols: list[dict[str, Any]],
    current_class_name: str | None,
) -> dict[str, Any] | None:
    if not current_class_name:
        return None

    current_class_tail = current_class_name.rsplit("::", 1)[-1]
    for symbol in file_symbols:
        symbol_type = str(symbol.get("type") or "")
        if symbol_type not in CODE_ENTITY_TYPE_SYMBOL_TYPES:
            continue
        qualified_name = str(symbol.get("qualifiedName") or symbol.get("shortName") or "")
        short_name = str(symbol.get("shortName") or "")
        if qualified_name == current_class_name or short_name == current_class_tail:
            return symbol

    return None


def _module_names_for_file(index: Any, file_id: str) -> list[str]:
    modules = getattr(index, "modules", {}) or {}
    result = [
        str(module_name)
        for module_name, file_ids in modules.items()
        if file_id in file_ids
    ]
    return sorted(result, key=str.casefold)


def _visible_module_symbols(index: Any, module_names: list[str], current_file_id: str) -> list[dict[str, Any]]:
    if not module_names:
        return []

    file_ids: set[str] = set()
    modules = getattr(index, "modules", {}) or {}
    for module_name in module_names:
        file_ids.update(str(file_id) for file_id in modules.get(module_name, []))
    file_ids.discard(current_file_id)

    result: list[dict[str, Any]] = []
    for file_id in sorted(file_ids):
        result.extend(
            symbol
            for symbol in _symbols_for_file(index, file_id)
            if str(symbol.get("type") or "") in CODE_ENTITY_CALLABLE_SYMBOL_TYPES | CODE_ENTITY_TYPE_SYMBOL_TYPES
        )
    return result


def _member_data(file_data: list[dict[str, Any]], current_class_name: str | None) -> list[dict[str, Any]]:
    if not current_class_name:
        return []

    current_class_folded = current_class_name.casefold()
    current_class_tail = current_class_name.rsplit("::", 1)[-1].casefold()
    result: list[dict[str, Any]] = []
    for item in file_data:
        container = str(item.get("container") or "")
        if container.casefold() in {current_class_folded, current_class_tail} or container.casefold().endswith("::" + current_class_tail):
            result.append(item)
    return result


def _scope_items_for_file(index: Any, attr_name: str, file_id: str, line: int) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for item in getattr(index, attr_name, []) or []:
        if str(item.get("fileId") or "") != file_id:
            continue
        active_from = int(item.get("activeFromLine") or item.get("startLine") or 1)
        active_to = int(item.get("activeToLine") or item.get("endLine") or 2**31 - 1)
        if active_from <= line <= active_to:
            result.append(dict(item))
    return result


def _compact_symbol(symbol: dict[str, Any]) -> dict[str, Any]:
    fields = (
        "symbolId",
        "type",
        "shortName",
        "qualifiedName",
        "container",
        "relativePath",
        "startLine",
        "endLine",
        "signature",
    )
    return {field: symbol.get(field) for field in fields if field in symbol}


def _compact_data(item: dict[str, Any]) -> dict[str, Any]:
    fields = (
        "dataId",
        "declarationKind",
        "scopeKind",
        "name",
        "shortName",
        "qualifiedName",
        "container",
        "typeText",
        "relativePath",
        "startLine",
        "endLine",
        "signature",
    )
    return {field: item.get(field) for field in fields if field in item}


def _compact_scope_item(item: dict[str, Any]) -> dict[str, Any]:
    fields = (
        "name",
        "target",
        "targetName",
        "namespace",
        "alias",
        "qualifiedName",
        "fileId",
        "relativePath",
        "startLine",
        "endLine",
        "activeFromLine",
        "activeToLine",
    )
    return {field: item.get(field) for field in fields if field in item}
