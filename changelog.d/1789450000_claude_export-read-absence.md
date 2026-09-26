### Fixed

- **An unread export table no longer confirms that a declaration is not
  exported.** Every producer (castxml, clang, DWARF) recorded
  `binary_exported_fact = PRESENT(False)` for each lookup miss whenever a
  binary was supplied, even when the export table came back empty because the
  container had none or its parse failed (`parse_pe_metadata` and
  `parse_macho_metadata` return empty metadata on error). The removal,
  visibility-loss and depth-projection readers then acted on a table nobody
  read. The ELF/PE/Mach-O builders and snapshot load now share one rule
  (`abicheck/extract/export_table_read.py`, `ExportTableState` in
  `abicheck/model/export_index.py`): an empty table makes every miss
  `NOT_COLLECTED` (unknown), a failed read `FAILED`, and only a table that
  yielded entries keeps `PRESENT(False)`. A stored baseline written before
  this fix, including a pre-v46 one whose `Visibility.HIDDEN` re-derives the
  same answer, is reconciled on load, so a stored operand and a live dump of
  the same binary agree. No schema change.
