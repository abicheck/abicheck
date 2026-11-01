### Added

- **`bundles:` entries can state their own scope policy** — a bundle takes
  `required: [target ids]` (a subset of its `targets:`; omitted means every
  member) and `on_incomplete: fail|warn`, so "these four of the six members
  must be present" lives in `.abicheck.yml` instead of on every invocation.
  The run plan's bundle cell carries them as `bundle_required`/
  `bundle_on_incomplete` (run-plan schema `abicheck.run-plan/v4`; an older
  reader refuses the plan instead of dropping the policy), and
  `actions/check-target` takes them as `bundle-required`/
  `bundle-on-incomplete`. It names each member by its baseline binary's
  canonical release key and passes `--select-required` for the required
  members, `--select` for the rest and `--set scope.on_incomplete=block|warn`.
  `check-project.yml` now stages the members it finds for such a bundle and
  leaves a missing one to the scope gate. `compare --set
  scope.on_incomplete=...` is applied on every route (it used to be refused
  with exit 64). `BundleSpec` moved to `abicheck.buildsource.project_bundles`
  and `CheckSpec`/`NO_BASELINE_CHANNEL` to
  `abicheck.buildsource.project_check_spec`; `project_targets` no longer
  exports them.
