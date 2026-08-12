<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **L4 source-ABI replay (`abicheck/buildsource/source_extractors/clang.py`)
  broke on a real build's own recorded `-fsycl`**: Intel's oneAPI DPC++/C++
  driver (`icx`/`icpx`/`dpcpp`/`dpcpp-cl`) runs two separate `-cc1` passes
  for one `-fsycl` compile — a SYCL device-side pass and a host-side pass —
  each writing a complete JSON document to the same stdout stream back to
  back with no separator, so a single-document `json.load()` failed with
  `Extra data` at the host/device split (reproduced against a real 2.8GB
  `-fsycl` oneDAL translation unit). `_clang_context_args` now appends
  `-fsycl-host-only` for such a compile unit — collapsing it back to the
  single host-side pass that actually links into the scanned binary —
  mirroring the identical fix already shipped for the L2 header-AST backend
  (`dumper_ast_config._build_clang_header_command`). A leftover multi-document
  stream from any other, not-yet-special-cased offload flag now also raises
  an actionable `SourceExtractionError` naming the likely cause instead of a
  bare byte-offset, matching the L2 backend's own hint. `_clang_context_args`
  was found to gate that flag on `pick_compiler_binary`'s *emulated* compiler
  (which falls back to the compile unit's own recorded `argv[0]`) rather than
  the binary L4 actually invokes (`clang_bin`) — fixed to take `clang_bin`
  explicitly, and `_argv.pick_compiler_binary`'s docstring now documents the
  emulated-vs-invoked distinction generally so a future binary-capability-
  gated flag here doesn't repeat the same mistake; `tests/
  test_source_extractors_clang.py::TestSyclHostOnlyGatedOnInvokedBinary`
  sweeps a matrix of invoked/recorded-`argv[0]` combinations as a regression
  guard for the whole bug class, not just the one reported pair. A
  generalized Hypothesis property test in the same class then found the
  same bug class reachable via a second, different code path: a real
  build's own recorded `-fsycl-host-only`/`-fsycl-device-only` (carried
  through verbatim via `abi_relevant_flags`) reached a non-Intel invoked
  binary unchanged, hitting the identical "unknown argument" failure
  independent of the insertion-side fix above. A first pass at closing this
  stripped a carried-through `-fsycl-host-only` (on the assumption it was
  harmless to drop, since stock clang's bare `-fsycl` "parses fine as one
  ordinary pass") while raising `SourceExtractionError` only for
  `-fsycl-device-only`. A second Codex review round caught that assumption
  itself was wrong: verified empirically against a real clang install, bare
  `-fsycl` with no selector defines `__SYCL_DEVICE_ONLY__` — i.e. it
  compiles as **device** context by default, not host — so dropping just
  `-fsycl-host-only` silently replayed an explicitly host-pinned TU as
  device code, the identical misrepresentation risk already recognized for
  `-fsycl-device-only`. Both Intel-only pass-selector flags are now handled
  identically: a recorded `-fsycl-host-only` OR `-fsycl-device-only` on a
  non-Intel invoked binary raises a clear `SourceExtractionError` for that
  one translation unit (degrading it to partial coverage) instead of ever
  replaying it under a different compilation context than the one actually
  recorded. A bare, unselected `-fsycl` (no explicit pin either way) is
  deliberately left untouched, matching the L2 backend's own established
  accepted approximation for that ambiguous shape.
- **`_argv._carry_abi_relevant_flags` (shared by the castxml and clang
  extractors) silently corrupted last-flag-wins toggle state for a repeated
  flag**: `extract_abi_relevant_flags` preserves every occurrence of a
  matching flag from the real build's argv, in order, with no dedup of its
  own — but the carry-through step deduped a flag against every earlier
  occurrence of the *identical literal token* it had already carried, not
  just against flags already emitted from the structured fields. A layered
  build config recording e.g. `-fno-sycl -fsycl -fno-sycl` (SYCL enabled
  then explicitly disabled again) collapsed to `-fno-sycl -fsycl` — the
  decisive final `-fno-sycl` silently dropped as a "duplicate" of the
  first — reversing the real effective state and, combined with the
  `-fsycl-host-only` insertion fix above, could append `-fsycl-host-only`
  and stamp SYCL-host facts for a translation unit the real build compiled
  *without* SYCL at all (Codex review). Fixed by only checking (never
  updating) the caller's initial "already emitted from structured fields"
  set, so every genuine repeat within `abi_relevant_flags` itself now
  survives in the real build's own order and multiplicity — the same class
  of fix applies to any other toggle-style flag pair sharing one spelling
  (`-fexceptions`/`-fno-exceptions`, …), not just `-fsycl`/`-fno-sycl`.
  This shared carry-through helper is also used by the castxml extractor's
  `build_castxml_command` (via its own `_replay_extra_flags` alias), so a
  persisted baseline produced under the old, deduplicating recipe and a
  fresh castxml extraction now advertise DIFFERENT producer versions rather
  than being silently treated as comparable (Codex review). Bumps
  `CLANG_EXTRACTOR_VERSION` to `0.12` and `CASTXML_EXTRACTOR_VERSION` to
  `0.3`.
