### Fixed

- **C11 atomic qualifier detection** — recognize current CastXML `AtomicType` nodes so `_Atomic` qualifier changes report the canonical `atomic_qualifier_changed` kind. Older CastXML baselines that lost the wrapped type are treated as no-evidence for that slot, avoiding a false ABI diff when unchanged headers are re-dumped.
