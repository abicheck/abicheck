### Fixed

- **A public inline function's local static is judged by its function** — a
  function-local static (`_ZZ…`, and its `_ZGVZ…` guard) is exported by every
  DSO that instantiates its function but is not a declaration, so the
  default public contract excluded findings on it (such as
  `symbol_binding_lost_unique` after `-fno-gnu-unique`) with the reason
  `not-exported`, although the symbol is exported. Its membership now follows
  its enclosing function's, resolved only to a symbol the snapshots know; a
  local static behind a private header stays out of contract. The template
  form of the same case (case180) is still excluded and is recorded as a
  known gap.
