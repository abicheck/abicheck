### Changed

- **Every reader of `RecordType.bases`/`virtual_bases`/`vtable`/
  `vptr_offset_bits` and `Param.is_va_list` now reads through the field's
  `Fact[...]` sibling instead of the retained legacy attribute** (ADR-063
  Phase 0's detector migration, closing the gap that phase's status
  previously recorded). All 104 previously-tracked sites across 20 modules
  are migrated via a new, mypy-safe `abicheck.model.resolved_fact_value()`
  helper; `scripts/fact_field_readers.py`'s `KNOWN_UNMIGRATED_READERS`
  baseline is now empty, though the underlying repo-wide scan stays fully
  live against a future regression. This is representation-only -- the
  legacy field and its `Fact[...]` sibling are kept in exact sync by each
  dataclass's own `__post_init__` bridge, so no detector's emitted findings
  change (confirmed by the full test suite and the FP-rate/tier-accuracy
  gates staying at their existing baselines). `diff_types.py` and
  `dwarf_snapshot.py` each split one self-contained cluster into a new
  sibling module (`diff_types_vtable.py`, `dwarf_snapshot_datasources.py`)
  to stay under the file-size cap after this migration's added lines;
  `dwarf_snapshot_datasources.py`'s `show_data_sources` is re-exported from
  `dwarf_snapshot.py` unchanged.
