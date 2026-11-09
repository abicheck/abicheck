### Fixed

- **A symbol moved to a new version node without keeping its old version is
  now `BREAKING`.** `symbol_moved_version_node` was a risk while consumers
  linked against the old version failed to load (`undefined symbol: g,
  version LIBEDIT_1.0`) whenever the old node still held other symbols. The
  kind is now emitted only when the new library no longer defines the symbol
  at its old version, and is `BREAKING`; a move that keeps the old version as
  a non-default alias stays the compatible `symbol_version_alias_changed`.
  Found by the generated edit corpus.
