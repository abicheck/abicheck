### Changed

- **One owner for debug-format reads in the checker** (ADR-063, "no backend-specific collection") — evidence-tier checks now ask `model.debug_evidence.debug_info_evidence`, and layout detectors read `compare.debug_layout_view.DebugLayoutView`; `AbiSnapshot.dwarf`/`dwarf_advanced` are read by that one module only (baseline 22 sites in 16 modules down to 2 in 1). No finding changes.
