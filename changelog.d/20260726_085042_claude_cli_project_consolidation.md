<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Changed

- **CLI project-integration surface consolidated into one `project` group**
  (ADR-054). `build-output validate`, `project-targets validate`, and
  `run-plan generate` are now `project validate-build`, `project validate`,
  and `project plan` respectively. `project plan` (formerly `run-plan
  generate`) now exits `1` on a run-plan that resolves to zero checks unless
  `--allow-empty` is passed — it previously exited `0` with only a warning,
  which let a misconfigured project silently skip every downstream CI check.
  `abicheck aggregate` gained a `--run-plan RUN_PLAN_JSON` option that
  projects a run-plan straight to the expected-target set, replacing the
  separate `run-plan to-aggregate-manifest` step. The standalone `plan
  --dump-manifest` diagnostic is gone; use `dump --dump-manifest FILE
  --dry-run` instead, which now also reports the manifest's translation
  units and `scope_fingerprint`.

### Removed

- **`build-output baseline-libraries` and `run-plan to-aggregate-manifest`
  CLI commands.** Neither was a general-purpose operation: the former
  derived `actions/baseline`'s input for exactly two reusable workflows
  (which now call `abicheck.buildsource.baseline_publish.derive_baseline_libraries()`
  directly), and the latter is superseded by `aggregate --run-plan`. The
  root `build-output` and `project-targets` command groups and the `plan`
  command are also removed — see ADR-054 for the full migration table.
