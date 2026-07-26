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
- The real production DPC++ decode path still read the entire concatenated
  host+device stream into one Python `str` (`ast_path.read_text()`) before
  any parsing began, even after the fixes above stopped retaining every
  *parsed* document — a multi-pass DPC++ capture's combined raw text is
  itself a multiple of any single pass's already-multi-GB size. New
  `sycl_context.decode_and_select_frontend_context_from_path` reads the
  file incrementally instead, bounding peak buffered text to roughly one
  document's size; `dumper_clang_errors._parse_clang_ast_result` now calls
  it instead of pre-reading the file. The cache-write side of the same
  path also gained `dumper_cache._atomic_write_json`, which streams
  `json.dump` straight to the cache file instead of building a full
  `json.dumps(...).encode()` blob first (Codex review, second round).
- `dumper._header_ast_parser`'s castxml/device rejection guard fired for
  *any* request that merely resolved to `"castxml"` — including the
  default `--ast-frontend auto` — instead of only an explicit
  `--ast-frontend castxml`, contradicting the function's own documented
  "under auto a non-host request skips castxml entirely" contract. A
  plain `--frontend-context device` with no `--ast-frontend` given at all
  was incorrectly rejected before ever reaching the clang backend that
  could satisfy it. Now checks the literal `backend` argument (not just
  its resolved value) so only an explicit castxml request is rejected
  (Codex review).
- `dumper_cache._atomic_write`/`_atomic_write_json` lacked coverage for the
  doubly-defensive path where the cleanup `os.unlink()` of the staging file
  itself fails after `os.replace()` already failed (only `_atomic_copy` had
  this test) — added, closing a Codecov-flagged patch-coverage gap (no
  behavior change).
- `dumper._dump_elf` backfilled a device-context (SYCL/DPC++) snapshot's
  header-parsed types with the *host* binary's own DWARF layout — DWARF
  describes the host-compiled binary's actual memory layout, meaningless
  for declarations parsed from a device-target AST pass, which can target
  a genuinely different architecture/ABI (different sizes/alignment/
  offsets). Now skips DWARF backfill entirely for a device-context dump,
  and reports `dwarf_layout_coherence` as `None` ("not applicable"), not
  `"unavailable"` (which would wrongly imply DWARF was consulted and
  simply absent) (Codex review).
- `clang_layout_tool.attach_clang_layout` (the optional G28 Phase 4 layout
  companion tool) had the identical gap in a separate mechanism: it
  re-compiles headers with ordinary flags (no concept of SYCL/DPC++ host/
  device context at all), so it could backfill a device-context snapshot's
  missing layout fields from its own host-compiled output. Now skips
  entirely for a device-context snapshot, verified via a
  `find_layout_tool_bin` spy that must never be reached (Codex review).
