### Changed

- **A rule whose kind the run observed is `unused`, not `not_evaluated`** — a zero-match rule now also reads `unused` when the comparison recorded a change of one of its kinds in any disposition (kept, suppressed, redundant, out of scope or reconciled), because the kind's producer demonstrably ran. This mainly affects the ~130 kinds no registered detector emits (for example `public_surface_shrank`): a stale rule on them could only read `not_evaluated` before, so `unused_rules: block` could never fire for it. Under `block` such a rule can now raise a clean `0` to `1`.

### Added

- **`not_evaluated` rules are a warning under `unused_rules: warn|block`** — every `not_evaluated` audit row now carries `missing_evidence` (the producing detectors that did not run and their gate reasons, or that the kind comes from a pipeline step that recorded nothing), and under `warn`/`block` `disposition_audit.unused_rules_review.not_evaluated` lists those rules and the Markdown audit renders them as a warning. It never gates. `allow` (the default) output is unchanged apart from the new row field. See ADR-067 addendum 2.
