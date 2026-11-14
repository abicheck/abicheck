### Fixed

- `tag_type_renamed` accepts an export as rename evidence only when its
  mangling actually references the tag type (Itanium source-name in a
  template-argument, parameter or scope position, Mach-O included; MSVC
  `U<name>@`/`V<name>@`), not when the tag's name is a substring: C exports
  such as `ktag_a_count`/`ktag_b_count` no longer fabricate a rename of
  empty tags `ktag_a`/`ktag_b`.
