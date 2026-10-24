### Added

- Report schema 6.6: an id-less rule's `disposition_audit` row and ledger rule provenance carry the readable `rule_identity` (canonical JSON) next to its opaque `rule-<hex>` `rule_id`; `suppression_audit.stale_rule_ids` lists each stale entry's `rule_id` in `stale_rules` order, so stale entries join to audit rows. The release recommendation names an id-less suppressing rule by its label, or by its identity when it has none.
