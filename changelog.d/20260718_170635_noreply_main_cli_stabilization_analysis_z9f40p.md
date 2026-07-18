<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **`scan --against` no longer advertises unsupported directory/package inputs** —
  the help text and docs claimed `--against` accepted "a previous dump, library,
  directory, or package", but the implementation only ever resolved a single
  file; the option now rejects directories at the CLI level (`dir_okay=False`)
  and its docs point at `abicheck compare OLD_PACKAGE NEW_PACKAGE` for
  directory/package comparisons.
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
