### Changed

- **`func_virtual_became_pure` declares where it is observable (ADR-093).** A pure virtual has no body and no symbol, so only the public header declares it: observable from `{B,H}` and not from the binary alone. A DWARF-only surface never holds the member, so the kind is not claimed at `{B,D}`. It leaves the UNCLASSIFIED baseline (133 -> 132).
