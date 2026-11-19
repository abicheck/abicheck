### Changed

- **Two more kinds declare where they are observable (ADR-093).** `secondary_vtable_group_changed` fires when a direct base's polymorphism flips and is observable from headers or DWARF, not from a stripped binary: `{B,D}`, `{B,H}`. `cpo_kind_changed` (a public name that is a function on one side and an exported variable on the other) is observable from the stripped binary: `{B}`. They leave the UNCLASSIFIED baseline (119 -> 117).
