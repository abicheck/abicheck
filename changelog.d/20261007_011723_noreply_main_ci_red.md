### Fixed

- **The default contract no longer lets a break stop gating** — after
  contract evaluation became the default (ADR-049 Phase 7), a removed ELF
  version node (`symbol_version_node_removed` and the other version-node
  kinds, whose subject is a version name no contract domain can place) read
  `UNKNOWN_UNRESOLVED` and scored as a pass, and adding `-H` turned a lost
  undeclared `_ZTV`/`_ZTI`/`_ZTS` export from BREAKING into exit 1. Version-node
  findings are now `NOT_APPLICABLE` (scored by policy, like `SONAME`), and the
  evidence-adaptive `public` default re-asks the observed export table for every
  finding the headers could not decide, not only an explicit non-commitment.
  When every compared side carries `--sources`/`--build-info` evidence, a
  finding the adaptive default still cannot place (build-option flips, removed
  public macros/inline functions/typedefs, new internal dependencies) is judged
  on `all` instead of being dropped as unresolved. Supplying optional evidence
  never makes a verdict cleaner (ADR-049 revision 2026-10-07).
