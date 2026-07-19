<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **`scan --against` no longer advertises unsupported directory/package inputs** —
  the help text and docs claimed `--against` accepted "a previous dump, library,
  directory, or package", but the implementation only ever resolved a single
  file; the option now rejects directories at the CLI level (`dir_okay=False`)
  and its docs point at `abicheck compare OLD_PACKAGE NEW_PACKAGE` for
  directory/package comparisons. `dir_okay=False` only rejects directories,
  not a package *file* (`.deb`/`.rpm`/`.tar.gz`/...); `scan` now explicitly
  rejects a package-shaped `--against` (reusing `package.is_package`'s
  extension/magic-byte detection) with a `UsageError` pointing at the same
  `abicheck compare` guidance, before it could reach `resolve_input()` (which
  cannot extract packages) and fail deep with an opaque "cannot detect input
  format" instead (Codex review).
- **`compare --used-by`/`--required-symbol(s)` JSON `summary` could contradict
  `changes`** — scoped-only findings (e.g. `consumer_required_symbol_removed`,
  `pe_ordinal_retargeted`) and missing-contract labels were folded into the
  JSON `changes` array after `summary` was already computed, so a scoped run
  gated only by one of these synthetic entries could report e.g.
  `"verdict": "BREAKING"` next to `"summary": {"total_changes": 0}`. `summary`
  now reflects the complete `changes` array; the pre-scoped counts move to a
  new `full_summary` key.
- **`dump`'s persisted snapshot and the `actions/baseline` manifest now record
  the actual depth contract** — a `dump_provenance` block
  (`requested_depth`/`effective_depth`/`degraded`/`frontend`/`source_scope`)
  is written into every dumped `.abi.json`'s JSON and threaded into each
  library's manifest artifact entry, so a later reader can tell how deep a
  published snapshot really goes without re-deriving it.
- **`tools/clang-layout-tool`'s CMake project no longer fails on a C-only
  try-compile** — `project(...)` now declares both `C CXX` (some LLVM/Clang
  CMake config packages run a C-language try-compile even for a C++-only
  consumer).
- **`dump --dry-run --depth source` with `--build-info` but no `--sources`
  now blocks instead of reporting success** — a raw `--build-info` compile
  database supplies L3 build context only; L4 source-ABI replay only ever
  runs over a `--sources` tree, so the real (non-dry) dump's strict depth
  gate would hard-fail on this input while the dry run previously exited 0.
- **`dump_provenance`'s `effective_depth` now matches the strict depth
  gate's own verdict** — it previously used the plain (non-gated) evidence
  label, which disagrees with the gate on a zero-match source-only dump (L4
  replay ran but linked nothing); a `--depth source` dump the gate had just
  accepted could serialize `effective_depth: "build", degraded: true`.
- **`-p`/`--compile-db` now reaches PE/Mach-O dumps** — the compile
  database's derived flags and matched signal were resolved only for the
  ELF path; a PE/Mach-O dump silently dropped a `-p` compile database's
  flags entirely, and `--depth build` backed only by `-p` was wrongly
  rejected as having reached only `headers` since
  `snap.parsed_with_build_context` was never stamped there.
- **`dump --dry-run --depth source --build-info <prebuilt pack>` no longer
  blocks incorrectly** — a *pack-shaped* `--build-info` (e.g. from a
  previous `collect` or the `abicheck-cc` wrapper) can carry its own L4
  `source_abi`, which `embed_build_source`'s pack-combine step falls back
  to when no `--sources` pack is given — so `--depth source` can genuinely
  succeed without `--sources` in that case. Only a raw compile database
  (never carrying L4 facts) is still treated as unsatisfiable.
- **`dump --dry-run` now also recognizes a Flow-2 `abicheck_inputs/`
  `--build-info` directory as pack-shaped** — the dry-run's pack detection
  checked only `BuildSourcePack` (`is_pack_dir`), missing the second
  directory kind `embed_build_source` itself already accepts
  (`_is_inputs_pack_dir`, ADR-035 D5); `--depth source --build-info
  <abicheck_inputs>` with no `--sources` was wrongly blocked even though
  the real dump can ingest its L4 facts and succeed.
