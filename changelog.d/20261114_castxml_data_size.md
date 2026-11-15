### Fixed

- **castxml records a record's data size, so a tail-padding reuse change is
  reported** — the castxml header backend now derives `data_size_bits`
  (dsize) per the Itanium C++ ABI: `sizeof` for a class that is POD for
  layout, otherwise the end of its last non-empty base or member (bit-fields,
  empty bases, the vptr included). Adding a member inside a non-POD class's
  tail padding now reports `TAIL_PADDING_REUSE_CHANGED` at castxml depth (and
  in a castxml+DWARF dump) instead of `COMPATIBLE`. Where GCC and Clang
  disagree on POD-for-layout (a constructor defaulted or deleted on its first
  declaration, a `[[no_unique_address]]` member) or a needed fact is missing
  (a virtual base, an incomplete type), the data size stays unknown and no
  finding is made.
