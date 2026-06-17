from __future__ import annotations

from typing import Any

from cpp_function_graph_model import (
    BEHAVIOR_CLAIMS_ALLOWED,
    SOURCE_STRUCTURE_CLAIM_STRENGTH,
    FunctionAstExtract,
    FunctionGraphEdge,
    FunctionVisibilityContext,
)


RESOLVER_VERSION = "cpp-function-graph-resolver-v0.1"
EXTERNAL_QUALIFIER_PREFIXES = ("std::", "wil::", "ATL::", "Microsoft::WRL::")


def resolve_function_graph_edges(
    *,
    ast_extract: FunctionAstExtract,
    visibility: FunctionVisibilityContext,
    include_external: bool = True,
    max_edges: int = 200,
) -> tuple[FunctionGraphEdge, ...]:
    edges: list[FunctionGraphEdge] = []
    for call in ast_extract.calls:
        edge = resolve_call(call, visibility=visibility, include_external=include_external)
        if edge is None:
            continue
        edges.append(edge)
        if len(edges) >= max_edges:
            break
    return tuple(edges)


def resolve_call(
    call: dict[str, Any],
    *,
    visibility: FunctionVisibilityContext,
    include_external: bool = True,
) -> FunctionGraphEdge | None:
    callee = str(call.get("callee") or "")
    call_kind = str(call.get("callKind") or _call_kind(callee))
    if not callee:
        return None

    if call_kind == "qualified":
        candidates = _qualified_candidates(callee, visibility)
        basis = ("qualified_name",)
    elif callee.startswith("this->"):
        candidates = _this_member_candidates(callee, visibility)
        basis = ("this_scope", "current_class")
    elif call_kind == "member":
        candidates = _typed_member_candidates(callee, call, visibility)
        basis = ("member_call_without_object_type",)
    else:
        candidates = _unqualified_candidates(callee, visibility)
        basis = ("unqualified_lookup",)

    if len(candidates) == 1:
        candidate = candidates[0]
        status = "exact" if call_kind == "qualified" or callee.startswith("this->") else "probable"
        return FunctionGraphEdge(
            from_symbol_id=visibility.function_symbol_id,
            edge_kind="calls_resolved" if status == "exact" else "calls_candidate",
            to_text=callee,
            to_symbol_id=str(candidate.get("symbolId") or ""),
            resolution_status=status,
            confidence=0.95 if status == "exact" else 0.82,
            basis=tuple(_candidate_basis(candidate, basis)),
            candidates=(_candidate_ref(candidate),),
            claim_strength=SOURCE_STRUCTURE_CLAIM_STRENGTH,
            behavior_claims_allowed=BEHAVIOR_CLAIMS_ALLOWED,
        )

    if len(candidates) > 1:
        return FunctionGraphEdge(
            from_symbol_id=visibility.function_symbol_id,
            edge_kind="calls_ambiguous",
            to_text=callee,
            to_symbol_id=None,
            resolution_status="ambiguous",
            confidence=max(float(candidate.get("_score") or 0.5) for candidate in candidates),
            basis=tuple(_merge_basis(candidates, basis)),
            candidates=tuple(_candidate_ref(candidate) for candidate in _score_candidates(candidates, call)),
            claim_strength=SOURCE_STRUCTURE_CLAIM_STRENGTH,
            behavior_claims_allowed=BEHAVIOR_CLAIMS_ALLOWED,
        )

    if call_kind == "member":
        return FunctionGraphEdge(
            from_symbol_id=visibility.function_symbol_id,
            edge_kind="calls_unresolved",
            to_text=callee,
            to_symbol_id=None,
            resolution_status="unresolved",
            confidence=0.0,
            basis=basis,
            claim_strength=SOURCE_STRUCTURE_CLAIM_STRENGTH,
            behavior_claims_allowed=BEHAVIOR_CLAIMS_ALLOWED,
        )

    if not include_external:
        return None

    return FunctionGraphEdge(
        from_symbol_id=visibility.function_symbol_id,
        edge_kind="calls_external",
        to_text=callee,
        to_symbol_id=None,
        resolution_status="external",
        confidence=0.0,
        basis=("not_in_project_symbol_index",),
        claim_strength=SOURCE_STRUCTURE_CLAIM_STRENGTH,
        behavior_claims_allowed=BEHAVIOR_CLAIMS_ALLOWED,
    )


def _qualified_candidates(callee: str, visibility: FunctionVisibilityContext) -> tuple[dict[str, Any], ...]:
    expanded = _expand_namespace_alias(callee, visibility)
    return _dedupe_candidates(
        _with_basis(symbol, ("qualified_name", "namespace_alias" if expanded != callee else ""))
        for symbol in _all_project_symbols(visibility)
        if str(symbol.get("qualifiedName") or "") == expanded
    )


