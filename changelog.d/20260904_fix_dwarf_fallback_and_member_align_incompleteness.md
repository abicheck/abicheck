### Fixed

- **DWARF fallback type resolution now flags incompleteness for named
  tags too** — a tag with no dedicated type-info branch (e.g. a real
  GCC-compiled `std::nullptr_t` field, `DW_TAG_unspecified_type` named
  `decltype(nullptr)`, which often carries no `DW_AT_byte_size` at all)
  previously only marked incomplete when it also had no name — but this
  module has no genuine understanding of such a tag's type semantics
  either way, and a genuinely-absent byte size is indistinguishable from
  an explicit zero. Now flagged unconditionally on reaching this
  fallback.
- **DWARF advanced-channel packing check (`_get_type_align`) now flags a
  struct member with no `DW_AT_type` at all** — mirrors the identical
  basic-channel fix for `_process_member`; the two evidence receipts are
  persisted independently, so fixing one alone didn't make the other
  truthful.
