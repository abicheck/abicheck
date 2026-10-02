### Changed

- One evidence-merge rule, `abicheck.model.evidence_merge` (unknown ⊕ absent =
  unknown; only a completed read yields absent). A cross-TU merge where only
  some translation units captured `contract_attributes` now records the fact
  as `PARTIAL` instead of `PRESENT`; cheap BTF/CTF/DWARF section probes report
  a failed probe as `FAILED` instead of "no section"; the bundle export check
  reads pre-v46 visibility through `surface_facts.is_dynamically_exported`.
- A missing OLD Python API surface (no `.pyi` recovered) is now an unknown
  baseline, not an empty one: the `python_api` detector is disabled with a
  coverage warning instead of reporting every NEW function and class as
  added. A NEW stub that fails to parse is still reported.

### Fixed

- An unread PE or Mach-O export table (a default or parse-failed block) no
  longer reports every export of the other side as removed or added — an
  identical pair compared `BREAKING`. A removed variable's export is no longer
  re-reported as `func_removed` on PE/Mach-O.
- A DWARF struct whose anonymous member's type cannot be read is no longer
  recorded with that member's fields missing (which the layout diff reported
  as removed fields); its layout is left unread.
- With an unknown OLD Python API surface, a changed function signature no
  longer reads as a clean addition (an API break lost with no stated gap).
