### Changed

- **A header-only dependency's internals no longer gate a compare with
  headers** — on a header-derived snapshot (`-H`, stored or live), a finding
  on an entity a dependency declares (outside the library's own include
  roots and namespaces) that no public declaration names and the library's
  binary does not define is out of contract with reason
  `dependency_internal_unreachable`: template instances, hidden-friend
  operators, inline variables, member typedefs, tag renames, re-routed
  overload sets and weak template instances of, for example, a SIMD library
  rebuilt for another ISA. They are still reported and counted in the
  disposition audit's `scope_reasons`. The library's own changes, and a
  dependency type its declarations use (with its size and layout), keep
  gating. The rule now also applies under the default public contract,
  where it previously never fired; only a symbol declared public with
  `--public-symbol` or a post-manifest is exempt. See ADR-089's 2026-10-25
  amendment.
