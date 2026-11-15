### Changed

- **AST/DWARF duplicate collapse is driven by declared `subsumed_by` relations** — the five DWARF-lane struct kinds (`struct_size_changed`, `struct_alignment_changed`, `struct_field_offset_changed`, `struct_field_removed`, `struct_field_type_changed`) now declare the AST-lane kind(s) that subsume them on the registry (ADR-093 D4), replacing the hand-written `_DWARF_TO_AST_EQUIV` table in `compare/finding_dedup.py`. Which finding survives is unchanged. A new architecture rule (`kind-relation-table`) rejects hand-written kind-to-kind tables outside the catalog and `policy/kind_relations.py`.

### Added

- **Dropped duplicates are recorded as subsumed** — a finding collapsed as a duplicate of a declared subsumer is still recorded in the disposition ledger as `deduplicated` (as before), and the relation is now declared on the registry rather than in code. Consumers can join the dropped DWARF finding to the AST finding it duplicates through the registry's `subsumed_by` declaration.