- **`dump_provenance.frontend` no longer goes silently `null` for a
  headers-less `--sources`/`--build-info` dump** — `ast_producer` is only
  ever stamped by the L2 header-AST pipeline, which a symbol-only ELF dump
  (no `-H`) never reaches even when a real L4 `source_abi:<extractor>`
  replay ran over `--sources`; `frontend` now falls back to the build-source
  pack's extractor ledger so the actual L4 frontend identity (`clang`/
  `castxml`) is still recorded.
- **PE/Mach-O `--depth build` no longer accepts a mangling-fallback dump
  as build-context evidence** — `service._try_header_scoped_dump()` can
  silently fall back to an export-table-only snapshot (e.g. an MSVC-mangled
  C++ DLL parsed with a mismatched compiler); a `-p`/`--compile-db` match
  against the *originally requested* headers was previously stamped onto
  that fallback snapshot regardless, so `dump foo.dll -H api.h -p build
  --depth build` could report success on a snapshot that never actually
  used the headers or the compile database. The stamp is now also gated on
  the returned snapshot being genuinely header-scoped (`from_headers`).
- **Broad suppression could still hide a namespaced public C++
  function/variable break under the default CastXML backend** — the
  mangled-vs-demangled identity recovery added for suppression
  reachability trusted `Function.name`/`Variable.name` already containing
  `"::"`, but CastXML never qualifies those fields with namespace context
  (only the bare declared name); the mechanism was therefore a no-op
  against real CastXML dumps for both functions and variables, not just
  the previously-identified variable gap. `_qualified_functions_by_mangled`/
  `_qualified_variables_by_mangled` and `MarkReachability`'s
  `_public_header_names()` now recover the qualified identity by
  demangling the *mangled* linker symbol directly (backend-independent)
  when `.name` isn't already qualified.
- **`compare`'s directory/package fan-out no longer routes through a fake
  nested Click invocation** — `_dispatch_release_compare` called
  `ctx.invoke(compare_release_cmd, ...)` even though every one of that
  command's ~44 parameters was already supplied explicitly (no Click
  default-filling was actually happening); it now calls
  `compare_release_cmd.callback` directly, hand-preserving the one real
  behavior `ctx.invoke` provided (backfilling `UsageError.ctx` for a
  "Usage: ..." header on a validation error raised inside the release
  engine). `dump`'s inline per-side embed (`compare --old/new-sources`)
  keeps its `ctx.invoke(dump_cmd, ...)` — it genuinely relies on Click
  filling in ~25 of `dump_cmd`'s 44 parameters from their declared
  `@click.option` defaults, which cannot be replicated without either
  duplicating those defaults by hand (silent-drift risk) or reimplementing
  `ctx.invoke` via Click's private context/default-resolution internals for
  no behavioral gain.
- **`dump --dry-run` no longer misses two validations the real run enforces**
  — a `-p`/`--compile-db` given without `-H`/`--header`, and a
  `--debug-format`/`--dwarf`/`--btf`/`--ctf` selection against a PE/Mach-O
  binary, both raised only in the real (non-dry) path, after the dry-run
  branch had already returned — so `dump --dry-run` could report success on
  an invocation the real run would immediately reject with a
  `UsageError`/`BadParameter`. The two checks are now pure predicates
  (`check_dump_compile_db_error`/`check_dump_debug_format_error`) raised
  directly, unconditionally, before the `--dry-run` branch — the identical
  exit-64 usage error on both paths, not a softer exit-1 evidence blocker
  synthesized for the dry run (CodeRabbit review caught an earlier version
  of this fix doing exactly that).
- **A `.deb`'s planted `.deb_control/symbols` payload path could spoof its
  own Debian symbols contract** — `DebExtractor` extracted `data.tar.*`
  directly into the target directory, then created `.deb_control/` inside
  that same tree for `control.tar.*`; a `data.tar.*` member literally named
  `.deb_control/symbols` (crafted or coincidental) was left in place
  whenever `control.tar.*` had no `symbols` member of its own, and returned
  as though it were the genuine `dpkg-gensymbols(1)` contract. The
  `.deb_control` directory is now cleared before `control.tar.*` is
  extracted into it, so only its real members can ever populate the
  returned `symbols_file`.
