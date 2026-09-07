### Added

- **`compare` now detects unversioned exported symbols automatically** —
  the `unversioned_exported_symbol` cross-source hygiene check
  (previously reachable only from the retired-in-progress `scan` command)
  now runs on every `compare` invocation, on both `OLD` and `NEW`
  independently, and reports how it evolved between them
  (`introduced`/`resolved`/`persistent`/`not_evaluated`). It needs only
  the ELF export table and version-definition section already present in
  every ELF snapshot, so it costs nothing on a snapshot without that
  evidence and adds no measurable time to an ordinary `compare` run. No
  new flag: per ADR-068 D4/D5, this is an automatic comparison stage, not
  an opt-in analysis. The finding keeps its ordinary `RISK` default
  verdict; the evolution state is purely descriptive.

