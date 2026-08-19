<!-- Codex review follow-up round 3 on CLI cleanup phase two, PR 2/PR 1. -->

### Fixed

- **`abicheck.service.render_output(fmt="junit", ..., stat=True)` now
  returns real JUnit XML again, matching the pre-PR-1 `--stat` boolean's own
  behaviour.** That boolean never short-circuited JUnit at all (its guard
  was `if stat and fmt != "junit": ...`), since an XML consumer needs the
  structured `<testsuite>` document regardless of `--stat`. The restored
  compatibility shim had missed this one format and returned the human
  one-line summary instead.
- **`run-plan.json`'s `gate` block is now validated the same way a
  hand-authored `aggregate --manifest`'s `gate` block already is.** A
  malformed block (not an object, an unknown key, or a value outside the
  known `missing_required`/`unexpected_target` vocabulary) on an otherwise
  valid `abicheck.run-plan/v2` plan was previously coerced silently to "no
  gate" rather than rejected — `to_aggregate_manifest()` would then omit the
  requested policy entirely and `aggregate` would apply the hard-coded
  `fail`/`include` defaults, potentially reversing the CI outcome the
  operator actually configured. It is now a loud, rejected usage error.
- Regenerated the published Agent Skills after correcting a stale
  `--on-unexpected-target fail` reference (removed in PR 2) in the
  `native-release-compatibility` skill's source to point at the manifest/
  run-plan `gate.unexpected_target` field instead.
