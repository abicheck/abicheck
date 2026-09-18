### Added

- `dump` and `compare` accept a repeatable `-D/--define NAME[=VALUE]`
  (ADR-074): a narrow, per-invocation *logical preprocessor definition* for
  the L2 header parse, for libraries whose public surface is gated behind a
  feature macro (`-DPVXS_ENABLE_EXPERT_API`, `-DPCRE2_CODE_UNIT_WIDTH=8`).
  It applies to both sides of a `compare` identically — there is no
  `old=`/`new=` form — and merges with `.abicheck.yml`'s `compile.defines`
  **by macro name**, so a CLI definition overrides that macro only and every
  other configured define stays in force. `compile.defines` remains the
  recommendation for stable CI and baseline generation. This is not a return
  of `--gcc-options`/`--compiler-option`: the operand is a macro definition,
  renders to exactly one `-D`-prefixed argv token, and an operand whose *name
  component* is not a bare C identifier is a usage error (`NAME=VALUE` is of
  course accepted); general compiler flags remain config-only under
  `compile.options`.

  The definitions are honored by every front end that can run an L2 header
  parse -- `dump`, `compare` (including `--no-baseline` and the
  directory/package release fan-out), and the typed
  `DumpRequest`/`CompareRequest` API via `CompileContext.defines`.

### Changed

- The "a header requires macro X" diagnostic now recommends `-DX` and
  `.abicheck.yml`'s `compile.defines:` instead of the long-removed
  `--compiler-option`. The clang header-parse failure message and the
  conflicting-target-triple warning lost their stale `--compiler-option`/
  `--gcc-options` advice too.
