### Fixed

- **castxml constructors/destructors now join the export table and clang's
  node** — castxml records no mangling for a constructor or destructor, so a
  castxml dump keyed each one on a placeholder (`__abicheck_ctor__ns::W(int)`,
  `~ns::W`) that stayed an `unresolved://` graph node joining nothing. The
  identity table (`model/special_member_identity.py`) now pairs such a
  placeholder one-to-one with an exported Itanium variant family and keys it
  on the complete-object spelling clang reports (`C1`/`D1`), with the other
  observed variants (`C2`, `D0`/`D2`) as aliases; the `exports` join treats a
  family's several exports as one entity's. Inline, implicitly declared but
  unexported, templated, and ambiguous overloads stay `unresolved`. No
  finding or verdict changes. `itanium_ctor_dtor_marker_span` moved to
  `model/mangled_name.py` (still importable from `diff_cxx_rules`).
