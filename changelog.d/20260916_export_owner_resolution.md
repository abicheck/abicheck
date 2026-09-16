### Fixed

- `func_removed_elf_only`'s constructor/destructor exemption no longer drops a
  real export loss. It resolved the owning class through `type_by_name()`,
  which is keyed by the **unqualified** record name, so the qualified
  candidate could never match and a bare-name collision (two classes named
  `Base` in different namespaces) let an unrelated declaration suppress a
  genuine loss. The join now runs through the fully-qualified ctor/dtor
  declaration keys, with explicit `unique`/`ambiguous`/`unresolved`/
  `unsupported` states and a reason code
  (`abicheck/compare/export_owner_resolution.py`).
- The exemption also required only that the owning class still be *declared*,
  which hides a real ABI break: measured with a loader, a client built once
  against OLD fails against NEW with `undefined symbol` when an out-of-line
  constructor is localized, and again when a weak `extern template`
  instantiation's constructor is localized. It now additionally requires the
  owner to be a non-template whose special members are declared `inline` and
  unchanged on both sides — the one fact that separates optimization-level
  emission churn from a loss no consumer can make up for itself.
- Itanium inheriting constructors (`CI1`/`CI2`, C++11 `using Base::Base;`)
  now parse, with the **derived** class as their owner rather than the base
  named by the base-type production that follows the code. They previously
  failed `itanium_scope_components` outright, so every ownership consumer —
  public-surface scoping, internal-namespace classification, `symbol_origin` —
  fell back to the conservative textual scan for them.
- `tests/test_cross_compiler_fp.py`'s C++ `-O0` vs `-O2` case declared its
  constructors out-of-line in the header while defining them in-class in the
  source, which makes the `-O2` build a genuine break (runtime-verified), not
  a false positive. Its header now matches its source and the out-of-line
  variant is kept as an explicit true-positive control.
