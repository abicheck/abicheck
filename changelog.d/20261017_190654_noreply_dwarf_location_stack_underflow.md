### Fixed

- **A malformed DWARF member location is now unknown, not a plausible offset** — the
  location-expression evaluator had no failure channel. A stack-consuming op on an
  insufficient stack (`DW_OP_plus` with only the implicit base) fell through to a
  value: `[0x22]` evaluated to `34` and `[(0x22, 0)]` to `0`. Unsupported opcodes,
  non-opcode items, and truncated LEB128 operands were skipped or read as partial
  values. `decode_member_location` now returns `None` for any expression with no exact
  offset, and every layout producer records that field's offset as unknown (or, for an
  anonymous member, the enclosing record's layout is not recorded). A guessed offset
  could manufacture a field-offset break or hide a real one. Well-formed expressions
  are unchanged. Because the same input now records a different fact, the extractor
  generation is bumped from 3 to 4, so cached packages from older builds are still read,
  with a generation-drift notice.
