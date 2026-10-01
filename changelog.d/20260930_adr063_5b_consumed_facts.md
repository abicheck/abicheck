### Changed

- The fact registry now marks 18 facts as `consumed` (ADR-063 sub-phase 5B): detectors read these facts' availability status instead of treating a missing value as absent. `FactDefinition.consumed_by` names each consuming detector, and the `fact-registry-completeness` gate checks by AST that each named detector exists and really reads the fact.
