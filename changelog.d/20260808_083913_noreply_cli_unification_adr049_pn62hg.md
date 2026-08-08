<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Changed

- **`realworld-validation.yml`'s real-package regression lane now covers the
  subsystems it actually exercises** (CLI-audit P1, CI hardening): its
  `pull_request` `paths` filter was scoped only to package/bundle plumbing,
  so a change to contract evaluation, comparability, compiler-profile
  resolution, or a report schema could skip the one lane that runs
  `abicheck compare` against a real package binary rather than a synthetic
  snapshot. Extended to cover `abicheck/contract_*.py`,
  `abicheck/compatibility_evaluation_*.py`, `abicheck/comparability*.py`,
  `abicheck/pack_application.py`, `abicheck/impact/**`,
  `abicheck/buildsource/{project_targets,run_plan,toolchain_probe,
  toolchain_bindings}.py`, `abicheck/schemas/*.schema.json`, and
  `abicheck/checker{,_policy}.py`. Also added a second real-package compare
  step exercising `--contract-evaluation --contract public` end to end
  (verified locally against a real Ubuntu `zlib1g`/`zlib1g-dev` `.deb` pair)
  — a paths-filter fix alone would only trigger the lane, not actually cover
  the feature it's meant to guard.
