### Fixed

- **Split the three facts `Visibility` conflated, so a lost export is no
  longer reported as a removed source declaration.** A declaration exists
  in the parsed headers, it belongs to the promised public contract, and a
  binary symbol is exported are three independent observations; one
  `Visibility` value answered all three at once, with no state for the
  ordinary "declared and not exported" combination and no way to say "we
  did not look". A `-fvisibility=hidden`/version-script change that stopped
  exporting a byte-identical declaration therefore read as a removed source
  API, and a public inline function with no export was misfiled out of the
  public surface. Each fact is now its own `Fact[bool]` on
  `Function`/`Variable` (snapshot schema v46), every producer answers only
  what it observed — a headerless snapshot says "declaration not
  established", never "no declaration" — and each detector reads the fact
  it actually needs (`abicheck/model/surface_facts.py`). The export change
  is still reported, on its own binary axis, as
  `FUNC_VISIBILITY_CHANGED`/`VAR_VISIBILITY_CHANGED`.

- **The legacy bridge no longer reads header *absence* out of
  `Visibility.ELF_ONLY`.** A pre-v46 snapshot's enum cannot establish that
  fact in either direction: both header-AST backends assign `ELF_ONLY` to a
  declaration they parsed out of a header whose symbol landed in `.symtab`
  rather than the dynamic table, and a headerless snapshot reaches the same
  member with no header parse at all. The record's own header provenance
  answers it instead, for every member alike; which member it was remains
  answerable separately (`is_export_table_only_record`), so the ELF-only
  removal kind is unaffected.

### Added

- **Per-finding `surface_facts` in reports (report schema 4.4).** A finding
  with one declaration behind it now states all three surface facts —
  `declared_in_headers`, `in_public_contract`, `binary_exported` — as
  `"true"`/`"false"`/`"unknown"`, so a reader can tell a lost declaration
  from a lost export from absent header evidence. Always complete when
  present: `"unknown"` is spelled out rather than omitted.

- **`func_export_added`/`var_export_added` — a gained binary export is now
  recorded instead of dropped.** With the declaration present on both
  sides, a version script that *starts* exporting an existing declaration
  makes the pair match, so no added-symbol path sees it and the run would
  otherwise report nothing at all for a real, observed change to the export
  table. Both kinds are compatible additions; they are the mirror of the
  loss-side visibility kinds above.
