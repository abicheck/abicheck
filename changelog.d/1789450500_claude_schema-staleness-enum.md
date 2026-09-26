### Fixed

- **A no-baseline compare report now validates against the published
  schema.** `analysis_assurance.schema_staleness_status` has always been
  `not_evaluated` when there is no OLD snapshot -- the value every sibling
  `*_context_status` field already allows -- but the report schema's enum
  listed only `clean`/`degraded`, so such a report failed validation. The
  enum (compare report schema **5.7**) and the merge's ranking scale now
  include it, and `run_outcome.assurance` is validated against the same
  `analysis_assurance` shape in both the compare report and the audit report
  (audit schema **1.6**) instead of accepting any object. No emitted value
  changes. `tests/test_report_schema_conformance.py` validates the reports
  every compare mode emits against their schemas and checks each assurance
  enum against the producer's own value vocabulary, so the two cannot drift
  again unnoticed.
