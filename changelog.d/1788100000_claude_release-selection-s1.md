### Added

- **`compare`'s directory/package release fan-out accepts an explicit member
  selection.** New `--select`/`--select-required` flags (repeatable) declare
  the expected release members by canonical identity (e.g. `libfoo.so`,
  never a raw filename stem) instead of relying purely on filename
  set-difference: a discovered member not named in the selection is now
  `out_of_scope`, and a declared member missing on both sides is
  `expected_not_produced`. A `--select`-only (optional) member's absence
  never makes the comparison scope incomplete; `--select-required` still
  gates `--on-incomplete-scope block` the way an ordinary unmatched member
  does. `compare --dry-run` on a directory/package pair now renders a
  "Comparison plan" section showing what would be compared before any
  per-library dump/compare runs (ADR-065 S1).

### Removed

- **`bundle_variants_config.py` (and its `bundle_variants:`/`required:`
  parsing) has been deleted.** It shipped with no production caller; ADR-065
  S1's deletion gate required either wiring a real consumer or removing it
  in the same slice, and a genuine consumer (per-variant `BundleFacts`
  capture tagging) does not exist yet, so the dead module is removed rather
  than left unreachable.
