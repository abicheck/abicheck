### Changed

- The clang-backend DWARF layout backfill now pairs a header record with a
  DWARF record under the same rule the L1 debug-type join uses (qualified
  spelling equality, non-contradicting layout, mutually unique), shared from
  the new `abicheck/model/debug_type_match.py`. The previous bare-name /
  last-`::`-segment matcher with field-name corroboration is deleted: it
  could hand a record the layout of a same-leaf type in another scope, and
  it refused every fieldless record (tag types, interface-only classes),
  reporting a false layout `mismatch`. Measured over the 142 catalog dumps
  and yaml-cpp 0.8.0 (clang backend, `-g`): no record lost its backfill,
  61 fieldless records gained one.
