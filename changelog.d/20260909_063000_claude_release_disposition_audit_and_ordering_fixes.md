<!--
A new scriv changelog fragment.

Uncomment the section that is right (remove the HTML comment wrapper).
-->
### Fixed

- Directory/package `compare`'s release-level `disposition_audit` now
  includes cross-library bundle findings — a release whose only change was
  a `bundle_library_removed`-shaped bundle finding used to report
  `detected_total: 0`/`effective_total: 0` alongside a breaking
  `bundle_findings` entry. The `--output-dir` `summary.json` sidecar now
  carries the same bundle contribution as the primary report.
- The lockstep-SONAME-bump suppression on a directory/package `compare`
  (a coordinated `SONAME_BUMP_UNNECESSARY` downgrade when a sibling library
  has a real binary break) now records the suppression on the affected
  library's own `disposition_audit`, instead of silently hiding the finding
  while still classifying it `non_gating` with no suppression rule.
- `compare --dry-run --view leaf`/`--view root-cause` on a directory or
  package operand now correctly exits `64` (usage error), matching the
  identical non-dry-run invocation, instead of reporting a clean dry-run
  exit `0`.
- `compare --view patterns` on a directory/package operand with multiple
  matched libraries no longer risks interleaving different libraries'
  pattern-modulation ledger output under the default parallel fan-out; each
  library's block is now rendered after every comparison completes, in
  matched-library order.
- Case 34's (`access_level`) example command referenced a `.abicheck.yml`
  it never created, so copying it verbatim exited with a usage error
  instead of producing the documented finding.
- `compare --no-baseline SNAP --view ...` now rejects the `--view` flag
  with a usage error instead of silently ignoring it — a `--no-baseline`
  audit reports an empty change set by construction, so it has no
  root-cause graph, findings list, or pattern-modulation ledger for
  `--view` to act on.
- Case 165's (`polymorphic_nonvirtual_dtor`) example command referenced the
  retired `compare --pattern-verdicts` flag (pattern-verdict modulation is
  unconditional now); copying it verbatim exited with a usage error instead
  of producing the documented finding.
- `tools/clang-layout-tool/README.md` referenced the retired
  `--ast-frontend clang` flag in its example command and prose; the docs
  contract's retired-surface sweep didn't scan `tools/` at all, so this
  went uncaught. The sweep now covers every `tools/**/*.md`.
- Documented (no behavior change) that `compare --format junit --view
  impact` on a directory/package release intentionally renders ordinary
  JUnit XML with no impact representation, matching single-pair
  `compare`'s own already-shipped identical no-op for the same flag
  combination.
- `compare --format json --view show=...` on a directory/package release
  now records the active filter (`show_only_filter`) and the pre/post
  finding counts (`release_filtered_summary`) in the release JSON document
  — a filtered-to-empty `findings` list next to `verdict: BREAKING` used
  to be indistinguishable from missing or truncated detail. The counts
  use each library's real, uncapped finding total rather than its
  display-capped `findings` list (which previously under-reported past
  the per-library display limit), and live under a new field name rather
  than reusing scalar `compare` JSON's own differently-shaped
  `filtered_summary`.
- The `--output-dir` `summary.json` sidecar no longer leaks the
  release engine's private per-library accounting keys
  (`findings_view`, `impact_table_view`, and similar) into its
  per-library JSON entries.
- The pattern-aware modulation ledger's own text (`compare --view
  patterns`) referenced the retired `--pattern-verdicts` flag; it now
  names the flag's actual replacement.
- `compare --view show=...` on a directory/package release now also
  discloses the active filter and displayed/total counts in the
  Markdown report (a `> Filtered by: ...` note), matching the JSON
  document's `release_filtered_summary` and scalar `compare` Markdown's
  own long-standing note for the same reason.
- The lockstep-SONAME-bump suppression on a directory/package `compare`
  now also records the suppressed finding in the affected library's
  `DiffResult.suppressed_changes`/`suppressed_count` — the fields
  `to_json()`'s own `suppression` block reads — not just on the
  disposition ledger; a rewritten `--output-dir` per-library JSON
  previously reported `suppressed_count: 0` despite its own
  `disposition_audit` recording a suppression.
- Added `tools/` to `[tool.mutmut].also_copy` — the mutation-testing
  sandbox never had a `tools/` directory to scan, so the new
  tools-README retired-surfaces test failed in that lane alone (the
  real repository tree it validates against has always had `tools/`).
- `TestReleaseTopologyOverlayCleansUpOnExit`'s own bash test harness used
  `${CMD[-1]}` (negative array indexing, requires bash ≥ 4.3) to read
  back a value from the flags it built for asserting on, which fails
  deterministically under macOS's system `/bin/bash` (3.2) with `set
  -u` — a pre-existing, unrelated-to-this-PR gap now fixed with a
  portable `${CMD[${#CMD[@]}-1]}` equivalent.
