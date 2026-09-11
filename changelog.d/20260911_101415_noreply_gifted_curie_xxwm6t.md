<!--
A new changelog fragment. See changelog.d/README.md for the workflow.

Uncomment exactly ONE '### <Category>' section below (remove its comment
wrapper) and replace the example bullet with your entry, written the way
it should read in CHANGELOG.md. Delete the other sections.
-->

### Fixed

- **`abicheck aggregate --format text` no longer crashes on a completed
  `compare --no-baseline` audit.** Widening `TargetReport.analyzed` to
  cover a completed audit left an unconditional `assert compatibility_
  verdict is not None` reachable with a `None` verdict; the text renderer
  now reports "audit completed — no compatibility verdict (no baseline)"
  for this shape instead. `profile_matrix` also no longer marks such a
  target's profile both `incomplete` and `unanalyzed` — indistinguishable
  from a report that never arrived — while a genuinely missing required
  report still does.
- **`gate-mode: advisory` now also neutralizes a no-baseline audit's
  `exit_axes.analysis_assurance`.** That shape carries no dedicated root
  `analysis_assurance_exit_contribution` key, so the existing
  neutralization (which zeroes that key) found nothing to act on; an
  explicitly advisory `require-complete-analysis: true` audit with partial
  assurance still blocked the trailing `aggregate` job.
