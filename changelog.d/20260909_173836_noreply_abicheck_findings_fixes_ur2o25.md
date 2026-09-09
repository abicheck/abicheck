### Fixed

- **`compare`'s report summary no longer mislabels a public-surface shrink as
  a compatible addition.** `summary.compatible_additions` stayed the
  historical "all COMPATIBLE findings" total, but since surface metrics
  became unconditional (ADR-027 Phase 5) a `public_surface_shrank` finding
  (a net *decrease* in public declaration count) had nothing to distinguish
  it from a real API addition when a consumer read that field alone. A new
  `summary.quality_issues` field (JSON `report_schema_version` 3.13; a
  parallel "Quality issues" row in the Markdown review digest) names the
  non-addition subset, mirroring the `quality_issues` field the release
  fan-out (`compare` over a directory/package) already emits per library.
- **The byte-identical-inputs coverage warning
  (`note_if_same_binary_compared`) now fires on a snapshot-input comparison
  too.** It previously keyed only on `LibraryMetadata.sha256`, which
  `collect_metadata` deliberately never populates for a JSON/text snapshot
  path — so a `--old-snapshot`/`--new-snapshot` compare (or any typed
  `CompareRequest` over two snapshot files) could never get the warning
  even when both sides were content-identical. It now falls back to a
  canonical snapshot-content digest when live-binary metadata is
  unavailable on either side, worded as "old and new abi snapshots are
  byte-identical" (not "binaries") to keep the weaker claim honest.
- **A finding downgraded to `evidence_status: unattributed` no longer
  carries the same unconditional impact text as an `artifact_proven`
  finding of the same kind.** `impact_for()` now accepts the finding's
  evidence status and appends an evidence caveat (e.g. `func_removed`'s
  "dynamic linker will refuse to load or crash at call site" no longer
  reads as certain for a finding synthesized from header-only evidence
  with no matching symbol-table entry). Wired into the JSON report's
  `impact` field, every Markdown report mode (full/leaf/root-cause), the
  native and ABICC-compatible HTML renderers, and SARIF's per-result
  `message.text` (SARIF's own rule `fullDescription` stays kind-level and
  unconditional, since it's shared across every finding of that kind — a
  new `checker_policy.impact_caveat_for()` helper carries just the caveat
  sentence there instead).
- **`SYMBOL_RENAMED_BATCH`'s evidence status now reflects its weakest
  constituent pair, not the kind-level default.** The batch rollup (a
  common-prefix rename or a namespace-segment move, both landing on
  `SYMBOL_RENAMED_BATCH`) never stamped `Change.symbol_binding`, so it
  always read `ARTIFACT_PROVEN` regardless of whether every rolled-up
  pair was actually matched against a real ELF symbol-table entry.
  `emit_prefix_batch_rename`/`emit_namespace_move_batches` now resolve
  each constituent pair against its real declaration in the old-side
  symbol map (by demangled name for the prefix-rename shape, by scope-
  component identity for the namespace-move shape — an identity mismatch
  against the map's own mangled keys previously made the check vacuously
  pass regardless of real evidence) and stamp `symbol_binding` only when
  *every* constituent's old side carries a real observed ELF binding, so
  a batch with even one header-reconstructed (non-ELF-backed) or
  unresolved pair correctly downgrades to `unattributed` on an
  `elf`-tiered run.
- **An `elf_only`-visibility removal's report now carries a demangled
  companion to its raw mangled symbol.** `func_removed_elf_only` and an
  `elf_only`-visibility `var_removed` previously showed only the raw
  mangled name in `description`/`old_value` (no header AST ever ran to
  produce a pretty name, unlike every other visibility) — unreadable in
  a machine format (JSON/SARIF), which deliberately never demangles
  `symbol`/`old_value` themselves. A new `Change.demangled_symbol`
  field (JSON `demangled_symbol`, schema 3.14; SARIF
  `properties.demangledSymbol`) carries the demangled form when it
  differs from the raw name; `None`/omitted for every ordinary,
  already-demangled finding.
- **Public-surface scoping can now demote genuinely internal-only
  vtable/RTTI churn, on every host regardless of whether an optional
  external demangler is installed.** `VTABLE_SLOT_COUNT_CHANGED`/
  `RTTI_INHERITANCE_CHANGED`/`VTT_SLOT_COUNT_CHANGED`
  (`diff_elf_layout.py`) carry a raw mangled `_ZTV`/`_ZTI`/`_ZTT` symbol in
  `Change.symbol`, not a plain type spelling. `surface.py`'s type-level
  reachability fallback previously tried to extract a type name straight
  out of that mangled blob, which could never match a real type — so
  these three kinds always fell back to the conservative "unknown, keep"
  default regardless of whether the class was actually public. The
  fallback now resolves the symbol's owning class via a new in-process,
  dependency-free structural parser
  (`model.mangled_name.itanium_special_name_owner_scope_components`) for
  this exact shape, rather than the optional `cxxfilt`/`c++filt`-backed
  `demangle()` — a policy-affecting classification must not silently vary
  by whether that external tool happens to be installed on the host.
  Verified against the FP-rate and per-tier-accuracy gates (no drift).
