### Fixed

- **An anonymous member's type is compared by identity, not by reader spelling** — the same unnamed struct or union member can be written `<unnamed-tag>` by one reader and `Outer::<unnamed-type-u>` by another, and the spelling alone was reported as a `type_field_type_changed` finding. Both spellings now resolve to the same anonymous identity, so only a real change of the member's type is reported.
