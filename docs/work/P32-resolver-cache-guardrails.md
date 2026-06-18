# P32 Resolver Cache Guardrails

Date: 2026-06-18
Workplan: `docs/workplans/function-graph-runtime-quality-parser-adapter-vnext.md`
Status: Implemented

## Implemented

- Added parser status/capability fingerprinting to Function Graph cache compatibility.
- Cache-only requests now miss when parser status changes even if parser id/version text remains the same.
- Graph fingerprints record parser status payload and fingerprint internally.

## Owner

- `src/indexer/cpp_function_graph_service.py`
- `src/indexer/cpp_function_graph_parser.py`

## Verification

- Source-service cache test covers parser-status cache misses.
- Existing cache option tests continue to guard option-sensitive cache behavior.

## Non-Goals

- No behavior claims.
- No dynamic dispatch certainty.
- No external API semantic resolution.