- The Markdown root-cause report's per-change evidence-caveat resolution
  moved out of the render layer (`report/render_markdown.py`'s
  `_format_change_md`) and into the compute half
  (`reporter_markdown.compute_root_cause_section`), matching every other
  report projection's compute/render split — the renderer now takes an
  already-resolved `EvidenceStatus`, never deriving one itself from raw
  evidence tiers.
- `SYMBOL_RENAMED_BATCH`'s constituent-evidence resolution moved out of the
  no-growth `diff_symbols_renames.py` legacy monolith into a new leaf module,
  `compare/rename_evidence.py` (`all_constituents_elf_bound`, parametrized by
  a per-shape identity key), which `compare/namespace_move.py`'s own
  near-duplicate helper now shares too instead of reimplementing the same
  reverse-index-then-lookup shape a second time — per ADR-061 D1, new
  comparison behavior belongs in `compare/`, not in a `no_growth`-tracked
  legacy module whose baseline gets raised to make room for it (Codex
  review).
- An `elf_only`-visibility removal's `demangled_symbol` resolution now
  batches every such removal's demangling into one `c++filt`/`cxxfilt` call
  per comparison (`demangle.demangle_batch`, the same warm-then-reuse
  pattern the rename detector already relies on) instead of forking one
  subprocess per removed symbol when the faster `cxxfilt` binding isn't
  installed — a library with many distinct ELF-only C++ removals could
  previously add seconds to minutes of pure process-launch overhead even
  when the selected report format never reads `demangled_symbol` at all
  (Codex review). Scoped to names that can actually end up
  `FUNC_REMOVED_ELF_ONLY`/`VAR_REMOVED` (excluding any OLD-side mangled key
  the NEW side still exports), not every ELF-only name in the OLD snapshot
  — the first cut of this prewarm demangled an *unchanged* large ELF-only
  C++ library's entire export table on every comparison (Codex review,
  fresh evidence). Both the resolver and the prewarm moved into a new leaf
  module, `compare/elf_only_demangle.py`, for the same no-growth-monolith
  reason as the batch-rename evidence move above.
- The root-cause Markdown view (`--report-mode root-cause`) now preserves a
  `scoped_only_changes` (`--used-by`/`--required-symbol`) entry's
  `EvidenceStatus.CONSUMER_PROVEN` override instead of re-deriving its
  evidence status from `DiffResult.evidence_tiers` like an ordinary
  comparison finding — JSON and SARIF already stamp a scoped_only finding
  this way (proven by the supplied consumer's own import table,
  independent of what the library-to-library comparison itself examined),
  so the un-fixed root-cause path could describe empirical consumer
  evidence and then call its own consequence merely plausible (Codex
  review).
- `--report-mode leaf`'s type-change section renderer
  (`_render_leaf_type_change_row`) now renders the evidence-qualified
  `impact` text `_change_row` already computes for it — it previously
  rendered no impact text at all for a type-kind finding (e.g.
  `type_size_changed`), unlike the non-type-change row renderer, which
  already did (CodeRabbit review).
- `surface.py`'s demangle-aware type-candidate resolution is now computed
  lazily, only when a finding actually reaches `_classify_type_level` —
  previously it ran unconditionally for every finding this module
  classifies, so an ordinary symbol-level `FUNC_REMOVED`/`VAR_REMOVED`
  that `_classify_symbol_level` resolves on its own still forked a
  `c++filt` (via `demangle()`) for a value it never used (CodeRabbit
  review).
- `surface.py`'s vtable/RTTI/VTT owner resolution now falls back to
  `demangle()` specifically when the owner's own component carries a
  template-argument list. The dependency-free structural parser
  deliberately keeps a template owner's *raw encoded* arguments (e.g.
  `Box<int>` -> `"BoxIiE"`) so distinct specializations stay distinct,
  but that raw spelling can never match the model's own canonical type
  names — a templated vtable/RTTI owner was therefore never actually
  matched at all, even on a host with a demangler installed (Codex
  review, fresh evidence).
- Appcompat's own HTML changes tables (`--app`'s breaking-for-app and
  irrelevant-for-app sections) now thread `DiffResult.evidence_tiers`
  through, matching the native and ABICC-compatible HTML renderers —
  previously an unattributed finding here always read as
  `artifact_proven`, unlike every other HTML surface (Codex review,
  fresh evidence).