def _this_member_candidates(callee: str, visibility: FunctionVisibilityContext) -> tuple[dict[str, Any], ...]:
    member_name = callee.rsplit("->", 1)[-1].rsplit(".", 1)[-1]
    return _dedupe_candidates(
        _with_basis(symbol, ("this_scope", "current_class"))
        for symbol in visibility.same_file_symbols
        if _short_name(symbol) == member_name
        and _same_container(symbol, visibility.current_class_name)
    )


def _typed_member_candidates(
    callee: str,
    call: dict[str, Any],
    visibility: FunctionVisibilityContext,
) -> tuple[dict[str, Any], ...]:
    if "->" in callee:
        object_name, member_name = callee.split("->", 1)
    elif "." in callee:
        object_name, member_name = callee.split(".", 1)
    else:
        return ()

    type_name = _local_type_for_object(object_name, visibility)
    if not type_name:
        return ()

    candidates = []
    for symbol in _all_project_symbols(visibility):
        if _short_name(symbol) != member_name:
            continue
        if _same_container(symbol, type_name):
            candidates.append(_with_basis(symbol, ("local_type_hint", "member_call")))
    return _dedupe_candidates(_score_candidates(candidates, call))


def _unqualified_candidates(callee: str, visibility: FunctionVisibilityContext) -> tuple[dict[str, Any], ...]:
    tail = _callee_tail(callee)
    same_scope_candidates: list[dict[str, Any]] = []
    using_candidates: list[dict[str, Any]] = []
    same_file_candidates: list[dict[str, Any]] = []

    for symbol in visibility.same_file_symbols:
        if _short_name(symbol) != tail:
            continue
        if _same_container(symbol, visibility.current_class_name):
            candidate = dict(symbol)
            candidate["_basis"] = ("same_class", "same_file")
            same_scope_candidates.append(candidate)
        elif _same_namespace(symbol, visibility):
            candidate = dict(symbol)
            candidate["_basis"] = ("same_namespace", "same_file")
            same_scope_candidates.append(candidate)
        elif _matches_using_declaration(symbol, tail, visibility):
            candidate = dict(symbol)
            candidate["_basis"] = ("using_declaration",)
            using_candidates.append(candidate)
        elif _matches_using_directive(symbol, visibility):
            candidate = dict(symbol)
            candidate["_basis"] = ("using_namespace",)
            using_candidates.append(candidate)
        else:
            candidate = dict(symbol)
            candidate["_basis"] = ("same_file",)
            same_file_candidates.append(candidate)

    if same_scope_candidates:
        return _dedupe_candidates(same_scope_candidates)

    if using_candidates:
        return _dedupe_candidates(using_candidates)

    if same_file_candidates:
        return _dedupe_candidates(same_file_candidates)

    module_candidates: list[dict[str, Any]] = []
    for symbol in visibility.visible_exported_symbols:
        if _short_name(symbol) == tail:
            candidate = dict(symbol)
            candidate["_basis"] = ("module_visible",)
            module_candidates.append(candidate)

    return _dedupe_candidates(module_candidates)


def _all_project_symbols(visibility: FunctionVisibilityContext) -> tuple[dict[str, Any], ...]:
    return tuple(visibility.same_file_symbols) + tuple(visibility.visible_exported_symbols)


def _dedupe_candidates(candidates: Any) -> tuple[dict[str, Any], ...]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for candidate in candidates:
        symbol_id = str(candidate.get("symbolId") or "")
        if not symbol_id or symbol_id in seen:
            continue
        seen.add(symbol_id)
        result.append(dict(candidate))
    return tuple(result)


