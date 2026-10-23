### Changed

- **A header-only dependency's internal types no longer gate a binary + DWARF
  compare** — a `TYPE_ADDED`/`TYPE_REMOVED` on a record whose declaring file is
  a dependency (outside the library's own declarations) and that no public
  declaration reaches (the exported functions' parameters, and the fields of
  types they name) is out of contract with reason
  `dependency_internal_unreachable`. It is still reported and counted in the
  disposition audit's `scope_reasons`. The library's own changes, and any
  dependency type a public declaration reaches, are unchanged. See ADR-087.
