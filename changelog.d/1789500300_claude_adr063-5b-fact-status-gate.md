### Changed

- ADR-063 5B: the seven header facts previously gated on per-declaration `fact_provenance` (`deprecated` on functions, variables, records, fields and enums; `EnumType.is_scoped`; `TypeField.default`) now gate on their own `FactStatus` through one shared `compare/fact_gate.both_facts_present`. A declined comparison with asymmetric or failed evidence is recorded in the report's detector `declined` list. The unused `fact_known_qualified`/`both_known_backed_fact_qualified` helpers were removed.
