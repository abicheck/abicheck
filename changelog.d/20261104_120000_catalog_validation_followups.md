### Fixed

- **The clang header frontend now parses a C header as C when the build
  supplies its own `-std`.** The language was selected with `-x c` only
  alongside the default standard, so a compile database's `-std=gnu11` under
  the default `clang++` driver read the header as C++ and failed with
  "`-std=gnu11` not allowed with 'C++'". `-x c`/`-x c++` is now always
  emitted, except when the build forwards its own `-x` language together with
  its `-std` (`extract/headers/clang_header_argv.py`).
- **No more `exported_object_alignment_reduced` for an unchanged global under
  the clang frontend (case61).** The alignment detector now uses the variable's
  compiler-derived `type_abi_fact` alignment as corroboration when no explicit
  alignment was recorded, so link-address placement noise on an unchanged
  `extern int` is suppressed under clang as it already was under castxml. A
  variable spelled with a typedef keeps the address-derived signal, since the
  fact describes the desugared type.
- **An `-fshort-enums` flip on a library with no enums is no longer a risk
  (case103).** When both sides' confirmed public headers (and DWARF, when
  present) declare no enum,
  `enum_size_flag_changed` is kept but demoted to COMPATIBLE, with the reason
  `no-enum-in-compared-surface` (`buildsource/flag_drift_relevance.py`).
- **Adding a class-template parameter default is no longer an API break
  (case224).** For a class template, `template_param_default_changed` now
  fires only for a changed or removed default. A function template's added
  default is still reported: it can make the template viable for a call an
  existing overload served.
- **An exported instantiation's changed default is attributed correctly under
  clang (case229).** The pairing reads template arguments from the symbol's
  mangling. clang's source spelling leaves out the defaulted argument, so it
  missed `default_template_arg_changed` and reported
  `template_param_default_changed` instead.
- **The catalog now gives the compatibility verdict for header-only
  template-default changes (case223/225/227/228).** These cases expect
  `API_BREAK`. The castxml lane, which cannot record defaults, now reports
  XFAIL through a new `known_gap_frontends` scope instead of a PASS on
  `NO_CHANGE`.
