### Fixed

- **A failed optional header pass no longer reads as a clean comparison.**
  When the castxml backend's clang join failed (no clang, a clang parse
  error), a vector, `_Complex` or `_BitInt` type stayed spelled
  `Unimplemented` on both sides, so `float _Complex` -> `double _Complex`
  compared `NO_CHANGE` with `analysis_assurance` `complete` and exit 0. That
  failure, a failed const-variable-template join, a failed clang
  explicit-instantiation text pass, a failed clang record-layout pass, and any
  declaration type left `Unimplemented`, are now recorded under
  `dump_provenance.extraction_limitations`, so the run reads `partial` and
  names the gap, live and from a stored snapshot. A multi-TU manifest dump
  keeps every unit's limitations. A dump whose optional pass failed is no
  longer stored in the snapshot cache, so the next run retries the pass
  instead of reusing the gapped snapshot.
