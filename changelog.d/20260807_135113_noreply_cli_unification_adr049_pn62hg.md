<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **`project`'s reusable `check-project.yml` no longer forwards a per-cell
  `compile.binding`'s `gcc-path`/`gcc-options` override onto `bundle`
  cells** (CLI-audit P1): a bundle cell's operand is the bundle-staging
  *directory*, and the CLI already hard-rejects `--gcc-path`/`--gcc-options`
  for a directory/package compare (the per-library release fan-out never
  threads a single-pair L2 compile context to each pair). Before this fix, a
  profile that set `compile.binding` for its target cells had that same
  per-cell override reach every bundle cell too, turning a previously
  working bundle check into a hard operational error — acknowledged as a
  known, unfixed pre-existing bug in the G34 producer/consumer-compiler-
  profile-separation plan. Fixed the same way the identical `ast-frontend`
  hazard was fixed: gated on `matrix.kind != 'bundle'`, falling back to the
  workflow-global `gcc-path`/`gcc-options` input for a bundle cell rather
  than silently dropping it, so behavior for a bundle cell is unchanged from
  before per-cell compile overlays existed. `sysroot` has no per-cell
  overlay field and is deliberately left unconditional — gating it would be
  a new, undecided behavior change (silently dropping an explicit
  workflow-global `--sysroot` for bundle cells) rather than a fix for the
  acknowledged bug.
