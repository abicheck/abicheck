### Changed

- **Four more kinds declare where they are observable (ADR-093).** `func_export_added` and `var_export_added` are observable only with the declaration present (public header or DWARF), since the export-gained gate needs the old side's declaration; `serialization_tag_changed` is observable from the public header's tag constants; `tail_padding_reuse_changed` from the header parse that records the data size. Their `observable_at` is now set, and they leave the UNCLASSIFIED baseline (140 -> 136).
