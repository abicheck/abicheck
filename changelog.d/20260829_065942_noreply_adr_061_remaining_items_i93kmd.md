### Changed

- **ADR-061 Phase 5: castxml record-entity parsing (including its
  vtable/RTTI layout walk) split out of `dumper_castxml.py`.**
  `parse_types()`, `build_record_type()`, field/bitfield parsing, and the
  vtable-slot reconstruction (`build_vtable`/`collect_virtual_methods`/
  `inherited_vtable_slots`/`resolved_override_keys`/`vtable_slot_key`) now
  live in `abicheck/extract/headers/castxml/records.py`, the third entity
  module built on that backend's shared context after `enums.py`/
  `functions.py`. `ctx.vtable_slot_root`/`ctx.vtable_slot_extra_roots`
  already lived on `CastxmlParserContext` from the prior `functions.py`
  slice, so this move needed no context-shape change — only relocating
  the code that reads and mutates them; `collect_virtual_methods`/
  `vtable_slot_key` are the first functions in this package to mutate
  shared context state rather than only read it. `_CastxmlParser`'s
  matching methods (including the `@staticmethod` `_parse_bitfield_bits`)
  are now one-line delegations; every existing caller (including tests
  reading a parser's private methods directly) keeps working unchanged,
  and there is no output/snapshot behavior change — verified against the
  real castxml integration suite, including every vtable/RTTI test case.
  Clang's `records.py`, and `templates.py` on both backends, remain open
  for the next slice — see ADR-061's Phase 5 section for the full
  account.
