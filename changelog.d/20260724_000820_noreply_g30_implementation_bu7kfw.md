### Added

- **`abicheck run-plan` CLI group and the `check-single.yml`/`check-project.yml`
  reusable workflows** (ADR-047 §4/§5, G30 P1.4). `abicheck run-plan generate`
  projects a project's `.abicheck.yml` `targets:`/`bundles:`/`profiles:`/
  `baseline:` block (G30 P1.5) plus each contract profile's
  `build-output.json` (G30 P1.1) into `run-plan.json` — the ordered check
  list `check-project.yml`'s matrix consumes — resolving `(target, profile)`
  cells per the "never a blind cross-product" rule: an explicit
  `checks[].profiles:` selector must resolve against that profile's build
  output or it's an error, while the implicit "every contract profile"
  sweep silently skips a profile that doesn't build the target.
  `abicheck run-plan to-aggregate-manifest` projects `run-plan.json` to
  `abicheck aggregate --manifest`'s wire shape using each check's own
  `check_id` (never the bare target/bundle name), so multi-profile/
  multi-channel same-target checks never collide in `aggregate`'s
  duplicate-target-id check. `check-single.yml` wraps one
  `actions/check-target` invocation for a caller that wants exactly one
  check; `check-project.yml` generates the run-plan, fans it out over a
  matrix, and runs a trailing `aggregate` job — with the two required
  `if: always()` placements (the aggregate job itself, and each matrix
  cell's report-upload step) so a `gate-mode: deferred` operational failure
  on one leg can never silently skip the fan-in gate or drop its own report.
  See `docs/reference/run-plan-schema.md` and
  `docs/reference/reusable-workflows.md`.
