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
  independent of the insertion-side fix above. `_clang_context_args` now
  strips a carried-through `-fsycl-host-only` whenever the invoked binary
  isn't Intel-family — harmless to drop, since stock clang's bare `-fsycl`
  already parses fine as one ordinary host-shaped pass. A carried-through
  `-fsycl-device-only` is handled differently, not stripped: it names a
  genuinely different (device-side) compilation context, so silently
  dropping it would replay the TU as ordinary host code and could fabricate
  false L4 findings rather than honestly degrade coverage — it now raises a
  clear `SourceExtractionError` for that one translation unit instead
  (Codex review; bumping `CLANG_EXTRACTOR_VERSION` to `0.10`).
