### Fixed

- An export added outside every version-script node is now reported as an
  introduced `unversioned_exported_symbol`. GNU ld binds such a symbol to the
  base version, which the check skips for pre-script API; the comparison now
  tells the two apart, flagging a base-bound export only when the old library
  did not export it.