- **`control.tar.*`'s own metadata could pollute package-compare library
  discovery** — `DebExtractor` extracts `control.tar.*` (package metadata:
  `control`, `md5sums`, the `dpkg-gensymbols(1)` symbols contract) into
  `target_dir/.deb_control/`, the same tree `discover_shared_libraries()`
  walks for `.deb`-to-`.deb` package compare. A crafted (or coincidental)
  `control.tar.*` member shaped like a shared object would be picked up by
  that function's "accept any `.so`-suffixed file at any depth" fallback
  and reported as though it were real package payload. `.deb_control` is
  now pruned from the walk by name (external review).
- **`dump_provenance.frontend` could name the wrong backend at `--depth
  source`** — it always preferred `snap.ast_producer` (the L2 header-AST
  backend) over the L4 replay extractor, so a header snapshot parsed with
  one frontend (e.g. `castxml`) combined with a prebuilt L4 pack from a
  *different* one (e.g. `clang`) recorded the unrelated L2 backend as the
  source-depth frontend. Now prefers the L4 extractor once the effective
  depth is `source` (the L2 backend is irrelevant to what actually produced
  the accepted evidence); `ast_producer` stays authoritative below `source`.
- **`dump --dry-run --depth source --build-info <pack>` gave no signal at
  all when the pack lacked real L4 evidence** — a pack-*shaped*
  `--build-info` (`is_pack_dir`/`_is_inputs_pack_dir`, a cheap manifest-shape
  check) was treated as fully satisfying, but that shape alone doesn't prove
  the pack's manifest actually carries usable `source_abi` facts; a
  manifest-only/empty pack is exactly as unsatisfiable as a raw compile
  database, yet produced neither the existing warning (which requires
  `--build-info` to also be absent) nor a blocker. Now warns that the pack's
  contents were not verified — the same "possibly satisfiable" treatment
  already given to `--depth build` backed by an unverified compile database
  — without a dry run loading the pack to check (real I/O it must not do).
- **`--dwarf-only` could satisfy `--depth build` without ever using the
  requested headers** — `--dwarf-only` explicitly ignores `-H` headers
  (DWARF becomes the primary data source instead), but `perform_elf_dump`'s
  `parsed_with_build_context` stamp wasn't gated on the snapshot actually
  being header-parsed (`snap.from_headers`), only on a matched `-p`/
  `--compile-db`; `dump lib.so -H api.h -p build --dwarf-only --depth build`
  could pass the strict depth gate and write a DWARF-only snapshot that
  never touched the header/build context (external review; mirrors the
  PE/Mach-O path's identical `from_headers` gate).
- **`dump --sources ... --depth binary` could "succeed" with an entirely
  empty snapshot** — a source-only dump (no `SO_PATH`) has no binary at
  all, so `--depth binary` (rank 0, the floor) was trivially "satisfied" by
  the strict depth gate even for a completely empty snapshot (`--depth
  binary` also resolves collect_mode to `off`, skipping L3-L5 embedding
  too); `dump --sources src --depth binary -o out.json` used to exit 0 and
  write a snapshot with no binary, header, build, or source facts
  whatsoever — a baseline/CI consumer would read that success as proof the
  requested rung is genuinely present. Now a usage error, in both the real
  run and `--dry-run` (external review).
- **A scoped `compare --used-by`/`--required-symbol(s)` JSON's new
  `full_summary` key was undocumented and unversioned** — added in this
  same round of fixes without a `REPORT_SCHEMA_VERSION` bump or a packaged
  schema entry, violating the schema's own additive-change policy.
  `REPORT_SCHEMA_VERSION` is now `2.9`; `full_verdict`/`full_severity`
  (already emitted, unversioned, since the pre-1.0 CLI reset) and
  `full_summary` are now all declared in `compare_report.schema.json` and
  documented in `docs/user-guide/output-formats.md` (external review).
- **`dump_provenance.source_scope` hardcoded `"target"` unconditionally** —
  correct for `dump`'s own inline `--sources` embed (always "target"
  scope), but `dump` also accepts a *prebuilt* `--build-info <pack>`/
  `abicheck_inputs` pack that could have been collected at any scope
  (`"changed"`/`"full"` from a `collect --depth source --since ...`/
  `graph-full` run); hardcoding "target" misreported that pack's actual
  scope, and even claimed a scope for dumps with no source replay evidence
  at all. Now reads `source_abi.coverage["replay_scope"]` when available,
  `null` otherwise (external review).
