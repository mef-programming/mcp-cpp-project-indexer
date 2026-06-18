# Function Graph MCP Tools

The function graph tools expose project-local source structure from indexed C++ function bodies. They are for navigation and structural review, not behavior proof.

## `get_function_body_graph`

Use this first when you need the calls, data accesses, and control-flow markers inside one indexed callable symbol.

Important arguments:

- `symbolId`: indexed callable symbol id.
- `mode`: `compute_if_missing`, `cache_only`, or `refresh`.
- `includeDataAccess`: include read/write data candidate edges.
- `includeControlFlow`: include syntax markers such as `if`, `return`, and loops.
- `includeExternal`: include unresolved non-project calls as `external`.

The response includes parser/resolver versions, cache status, fingerprints, edges, `claimStrength=source_structure_allowed`, and `behaviorClaimsAllowed=false`.

## Xrefs And Neighborhood

`get_call_xrefs_from`, `get_call_xrefs_to`, and `get_symbol_neighborhood` read persisted graph edges only. They do not compute missing graphs. Compute or refresh `get_function_body_graph` for relevant callers before relying on xref completeness.

## Cache Modes

- `compute_if_missing`: reuse a compatible cached graph or compute it.
- `cache_only`: return only compatible cached graphs; otherwise return `cache_miss`.
- `refresh`: recompute and replace persisted edges for that symbol.

Graph cache compatibility includes source fingerprints, parser/resolver versions, visibility fingerprints, and graph options such as data/control/external edge flags and `maxEdges`.

## Cache Maintenance

Normal users should prefer rebuilding the project index when scanner inputs change and `refresh` when one function graph must be recomputed. Internal storage exposes cache stats and parser/resolver-version pruning for maintenance code, but no separate public MCP cache-maintenance tool is required.

Management API operators can use the existing management command endpoint for cache maintenance:

- `function_graph_cache_stats`: returns cache counts, parser/resolver version breakdowns, oldest/newest cache timestamps, and edge counts per stored graph.
- `function_graph_cache_prune_versions`: prunes stale parser/resolver cache versions through the management plane only.

Recommended workflow:

1. Call `function_graph_cache_stats` and inspect version breakdowns.
2. Call `function_graph_cache_prune_versions` with `dryRun=true` or omit `dryRun`, plus `keepParserVersions`, `keepResolverVersions`, or `keepCurrent=true`.
3. If the dry-run counts are expected, call the same command with `dryRun=false`.

Example management command payloads:

```json
{"command":"function_graph_cache_stats"}
```

```json
{"command":"function_graph_cache_prune_versions","keepCurrent":true}
```

```json
{"command":"function_graph_cache_prune_versions","keepCurrent":true,"dryRun":false}
```

Prune requests without explicit keep versions and without `keepCurrent=true` are rejected.

## Claim Contract

Function graph output may say a source structure was observed or a project-local candidate was found. It must not claim runtime behavior, side effects, alias certainty, dynamic dispatch certainty, or external API semantics.
