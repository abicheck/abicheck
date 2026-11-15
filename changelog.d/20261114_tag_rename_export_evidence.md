### Fixed

- **`tag_type_renamed` no longer depends on the header backend** — the
  re-mangled explicit instantiation over an empty tag (`kfam<ktag_a>()` ->
  `kfam<ktag_b>()`) is an export with no declaration when the template is
  declared generically, so the gcc-plugin and clang header backends modelled
  no function for it and reported a bare `func_removed_elf_only` /
  `func_added_elf_only` pair. The detector now also reads the binary's export
  table, and no longer counts castxml's synthetic special-member placeholders
  (`__abicheck_ctor__ktag_a()`, `~ktag_a`), which re-keyed with the tag name
  for any two swapped empty tags and were the only reason the castxml path
  matched.
