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
  `FUNC_VISIBILITY_CHANGED`.

### Added

- **Per-finding `surface_facts` in reports (report schema 4.4).** A finding
  with one declaration behind it now states all three surface facts —
  `declared_in_headers`, `in_public_contract`, `binary_exported` — as
  `"true"`/`"false"`/`"unknown"`, so a reader can tell a lost declaration
  from a lost export from absent header evidence. Always complete when
  present: `"unknown"` is spelled out rather than omitted.
