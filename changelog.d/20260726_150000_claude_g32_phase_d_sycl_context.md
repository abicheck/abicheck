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
- **`sycl_context.py` wired end to end into the extraction pipeline**:
  `--frontend-context device` / a manifest's `frontend_context: device`
  now actually reach the L2 header frontend — Phase B's blanket
  "not supported yet" rejection is lifted in `dump_manifest.py` and
  `cli_options.resolve_compile_context`, and `cli_options.merge_compile_
  config`'s silent drop of `frontend_context` when folding a
  `.abicheck.yml` `compile:` block is fixed. `dumper.py`'s clang path
  detects a DPC++-capable compiler (`icx`/`icpx`/`dpcpp`/`dpcpp-cl`,
  `dumper_clang._is_dpcpp_family_binary`) and, for such an invocation,
  always decodes/selects via `sycl_context` — never falling back to the
  legacy single-document path on an empty/malformed decode, which is
  reserved for an invocation positively identified as non-SYCL up front.
  A non-`host` request against a non-DPC++-capable invocation (whether
  clang or castxml) fails immediately with `AstContextMissingError`
  rather than silently returning the ordinary host AST. The resolved
  `kind` is threaded through the ELF per-TU manifest path
  (`tu_fragment.TuFragment`/`tu_merge.MergedTuFragments`) and the single-
  header PE/Mach-O/ELF paths onto a new `AbiSnapshot.frontend_context_kind`
  field, and folded into `comparability.compute_extraction_contract`'s
  hashed `profile_fingerprint` fields (gated on non-`None`, so every
  pre-Phase-D/non-SYCL dump's fingerprint is unchanged) — closing the gap
  where two extractions requesting different `frontend_context` values
  would otherwise parse genuinely different ASTs invisibly to D2's
  comparability gate. `service.py`'s ELF/PE/Mach-O dump paths and
  `scan`/`dump`/`compare` all thread the resolved `CompileContext.
  frontend_context` through to `dumper.dump`, so `scan --frontend-context
  device` reaches this selector the same way `dump`/`compare` do.
