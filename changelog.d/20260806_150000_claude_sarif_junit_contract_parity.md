<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Added

- **Per-finding ADR-049 contract fields in SARIF and JUnit output**
  (CLI-audit P1): `--contract-evaluation` previously stamped every finding
  with `contract_relevance`/`contract_reason_code`/`contract_assurance`/
  `compatibility_evaluation_status`/`compatibility_decision`/
  `gate_contribution`/`contract_evidence_refs`, but only the JSON and
  Markdown reports rendered the full shape — SARIF only emitted
  `contractRelevance`/`contractReasonCode`/`compatibilityEvaluationStatus`
  for a `NOT_EVALUATED` finding, and JUnit exposed none of it per finding at
  all. A machine consumer reading only SARIF or JUnit could see a
  non-gating finding without being able to tell, from that format alone,
  *why* it stopped gating. `to_sarif` now attaches the full canonical set
  as `properties` on every result whose `contract_relevance` is stamped
  (evaluated or not), and `to_junit_xml`/`to_junit_xml_multi` attach the
  same set as a `<properties>` block on each `<testcase>`. The native HTML
  report also gained a "📜 Contract" badge (relevance, reason code,
  assurance, compatibility decision, evidence references) on every
  evaluated finding's row — previously it rendered contract detail only
  for the `NOT_EVALUATED` appendix section, never for an `IN_CONTRACT`/
  `NOT_APPLICABLE` finding in the main Removed/Changed/Added tables. A run
  that never opts into `--contract-evaluation` is unaffected across all
  three formats — `contract_relevance` is `None` for the whole report, so
  nothing is emitted.
