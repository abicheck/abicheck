### Fixed

- **A symbol moved to a new version node without keeping its old version is
  now `BREAKING`.** `symbol_moved_version_node` was a risk while consumers
  linked against the old version failed to load (`undefined symbol: g,
  version LIBEDIT_1.0`) whenever the old node still held other symbols. The
  kind is now emitted whenever a symbol the new library still exports is no
  longer defined at a version the old library defined it at — its old default
  or a retained compat version (`foo@V1` beside `foo@@V2`), which read
  `NO_CHANGE` before — and is `BREAKING`; a move that keeps every old version
  stays the compatible `symbol_version_alias_changed`. A public symbol whose
  name merely looks private (`private_data`) is no longer demoted by the
  internal-version-node rule. ADR-011 amended. Found by the generated edit
  corpus.
