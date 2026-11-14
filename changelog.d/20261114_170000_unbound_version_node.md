### Fixed

- Dropping an ELF version node that no symbol was bound to (a
  `.gnu.version_d` entry no consumer can have recorded) is no longer graded a
  binary break: `symbol_version_defined_removed` reads `COMPATIBLE_WITH_RISK`
  for it, with modulation reason `version_node_bound_no_symbol`. A node that
  binds a symbol, or a snapshot without version-binding facts, keeps
  `BREAKING`.