- The native `dump` CLI command's ELF path (`cli_dump_helpers.
  perform_elf_dump`) bypasses `service.run_dump` (which already threads
  `compile.frontend_context` into `dumper.dump` for the `scan`/`compare`/
  PE/Mach-O paths) and calls `dumper.dump` directly — that direct call
  omitted `frontend_context` entirely, so `dump --frontend-context device`
  silently produced an ordinary host-context snapshot instead of forwarding
  the request. Fixed by threading `compile_context.frontend_context` (or
  `"host"` when no compile context was resolved) into that call too (Codex
  review).
- `sycl_context._iter_json_documents`'s document-boundary scan retried
  `json.JSONDecoder.raw_decode` from position 0 over an ever-growing `str`
  on every incomplete chunk, and grew that same `str` via `buf += chunk` —
  both re-process/re-copy the *entire* accumulated buffer each time, making
  a single document spanning more than one chunk quadratic in its own size
  (measured directly: a ~4MB document in 4KiB chunks took ~6.7s pre-fix,
  ~1.6s at half that size, ~26s at double — textbook quadratic growth).
  Exactly the case this module exists to support, since a single DPC++
  pass's AST can itself be hundreds of MB to multi-GB. Rewritten as a
  hand-rolled incremental bracket/string-escape scan over a list of
  not-yet-consumed chunks (each byte inspected exactly once across the
  whole stream) that materializes and `json.loads`-parses a document's text
  exactly once, when its closing bracket is found — down to ~0.7s at the
  same ~4MB size. The new regression test proves this via a deterministic
  `json.loads` call-count assertion rather than wall-clock timing, which
  turned out fragile: this scan is a Python-level per-character loop, and a
  trace-based (non-`sys.monitoring`) coverage backend slows it down enough
  on its own to erase a hand-picked timing margin, while the old
  C-level-dominated cost was largely unaffected by the same tracing (Codex
  review).
- `service_header_scoped._try_header_scoped_dump`'s broad `except Exception`
  (which exists to fall back to export-table mode when a PE/Mach-O header
  backend is merely unavailable) also caught `AstContextMissingError`/
  `AstContextAmbiguousError` — both only ever raised in response to a
  non-`"host"` `--frontend-context` request (there is no `"device"`
  default), so catching them here silently discarded an explicit device-
  context request's failure and succeeded anyway with `--header`/
  `--include` ignored. Now re-raises both, mirroring this same function's
  existing `deadline.DeadlineExceeded` propagation for the identical reason
  (Codex review).
- `AbiSnapshot.frontend_context_kind` was added without bumping
  `serialization.SCHEMA_VERSION` — every other purely-additive snapshot
  field from v9 onward (including this same PR's own `dwarf_layout_
  coherence`/`dwarf_layout_coherence_mismatches` at v16) got its own bump
  specifically so a pre-bump reader gets `snapshot_from_dict`'s usual
  version-mismatch `UserWarning` instead of silently discarding the new
  field on re-save. Bumped to `SCHEMA_VERSION = 17` (Codex review).
- `service_dump_cache._manifest_cache_paths`/`manifest_tu_scope_field`
  together only ever see a manifest's `roots` as membership in a
  deduplicated, role-blind flattened header list shared with every TU's
  `forced_includes` — a header already reachable via `forced_includes`
  that is (or isn't) *also* declared a `root` produces an identical
  flattened set either way. But `roots` is what `dumper.dump` uses as the
  manifest's own declared-surface `headers`, driving provenance/public
  classification independently of file content, so two such manifests
  could hash identically and share a cached snapshot classified under the
  *other* manifest's declared surface. New `_manifest_roots` folds the
  manifest's own ordered `roots` list into the whole-snapshot cache key as
  its own component (Codex review).
- `dumper._header_ast_parser`'s castxml/device rejection guard only checked
  the literal `--ast-frontend` flag, so `ABICHECK_AST_FRONTEND=castxml`
  pinning a bare `--ast-frontend auto` request was treated the same as an
  unpinned `auto` — silently falling through to the clang backend instead
  of being rejected the same way an explicit `--ast-frontend castxml`
  would be, contradicting `docs/reference/environment.md`'s own "pins the
  AST frontend when the request is auto ... honoured verbatim" contract.
  Now also rejects when the env var pins `castxml` (Codex review).
- `cli_dump_helpers.render_dump_dry_run`'s manifest-mode
  `compute_extraction_contract` call omitted both `manifest_tu_scope` and
  `declared_includes` — the real (non-dry) manifest dump path
  (`dumper_contract._attach_extraction_contract`) supplies both, so the
  dry-run's printed `scope_fingerprint` preview fell back to the legacy
  field set and could never match what a real extraction would actually
  compute, defeating the point of a "preview the extraction contract"
  dry-run (Codex/CodeRabbit review).
- `dump_manifest.py`'s `frontend_context` validation error claimed
  `"device"` was "ADR-050 Phase D (G32), not yet implemented" — stale
  wording left over from before Phase D shipped; `_SUPPORTED_FRONTEND_
  CONTEXTS` already accepts `"device"` (CodeRabbit review).
- `dumper_manifest.py`'s per-TU pooled extraction loop called
  `_handle_failure(tu)` twice on a required TU's failure — once
  immediately after cancelling in-flight futures, and again unconditionally
  right after, even though the first call's bare `raise` already
  propagates before the second could run. Harmless today but confusing;
  removed the redundant first call (CodeRabbit review).
- `tu_merge.merge_fragments` validated cross-fragment `ast_producer`
  consistency but blindly copied `ordered[0].frontend_context_kind`
  without the same check — extended the existing `HETEROGENEOUS_ABI_
  CONTEXT` guard to cover `frontend_context_kind` too, so a manifest
  merge that ever saw diverging per-TU host/device resolution fails loudly
  instead of silently misrepresenting the merged snapshot's provenance
  (CodeRabbit review).
- `sycl_context._select_from_document_stream`'s selection loop always
  `json.loads`-parsed every document in a multi-pass DPC++ stream into a
  full dict, even ones it already knew (positionally, from `stderr`'s own
  `-cc1` invocation lines, before ever looking at the document's content)
  could not possibly match the requested `frontend_context` kind. A
  non-matching multi-GB pass's dict was therefore built and briefly live in
  memory *at the same time* as an already-selected multi-GB match's dict —
  real peak-memory doubling on exactly the large DPC++ captures this module
  exists to support. `_iter_json_documents` now yields each document's raw
  text instead of a parsed dict, and the selector only calls `json.loads`
  when a document's kind matches the request (or has no correlated
  invocation at all, so its kind can't be ruled out in advance) — a
  definitely-non-matching document is never parsed, not even transiently
  (Codex review).
- `service._attach_header_graph`'s per-header `clang -M` include-closure
  pass (`buildsource.header_graph.ClangHeaderIncludeExtractor`) had no
  `frontend_context`/SYCL concept at all — unlike the semantic AST pass in
  the same function, which already threads `frontend_context` through. A
  device-context dump's include pass would therefore resolve
  `__SYCL_DEVICE_ONLY__`-style guards as host and attach host-only include
  edges to a device snapshot's graph. Rather than guess at unvalidated
  `-fsycl`/`-fsycl-is-device` flags for a one-shot `-M` invocation (no real
  captured evidence exists for how that behaves, unlike the AST decoder's
  real DPC++ capture), the include pass is now skipped entirely for a
  non-host `frontend_context` — the same host/device tradeoff already made
  for DWARF layout backfill (`dumper._dump_elf`) and the clang layout tool:
  honestly "not collected" rather than confidently wrong (Codex review).
