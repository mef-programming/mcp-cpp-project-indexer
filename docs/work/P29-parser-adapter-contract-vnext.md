# P29 Parser Adapter Contract vNext

Date: 2026-06-18
Workplan: `docs/workplans/function-graph-runtime-quality-parser-adapter-vnext.md`
Status: Implemented

## Implemented

- Added parser status/capability metadata to the internal parser contract.
- Added stable parser status fingerprints and cache-version helpers.
- Lightweight parser now reports explicit capabilities and remains the default parser.

## Owner

- `src/indexer/cpp_function_graph_parser.py`
- `src/indexer/cpp_function_graph_extract.py`

## Verification

- Targeted parser tests assert status metadata and cache-version fingerprinting.

## Non-Goals

- No server-owned parser decisions.
- No second parser loop in the Function Graph service.
