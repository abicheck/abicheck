### Fixed

- `scan` no longer discards DWARF type evidence at the `headers`/`build`
  evidence rungs when no headers were supplied. The rungs skip a full debug
  parse on the expectation that the public-header AST supplies type layout
  instead; with no headers given, the run reached neither source and silently
  reported no type-level findings while still naming the rung it was asked
  for — `scan lib.so --against old.so` returned `NO_CHANGE` where
  `compare old.so lib.so` on the identical pair reported `type_size_changed`
  and exited 4. The shortcut is now conditional on that substitute evidence
  actually being present.
