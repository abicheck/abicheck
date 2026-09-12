### Added

- **Per-bundle-member baseline header staging.**
  `abicheck.buildsource.bundle_member_snapshots` resolves each bundle
  member's own *baseline* snapshot out of a baseline-set's `manifest.json`
  — with the same containment and content-digest guarantees the staged
  member binary already gets — and stages them into a clean,
  one-snapshot-per-member directory usable as a directory-`compare`
  old-side operand, reporting per member whether that snapshot genuinely
  carries header-derived evidence (`AbiSnapshot.from_headers`). This is the
  historical, per-member header evidence `actions/baseline` already dumps
  from the baseline checkout, which a bundle check's raw-binaries old
  operand carries none of. `actions/resolve-baseline` exposes it through a
  new `stage-member-snapshots` input and `member-snapshots-dir` /
  `member-header-evidence` outputs. A bundle check's `depth` restriction is
  deliberately unchanged: the candidate-side and bundle-graph halves of the
  evidence path are still missing, and lifting a false-clean guard before
  its evidence path is complete would re-create the silent miss it prevents
  — see `docs/contribute/known-gaps.md`.
