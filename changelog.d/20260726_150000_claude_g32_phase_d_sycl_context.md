<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Added

- **SYCL/DPC++ host vs. device AST context selection (ADR-050 D5, G32
  Phase D)**: new `abicheck.sycl_context` module decodes a DPC++ frontend's
  possibly-multi-document `-ast-dump=json` output (one document per
  `-cc1` compilation pass — a `host` pass plus one or more `device` passes
  per offload target, concatenated on stdout with no separator) into a
  stream of `{kind, target, ast}` contexts, correlated against the
  driver's own `-cc1 ... -triple <T> ... -fsycl-is-(host|device)`
  invocation lines on `-v` stderr output — the raw AST JSON alone carries
  no host/device label of its own. Selection is always by `kind`, never
  by target-triple pattern matching (diagnostic-only): exactly one
  matching context selects; zero raises the new
  `AstContextMissingError`; more than one shares the same `kind` raises
  the new `AstContextAmbiguousError` (no implicit tiebreaker). Designed
  and tested against a real `icpx` capture (Intel oneAPI DPC++/C++
  Compiler 2026.1.0), not a guessed format — see
  `tests/fixtures/g32/dpcpp/README.md`.
