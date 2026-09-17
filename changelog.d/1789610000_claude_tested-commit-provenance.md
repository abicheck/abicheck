### Added

- **The producer's analysed revision now travels inside the canonical
  aggregate document, and a trusted publisher reads it in one pass.** A
  `pull_request` producer builds an ephemeral merge commit that no GitHub
  API endpoint names, so a `workflow_run` publisher could only learn it from
  the producer — which meant a sidecar file, a privileged shell parsing it,
  and a *second* whole run of `actions/verify-source-run` to check the claim,
  downloading the same artifact twice. `actions/aggregate` gains
  `record-analysis-context`, which writes an `analysis_context` block
  (`abicheck.analysis-context/1`) carrying the tested revision, the PR head
  and base, the producer's run and attempt, and the orchestration revision as
  five separate fields; the block is emitted for a zero-comparison run too.
  `actions/verify-source-run` gains `provenance-from` to read it back out of
  the artifact it already extracted, verify its association with the pull
  request through the API, and report on `tested-sha-source`/`provenance`
  whether the identity was established — an absent record is never silently
  replaced by the PR head. `report-from`/`report-path`/`report-available`
  return the report location together with the identity it was checked
  under, giving a verified run whose report is missing an explicit
  unavailable-analysis answer. Owners:
  `abicheck.model.analysis_context`, `abicheck.frontends.action.cli_provenance`.
