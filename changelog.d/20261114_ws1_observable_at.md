### Added

- `ChangeKindMeta` gains `observable_at` (the minimal evidence configurations that observe a kind, ADR-093) and `relations` (variant, same-event, subsumption and implication declarations). Every kind starts `UNCLASSIFIED`; the catalog validates antichains, relation targets, same-event verdict agreement and acyclic implication when the registry is assembled.
