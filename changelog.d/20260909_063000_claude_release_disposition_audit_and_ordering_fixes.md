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
