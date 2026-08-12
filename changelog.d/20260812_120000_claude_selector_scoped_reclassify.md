<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Added

- **Selector-scoped reclassification (`reclassify:`), a third `--policy-file`
  primitive.** `suppress:`'s `Suppression` rules already had a rich
  per-symbol/pattern/namespace selector grammar but only one action —
  deleting the finding — while `overrides:` could change a finding's
  verdict but only per `ChangeKind`, globally, with no selector. Neither let
  a project say "every `func_visibility_changed` on *this* symbol family is
  a known, accepted risk" without either downgrading the whole kind
  project-wide or throwing the finding away entirely — the motivating case
  is a COMDAT-inline-heavy library like oneDAL, where dozens of symbols need
  the same downgrade while an unrelated visibility regression elsewhere
  must still break. `reclassify:` closes that gap: each rule reuses
  `Suppression`'s exact selector grammar (`symbol`/`symbol_pattern`/
  `type_pattern`/`member_name`/`namespace`/`entity_namespace`/
  `cause_namespace`/`source_location`/`change_kind`/`expires`) plus a
  required `to:` (`break`/`warn`/`risk`/`ignore`, the same vocabulary
  `overrides:` already uses), and keeps the finding visible at the new
  verdict instead of deleting it. Consulted ahead of the kind-global
  `overrides:` entry for the same kind (a selector-scoped rule is strictly
  more specific), and still respects the existing frozen-namespace verdict
  floor. See `abicheck/reclassify.py`'s module docstring and
  `policy_file.py`'s format example. Consulted by the shared per-finding
  severity resolver (`severity.effective_verdict_for_change`, and
  everything built on it — `IssueCategory` buckets, JSON/HTML/SARIF
  severity labels, severity-based exit codes), not just
  `PolicyFile.compute_verdict`'s legacy verdict, so a `to: risk`/`to:
  ignore` reclassification can't still fail a severity-gated run through
  the other path. A non-string selector value (e.g. `symbol_pattern: 42`)
  is now a hard `PolicyError` at load time instead of an uncaught
  `TypeError` or a rule that silently never matches. `reclassify:`'s
  `expires` now normalizes an unquoted YAML timestamp (decoded by PyYAML as
  a `datetime`) to a `date`, matching `suppress:`'s existing handling.
  `PolicyFile`'s new `reclassify` field is keyword-only, so an external
  caller constructing `PolicyFile(base, overrides, source_path)`
  positionally is unaffected. `scan --against`'s severity-scheme report now
  correctly names a `reclassify:`-demoted finding as the scan's own
  blocking cause (`severity.classify_change_object` gained an optional
  `policy_file` parameter, threaded through
  `cli_scan_baseline._blocking_compatible_changes`) instead of silently
  omitting it — the gate/exit-code computation was already correct, only
  the itemized report was missing the symbol. A `reclassify:` rule
  downgrading one addition-kind finding to `ignore` now correctly lands in
  the `addition` severity bucket rather than `quality_issues`, even when a
  kind-global `overrides:` entry for the same kind would otherwise move it
  out of the compatible kind set entirely — the scoped rule's own
  resolution now wins the addition/quality split too, not just the
  verdict. Active `reclassify:` rules are now disclosed in the standard
  JSON report's new `policy_reclassify` key (`report_schema_version` 2.30,
  additive), alongside the existing `policy_overrides`/`policy_file` keys —
  previously an ordinary comparison reclassifying a finding left no trace
  of the active rule anywhere in the standard report. The same disclosure
  now also appears in the Markdown (`**Policy reclassify**`), HTML
  (`<tr><th>Policy reclassify</th>...`), and SARIF (`policyReclassify` run
  property) report formats, mirroring their existing `overrides:`
  disclosure. `--audit-suppressions`' `SuppressionList.audit()` gained an
  optional `policy_file` parameter: a `reclassify:` rule promoting a
  normally-compatible finding to `break` for one specific symbol is now
  correctly flagged as a high-risk suppression match, which a kind-wide
  `breaking_kinds` set alone could never express. That classification now
  routes through the same shared per-finding resolver
  (`severity.effective_verdict_for_change`) the comparison's own verdict
  already uses, rather than a standalone `reclassify:` check -- so a
  pipeline-set `effective_verdict` modulation and the frozen-namespace
  verdict floor are honored with the correct precedence too, not just
  `reclassify:` in isolation.
