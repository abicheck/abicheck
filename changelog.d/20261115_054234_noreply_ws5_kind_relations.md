### Changed

- **Variant relation is resolved from declared metadata** — every `*_elf_only` kind now declares `relations=KindRelations(variant_of=<base>)` on its registry entry (ADR-093 D4), and `policy/kind_relations.py` resolves it. The name-derived table `model/change_catalog/kind_variants.py` is deleted: `kind_match_set`, `matched_via_variant`, `expand_variant_overrides`, `ROLLUP_MEMBER_KINDS` and `rollup_member_kinds` move to `abicheck.policy.kind_relations`. Selector, override, reclassification and acknowledgment matching is unchanged for every `ChangeKind`; a new variant is now a catalog declaration, not a name.

