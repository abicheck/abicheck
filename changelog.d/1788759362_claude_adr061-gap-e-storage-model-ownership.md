### Changed

- **Internal (ADR-061 gap E, closure package 5, slice 1): the snapshot
  loader's evidence-derived Python-extension backfill moved out of
  `storage`-shaped decode logic and into `workflows`.**
  `serialization.snapshot_from_dict`'s legacy backward-compatibility backfill
  (deriving `AbiSnapshot.python_ext` from already-parsed ELF/PE/Mach-O
  evidence for a snapshot that predates the G14 key) called
  `python_ext.detect_python_extension()` directly — real evidence
  derivation, not a fact lookup, which a `storage`-classified module may
  never call into `extract` to do. It now runs as an explicit post-load step,
  `workflows/snapshot_load.py::backfill_python_ext_from_evidence()`, invoked
  from `snapshot_from_dict` after the snapshot is fully decoded.
  `snapshot_from_dict`'s own public signature and behavior are unchanged —
  verified by six characterization tests
  (`tests/test_snapshot_python_ext_backfill.py`) written against the
  pre-move behavior first and passing unchanged after the move. Separately,
  the ~10 `_xxx_from_dict` helpers in the sibling
  `snapshot_platform_blocks.py` now import their dataclasses
  (`ElfMetadata`, `PeMetadata`, `MachoMetadata`, `DwarfMetadata`,
  `AdvancedDwarfMetadata`, `SyclMetadata`, `KabiMetadata`,
  `NumPyCapiSurface`, `PythonExtMetadata`, `PythonApiSurface`, and their
  nested value types) from each type's canonical `model/*_facts.py` home
  instead of the flat, `extract`-classified parser module — closing that
  file's own `storage -> extract` edge and letting it be classified
  `storage`. No observable behavior changes.
