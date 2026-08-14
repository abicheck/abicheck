### Added

- **`new_target`: a first-class lifecycle state for a library's first
  release in a multi-library baseline-set.** Previously, a target genuinely
  absent from an otherwise-healthy, schema/profile/`project_ref`/generation-
  compatible baseline-set always reported `ambiguous` — indistinguishable
  from a real staging mistake (wrong channel, wrong `baseline-path`, a
  typo'd target id), forcing a workaround of routing a new library's first
  check through `channel: none` until its second release. `resolve-baseline`
  and `check-target`'s new `allow-new-target` input (`checks[].
  allow_new_target` in `.abicheck.yml`'s per-target config) opts a specific
  check into the `new_target` outcome instead — an advisory, non-fatal
  report (`verdict: NEW_TARGET`, `check_evidence_coverage.state:
  new_target`) distinct from both `resolved` (a real comparison ran) and
  `ambiguous` (a real problem). Deliberately unsupported for a `kind:
  bundle` check (rejected at config-validation time and in
  `actions/check-target/validate-inputs.sh`): a bundle comparison needs one
  coherent release where every member already coexisted, so there is no
  well-defined old side for a member that's new. See
  [Baseline Management → A new library's first
  release](docs/use/baseline-management.md#a-new-librarys-first-release)
  and the [resolve-baseline](docs/reference/resolve-baseline.md)/
  [check-target](docs/reference/check-target.md) references. Report
  schema `2.35`: `verdict` gains `"NEW_TARGET"`,
  `check_evidence_coverage.state` gains `"new_target"`, and a new
  optional `baseline_new_target` boolean mirrors the existing
  `baseline_bootstrap`.
