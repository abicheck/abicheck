### Fixed

- **ADR-068 cross-source hygiene findings now show their evolution state in
  Markdown, and are no longer misfiled as deployment risk.** The full-mode
  Markdown report never surfaced the `introduced`/`resolved`/`persistent`/
  `not_evaluated` axis `compute_cross_source_evolution` stamps on every
  hygiene finding (`unversioned_exported_symbol`, `exported_not_public`,
  `rtti_for_internal_type`, and friends) -- it was previously carried only in
  the JSON report's `cross_source_evolution` block and per-finding field. A
  `persistent` hygiene finding (already present before this comparison) now
  renders with a `Cross-source hygiene: persistent (pre-existing, not new)`
  tag and lands in its own `## 🧹 Cross-Source Hygiene Findings` section with
  per-state counts, instead of inside `## ⚠️ Deployment Risk Changes` --
  whose GLIBC-oriented blurb never applied to these findings and drowned
  genuine deployment-compatibility risk in the same section on an
  otherwise-unchanged pair of releases. The `--report-mode root-cause` view
  now carries the same tag on any hygiene finding it groups.
- **`evidence_status: "artifact_proven"` no longer overclaims proof for a
  synthetic, header-only removal finding on an ELF-tiered comparison.**
  `evidence_status_for_result` previously downgraded `artifact_proven` to
  `unattributed` only when the *comparison as a whole* never examined a real
  binary (`evidence_tiers == ["header"]`). A comparison that *did* examine a
  real ELF symbol table could still emit a `func_removed`/`var_removed`/
  `func_visibility_changed` finding synthesized from header-only overload-set
  evidence with no corresponding ELF export -- that specific finding now
  downgrades to `unattributed` too, keyed on its own unset `symbol_binding`
  (only for the finding kinds whose detector genuinely stamps it from a real
  symbol-table entry, and only on an `"elf"`-tiered run -- `symbol_binding`
  is never populated on PE/Mach-O regardless of evidence quality, so those
  runs are unaffected).
