### Fixed

- **The evidence-adaptive default contract no longer drops findings it cannot place.**
  With no `--contract` stated, a finding the chosen domain could not resolve
  (`UNKNOWN_UNRESOLVED`) was left out of the verdict, so the run read
  `NO_CHANGE` with exit 1 where the same comparison without headers, or before
  contract evaluation became the default, was BREAKING / API_BREAK /
  COMPATIBLE_WITH_RISK. Affected: a lost `_ZTI`/`_ZTV` for a public class when
  the header domain was incomplete, L3 build-option flips, L4/L5 source findings
  (removed macro/inline function/typedef, header-graph rename/move) under an
  `exports` default. Such a finding is now consulted against the observed
  export table and, if still unresolved, scored as `--contract all` scores it.
  A stated `--contract public|exports` keeps its unresolved findings and
  coverage exit unchanged.
- **ELF version definitions and requirements are not contract entities.**
  `symbol_version_node_removed`, `symbol_version_defined_added`/`_removed`,
  `symbol_version_required_added`/`_added_compat`/`_removed` and
  `versioned_symbol_scheme_detected` (whose `symbol` is a version tag or the
  library) now read `NOT_APPLICABLE` in every domain, so a removed version node
  (`version 'X' not found`) scores BREAKING again.
