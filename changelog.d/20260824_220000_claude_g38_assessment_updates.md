<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **G38 Phase 13 driver: `compare_release_against_bundle_facts()` now
  forwards `header_backend`/`compile`, and accepts per-library header/
  compile overrides.**

  Two gaps surfaced by an external validation pass against
  `compare_release_against_bundle_facts()` (the Phase 13 stored-vs-live
  bundle driver, `abicheck/bundle_side_input.py`), both real and both fixed:

  1. The driver's `service.resolve_input()` call for the NEW side never
     passed `header_backend`/`compile` at all, so a header-scoped
     comparison always resolved under `header_backend="auto"` with no
     `CompileContext` — on a host with no working castxml (a clang/icpx-only
     host), that dies rather than falling back to a caller's own resolved
     compiler binding/frontend. Both are now forwarded straight through.
  2. `headers`/`includes`/`compile` applied uniformly to every matched
     library, which is only correct when every library in the bundle shares
     one header tree and one compile configuration — not true for a
     mixed-toolchain release (e.g. a plain-C++ library alongside a
     `-fsycl`/`icpx` one). New optional `per_library_headers`/
     `per_library_includes`/`per_library_compile` `{canonical_name: ...}`
     maps are consulted before the uniform fallback per matched library, so
     only the libraries that actually differ need an override. A run using
     only the uniform fallback remains a **cost proof** (the driver
     completes in reasonable time/memory), not a **correctness proof**, for
     a mixed-toolchain bundle — documented explicitly on the function.

  The third gap from the same assessment — no CLI/`.abicheck.yml` surface
  for this driver — remains the already-documented, deliberate Phase 13
  "Known gap": every file that would host the dispatch is within two lines
  of the AI-readiness 2000-line hard cap (see the G38 plan doc's Phase 13
  section). Adoption still needs a committed Python step
  (`compare_release_against_bundle_facts(...)`), not a bare
  `uses: abicheck/abicheck@sha` Action input, until one of those files is
  split.
