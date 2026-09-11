### Fixed

- **DWARF's own per-translation-unit debug-info gaps can no longer fabricate a
  `TYPE_VTABLE_CHANGED`/`VIRTUAL_METHOD_ADDED` finding.** When two
  compilation units disagree on a class's own bases/virtual methods (a
  differing `-g` level, a TU that never used a given virtual, or similar
  debug-info trimming), `dwarf_snapshot.py`'s "first definition wins" ODR
  handling now cross-checks the discarded duplicate's own evidence against
  the retained definition's before throwing it away, and marks the
  affected record's `bases`/`virtual_bases`/`vtable` facts as
  known-incomplete rather than confidently complete.
  `compare/vtable_evidence.vtable_transition_is_evidenced` declines to
  treat a resulting difference as a real change, closing the last open
  piece of ADR-063's fact-completeness work (the DWARF half of the
  PDB `vtable` fabrication fix shipped earlier).
