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
  bare byte-offset, matching the L2 backend's own hint.
