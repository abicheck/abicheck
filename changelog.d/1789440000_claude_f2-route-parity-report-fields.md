### Fixed

- **A typed-API or release-member report now carries the same fields as the
  identical `compare` CLI run.** `old_evidence_depth`/`new_evidence_depth` and
  the `suppression_audit` block used to be computed by the native `compare`
  CLI alone, so `service.run_compare`/`run_compare_request` reports and every
  directory/package member's complete report carried `null` depths and no
  suppression audit (stale or high-risk rules invisible). Both are now
  attached by the shared Tier-2 pipeline
  (`workflows.analysis_assurance_attach.attach_evidence_depths`,
  `workflows.suppression_audit_attach.attach_suppression_audit`), and the CLI
  calls the same owners; the CLI-only helper is deleted.
- **`effective_config_digest` no longer depends on the front end.** A plain
  run (no `--contract`, no `--pack`) through the typed API or the release
  fan-out stamped a resolved `CompatibilityEvaluationConfig` and reported the
  `contract` tier, so its digest differed from the identical single-pair
  `compare` run's `baseline` tier. The rich tier is now recorded on every
  route exactly when a contract evaluation or a contributing pack resolved
  one, as the digest's own documentation states; a `.abicheck.yml`
  `policy.overrides` value still reaches the baseline tier through the
  scoring policy file.