- **`dump --dry-run --depth build -p <compile-db>` accepted an
  empty/non-matching compile database as a soft warning instead of a
  blocker** — loading a compile database and checking whether it matches
  the resolved headers is cheap, deterministic, read-only resolution (the
  same JSON load + path match the real run performs before castxml even
  runs), not "real work out of scope for a dry run" as an earlier version
  of this fix pass assumed; a matched database is now a clean pass (no
  warning at all — it does supply real evidence), an empty/unmatched one is
  now a definite blocker, matching the real run's strict depth gate exactly
  (external review).
- **`dump`'s "Resolved evidence depth: ..." stderr line could disagree with
  the same snapshot's `dump_provenance.effective_depth` in its JSON** — the
  stderr line used the plain `evidence_depth_label`, while the JSON used
  the stricter `_gated_source_label` `check_requested_depth_satisfied`
  itself gates on; they disagree on the documented zero-match-source-only
  case (L4 replay genuinely ran but linked nothing), so a `--depth source`
  dump could print `Resolved evidence depth: build` to stderr next to
  `"effective_depth": "source"` in the JSON it just wrote.
  `fold_dump_provenance_into_json` now returns its computed label for the
  stderr line to reuse verbatim, so the two can no longer diverge (external
  review).
- **`compare --format json --stat --used-by`/`--required-symbol(s)` could
  report a scoped `verdict` next to stale full-library `summary` counts** —
  the JSON `summary`/`full_summary` recompute that keeps a scoped run's
  `verdict` and `summary.total_changes` consistent only ran when the
  rendered payload had a `changes` array; `--stat` (`to_stat_json`) omits
  `changes` entirely, so a `--stat --used-by` run whose only gating issue
  was a scoped-only finding or a missing required symbol still swapped
  `verdict` to the scoped result but left `summary` untouched and never
  added `full_summary` — the same self-contradictory JSON shape the earlier
  fix above addressed for the non-`--stat` case. `--stat` now adds each
  scoped-only/missing-contract finding's own contribution on top of the
  existing full-library counts (Codex review).

### Added

- **Debian `.symbols` contract now participates in package compare** —
  `DebExtractor` only ever read a `.deb`'s `data.tar.*`; `control.tar.*`
  (which carries the `dpkg-gensymbols(1)` contract as `./symbols` for a
  library built with it) was never extracted at all, so the packaging
  contract could never inform a `compare`/`compare-release` run regardless
  of drift between it and the actual binary. `ExtractResult` gains a
  `symbols_file` field; when both sides of a `.deb`-to-`.deb` compare ship
  one, a mismatch (added/removed entry, minimum-version regression) is
  folded into the release warnings as informational context — additive
  only, never gating the verdict/exit code. See
  `docs/user-guide/debian-symbols.md` § "Automatic contract check on `.deb`
  package compare".

### Documentation

- **The `actions/baseline` Action is now documented** — it had no
  user-facing doc page at all. Added `baseline-management.md` § "Recipe A2:
  Multi-Library Releases", explicitly framed as a *baseline-set generator*
  (a thin wrapper running `dump` per library plus a manifest) rather than
  the removed baseline registry, to avoid readers mistaking it for a
  reintroduction of the `push`/`pull`/`list`/`delete` subcommand group
  ADR-043 removed. `actions/collect-facts` was already documented only in
  the advanced source-facts pages (`producing-source-facts.md`,
  `github-action-source-scans.md`), never surfaced as part of the everyday
  `dump`/`compare`/`scan`/`deps` story — no change needed there.
