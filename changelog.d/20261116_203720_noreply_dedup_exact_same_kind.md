<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **Same-kind findings no longer collapse when they differ in an identity fact** — `finding_dedup._dedup_exact` keyed on a hand-picked field tuple that omitted `qualified_name`, `source_location` and `disambiguator`, so two real findings of one `ChangeKind` that differed only in those facts were merged and one break vanished. The key now includes the canonical finding identity (`finding_identity.resolve_change_identity`'s `primary_id`) and the occurrence `disambiguator`. Identical copies still collapse, and the entity-free `primary_id` keeps AST and DWARF twins collapsing across evidence tiers.