def _score_candidates(candidates: Any, call: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    result = []
    for candidate in candidates:
        scored = dict(candidate)
        basis = list(_candidate_basis(scored, ()))
        score = 0.5
        basis_weights = {
            "qualified_name": 0.35,
            "this_scope": 0.35,
            "current_class": 0.3,
            "same_class": 0.25,
            "same_namespace": 0.18,
            "using_declaration": 0.16,
            "using_namespace": 0.1,
            "local_type_hint": 0.22,
            "member_call": 0.08,
            "same_file": 0.08,
            "module_visible": 0.05,
            "namespace_alias": 0.04,
        }
        for item in basis:
            score += basis_weights.get(str(item), 0.0)

        arity = call.get("argumentCount")
        if isinstance(arity, int):
            signature_arity = _signature_arity(str(candidate.get("signature") or ""))
            if signature_arity is not None and signature_arity == arity:
                score += 0.12
                if "arity_match" not in basis:
                    basis.append("arity_match")
            elif signature_arity is not None:
                score -= 0.08
                if "arity_mismatch" not in basis:
                    basis.append("arity_mismatch")

        scored["_score"] = max(0.0, min(score, 0.99))
        scored["_basis"] = tuple(item for item in basis if item)
        result.append(scored)

    return tuple(sorted(result, key=lambda item: float(item.get("_score") or 0.0), reverse=True))


def _candidate_basis(candidate: dict[str, Any], fallback: tuple[str, ...]) -> tuple[str, ...]:
    basis = candidate.get("_basis")
    if isinstance(basis, tuple):
        return basis
    return fallback


def _candidate_ref(candidate: dict[str, Any]) -> dict[str, Any]:
    result = {
        key: candidate.get(key)
        for key in (
            "symbolId",
            "shortName",
            "qualifiedName",
            "container",
            "type",
            "relativePath",
            "startLine",
            "endLine",
            "signature",
        )
        if key in candidate
    }
    if "_score" in candidate:
        result["score"] = round(float(candidate["_score"]), 3)
    basis = _candidate_basis(candidate, ())
    if basis:
        result["basis"] = list(basis)
    return result


def _short_name(symbol: dict[str, Any]) -> str:
    return str(symbol.get("shortName") or symbol.get("name") or "")


def _same_container(symbol: dict[str, Any], container: str | None) -> bool:
    if not container:
        return False
    symbol_container = str(symbol.get("container") or "")
    return symbol_container.casefold() == container.casefold()


def _same_namespace(symbol: dict[str, Any], visibility: FunctionVisibilityContext) -> bool:
    namespace = "::".join(visibility.current_namespace)
    if not namespace:
        return False
    symbol_container = str(symbol.get("container") or "")
    return symbol_container.casefold() == namespace.casefold()


def _matches_using_declaration(
    symbol: dict[str, Any],
    tail: str,
    visibility: FunctionVisibilityContext,
) -> bool:
    qualified_name = str(symbol.get("qualifiedName") or "")
    short_name = _short_name(symbol)
    for item in visibility.using_declarations:
        target = str(item.get("target") or item.get("targetName") or item.get("qualifiedName") or "")
        name = str(item.get("name") or target.rsplit("::", 1)[-1])
        if name == tail and (target == qualified_name or (not target and short_name == tail)):
            return True
    return False


def _matches_using_directive(symbol: dict[str, Any], visibility: FunctionVisibilityContext) -> bool:
    container = str(symbol.get("container") or "")
    if not container:
        qualified_name = str(symbol.get("qualifiedName") or "")
        container = qualified_name.rsplit("::", 1)[0] if "::" in qualified_name else ""
    container_folded = container.casefold()
    for item in visibility.using_directives:
        namespace = str(item.get("namespace") or item.get("target") or item.get("targetName") or "")
        if namespace and container_folded == namespace.casefold():
            return True
    return False


def _expand_namespace_alias(callee: str, visibility: FunctionVisibilityContext) -> str:
    if "::" not in callee:
        return callee
    alias, tail = callee.split("::", 1)
    for item in visibility.namespace_aliases:
        item_alias = str(item.get("alias") or item.get("name") or "")
        target = str(item.get("target") or item.get("targetName") or item.get("namespace") or "")
        if alias == item_alias and target:
            return f"{target}::{tail}"
    return callee


def _with_basis(symbol: dict[str, Any], basis: tuple[str, ...]) -> dict[str, Any]:
    result = dict(symbol)
    result["_basis"] = tuple(item for item in basis if item)
    return result


def _merge_basis(candidates: tuple[dict[str, Any], ...], fallback: tuple[str, ...]) -> tuple[str, ...]:
    result: list[str] = []
    for item in fallback:
        if item and item not in result:
            result.append(item)
    for candidate in candidates:
        for item in _candidate_basis(candidate, ()):
            if item and item not in result:
                result.append(item)
    return tuple(result)


def _local_type_for_object(object_name: str, visibility: FunctionVisibilityContext) -> str | None:
    normalized = object_name.strip("*& ")
    for item in visibility.local_declarations:
        if str(item.get("name") or "") == normalized:
            return _normalize_type_name(str(item.get("typeText") or ""))
    return None


def _normalize_type_name(type_text: str) -> str | None:
    text = type_text.replace("const ", "").replace("*", "").replace("&", "").strip()
    return text or None


def _signature_arity(signature: str) -> int | None:
    open_index = signature.find("(")
    close_index = signature.rfind(")")
    if open_index < 0 or close_index < open_index:
        return None
    args = signature[open_index + 1:close_index].strip()
    if not args or args == "void":
        return 0
    depth = 0
    count = 1
    for char in args:
        if char in "([{<":
            depth += 1
        elif char in ")]}>" and depth > 0:
            depth -= 1
        elif char == "," and depth == 0:
            count += 1
    return count


def _callee_tail(callee: str) -> str:
    return callee.rsplit("::", 1)[-1].rsplit("->", 1)[-1].rsplit(".", 1)[-1]


def _call_kind(callee: str) -> str:
    if "->" in callee or "." in callee:
        return "member"
    if "::" in callee:
        return "qualified"
    return "unqualified"
