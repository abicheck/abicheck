### Documentation

- **ADR-063 Phase 5B's case-(a) field inventory is now fully audited.**
  Investigated converting `TypeField.default`/`deprecated`,
  `Function.deprecated`, `Variable.deprecated`, `RecordType.deprecated`,
  and `EnumType.deprecated`/`is_scoped` to a direct `FactStatus` read;
  found not safely convertible without a legacy-hybrid load-path fix (a
  real end-to-end test regression, not a hypothetical), and left on their
  existing `fact_provenance`-based gating with the specific finding
  recorded in both `diff_types_field_facts.py` and
  `docs/contribute/plans/one-semantic-pipeline.md`. No behavior change.
