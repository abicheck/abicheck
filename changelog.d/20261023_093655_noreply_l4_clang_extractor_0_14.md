### Changed

- **Clang-produced L4 source surfaces are stamped `producer_version` `0.14`** — a stored surface from `0.13` compared with a `0.14` surface is now a payload-revision mismatch. Only the pinned typedef `qualified_name` and `value` fields are compared across it; every other source check is withheld, and the run reports one `source_fact_coverage_incomplete` coverage finding (not a break) with `fact_set_comparability` reading `unknown`. Re-dump a baseline to compare its source checks in full. The source generation axis moves to 7.
