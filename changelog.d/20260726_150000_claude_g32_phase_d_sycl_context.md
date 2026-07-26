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

### Fixed

- The whole-snapshot dump cache key for a `--dump-manifest` dump did not
  include the manifest's own `frontend_context` — a manifest changed only
  from `host` to `device` (or vice versa) could silently return a stale
  cached snapshot from the *other* context instead of running the selector
  (Codex review).
- `snapshot_from_dict` never read back `AbiSnapshot.frontend_context_kind`,
  so a persisted or cache-hit host/device snapshot silently lost the tag on
  every save/load round-trip even though `snapshot_to_dict` already wrote it
  (Codex review).
- `abicheck.sycl_context` gained `decode_and_select_frontend_context`, a
  fused decode+select entry point used by the real `dumper_clang_errors`
  production path: it never retains a non-matching pass's full parsed AST
  tree, unlike the separate decode-then-select two-step, which built every
  document (including every one the caller was about to discard) up front —
  a real memory-multiplication risk for a DPC++ header, whose per-pass AST
  dump can itself reach multi-GB size (Codex review).
- `dumper.dump()`'s own hybrid recursion (`--ast-frontend hybrid`, used by
  direct Python-API callers) did not forward `frontend_context` to either
  of its two recursive castxml/clang sub-dumps, so a `frontend_context=
  "device"` request silently defaulted both to `"host"` instead of failing
  — hybrid merges castxml+clang and castxml has no device-context concept,
  so this combination is now rejected explicitly before the hybrid
  dispatch (Codex review).
- `service.py`'s internal semantic header graph (G29 Phase A) was always
  built from a hardcoded `"host"` AST pass regardless of the primary
  snapshot's own requested `frontend_context` — a device-context dump's
  embedded graph would combine device declarations with host-only
  call/type/include edges, feeding crosschecks a graph incoherent with what
  it describes. Now threads the same `CompileContext.frontend_context`
  through (Codex review).
- `sycl_context.decode_and_select_frontend_context` accumulated every
  matching document before checking for ambiguity, so two or more
  same-`kind` passes (each themselves potentially multi-GB) could be held
  in memory simultaneously just to report an error that needs neither
  tree. Now raises `AstContextAmbiguousError` as soon as a second match is
  seen, without scanning or retaining anything further (Codex review).
