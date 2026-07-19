<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Added

- **Two evidence-coherence cross-checks** (triage AC-008, AC-009): the
  intra-version `run_crosschecks` engine gains two new RISK `ChangeKind`s that
  flag mis-scoped build/source evidence before it is trusted.
  `compile_context_conflict` fires when two or more L3 compile units attributed
  to one build target disagree on an ABI-relevant compile context — one built
  `-frtti` and another `-fno-rtti` (or `-fexceptions`/`-fno-exceptions`), or the
  same preprocessor define bound to two values — because aggregating them into a
  single build context silently keeps one ABI and drops the other (the oneTBB
  umbrella-header / oneDAL per-variant case). `source_surface_dso_mismatch`
  fires when the linked L4 source surface carries reachable declarations but its
  decl→export linking matched none of the analyzed binary's exported symbols, so
  the surface almost certainly describes a different or shared DSO (one surface
  folded from every target's sources and reused across libraries). Both skip
  cleanly when their evidence is absent (no L3 build evidence / no binary export
  table) and are never artifact-proven breaks. `compile_context_conflict`
  compares *effective, language-qualified* per-TU modes — last-wins over the
  ordered `abi_relevant_flags` and C++-only for RTTI/exceptions/thread-safe
  statics — so a C TU or a `-fno-rtti -frtti` override is not a false positive.
  `source_surface_dso_mismatch` intersects the surface's own decl→export
  attribution *mappings* with the binary's live export set rather than trusting
  the summary `matched_symbols` counter, so a stale/shared surface linked
  against a different DSO (whose counter is positive against that other binary)
  is correctly caught. The comparison uses the L4 source-linker's export
  keyspace (raw platform symbol names), not the dumper's double-stripped
  Mach-O spelling, so a correctly relinked macOS C++ dylib surface (whose
  mappings keep the `_Z…` form) is not a false positive.

### Fixed

- **Explicit empty packs override embedded facts on the `compare` path**
  (Codex review): the dump/merge fold prefers a non-empty layer payload so an
  empty placeholder (e.g. a clang-less inline replay) can't mask a
  lower-priority pack's real facts, but the same preference wrongly let a
  `compare` run fall through to a snapshot's *stale embedded* L4/L5 when an
  explicit `--old/new-build-info`/`--old/new-sources` pack supplied an
  intentionally empty layer (a failed/absent replay). `_combine_packs` now takes
  a `prefer_nonempty` flag; `_resolve_side_pack` passes `prefer_nonempty=False`
  so an explicit pack overrides the embedded payload even when its layer is
  empty — the documented "explicit flags override embedded" contract — while the
  dump/merge callers keep the non-empty preference.
