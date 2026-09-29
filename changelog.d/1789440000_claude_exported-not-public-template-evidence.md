### Fixed

- **`exported_not_public` / `func_added_elf_only` no longer misjudge template
  and conditionally declared exports** (found validating oneCCL and oneDNN).
  An export that instantiates, or is a member of, a template a public header
  declares (a member template of a public class, a member of a public class
  template) is now accounted `public_template_instantiation` instead of a
  "hide it" leak, and `func_added_elf_only`/`func_removed_elf_only` name that
  public template rather than claiming the symbol is declared in no public
  header. The internal-namespace test reads the entity's own scope through
  one structural walker (`model/export_entity_name.entity_name_components`),
  so a `detail::` type in a return type or template argument no longer marks
  `*_attr::set`/`get` internal; the shared `skip_template_args` primitive now
  balances namespaced-enumerator literals (`LN…E0E`), argument packs,
  expressions and substitutions. A `std::`/vendored template instantiated over
  the library's own types (`std::_Sp_counted_deleter<dnnl::impl::stream*, …>`)
  is the library's own vague-linkage copy (`own_type_instantiation`), not a
  statically linked libstdc++. An export whose name the public header *text*
  declares inside an `#if`/`#ifdef` region or in an `--exclude-header`-excluded
  header now says so, at low confidence, instead of asserting absence.
- **A cross-source hygiene finding's report `operation` reflects how it
  evolved.** `introduced` reads `added`, `resolved` reads `removed` and
  `persistent` reads the new `unchanged` value, instead of every such finding
  reading `modified` (report schema 5.7).
