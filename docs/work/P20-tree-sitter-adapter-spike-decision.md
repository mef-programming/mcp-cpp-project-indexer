# P20 Tree-sitter Adapter Spike Decision

Implemented phase: Function Graph Accuracy and Maintenance Slice P20.

## Changed

- The optional Tree-sitter adapter remains gated behind dependency/status checks.
- The lightweight parser remains the default parser for normal service execution.
- Tree-sitter extraction parity is still required before enabling the adapter when optional dependencies are present.

## Runtime Ownership

Parser selection remains internal to `src/indexer`.

## Verification

- Existing adapter-unavailable tests continue to assert that optional Tree-sitter is not required.

## Non-Goals Respected

No hard dependency and no second parser loop.
