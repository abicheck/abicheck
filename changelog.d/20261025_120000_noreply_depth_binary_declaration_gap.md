### Fixed

- **`compare --depth binary` on header-built snapshots no longer reads falsely
  clean** — a header-built snapshot does not store the debug-info
  function/variable signatures, typedefs or record relations (bases, vtable
  slots, member access, field qualifiers) that a binary-only dump reads. Its
  `binary` projection therefore could not report a return or parameter type,
  typedef, vtable or base change, and read `NO_CHANGE` with `complete`
  assurance where a binary-only dump of the same libraries reads `BREAKING`.
  Such a run now reads `analysis_assurance.status: partial` and names each
  missing kind of evidence in its notes ("not assessed at depth binary: …").
  The verdict and exit code are unchanged. To get these findings at binary
  depth, dump without headers (or compare the libraries directly).
