### Fixed

- **The clang header backend now reports size and offset changes that DWARF
  could not describe.** It asks clang for the record layout it computes
  (`-fdump-record-layouts` over a `sizeof` probe), records an explicitly
  instantiated class template (`extern template class Buffer<int>;`) as its
  own `Buffer<int>` record, and records a field declared through an alias
  template (`pimpl<T>`) by the alias target's type. Catalog cases 17, 43, 77
  now report `type_size_changed`/`type_field_offset_changed` under clang, and
  case80 reports `type_field_type_changed`. Clang snapshots are re-dumped
  (extractor generation 37).
- **`internal_type_leaks_via_public_api` now fires when the public
  declaration spells the internal type with a partial scope**
  (`: public detail::Base`), as clang records it (catalog cases 74–76 under
  clang). The match is made only when exactly one record has that name.
