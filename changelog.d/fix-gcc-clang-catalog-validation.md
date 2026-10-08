### Changed

- **Source-contract changes are classified by direction instead of as
  layout breaks.** A const/volatile behind a parameter's pointer or
  reference was suppressed entirely; gaining one on the single pointee
  (`char *` → `const char *`) is now `param_pointee_qualifier_added`
  (`COMPATIBLE_WITH_RISK`: direct calls compile, a `void (*)(char *)`
  function-pointer consumer does not), and a qualifier lost or gained below
  the first level is `param_pointee_qualifier_changed` (`API_BREAK`).
  `FUNC_PARAMS_CHANGED` still stays silent for both. Adding `restrict` to a
  parameter is now `param_restrict_added` (`COMPATIBLE_WITH_RISK`: an
  overlapping caller gets different results from an optimised callee);
  `param_restrict_changed` now means removal only.
- **Verdict re-scoring for source-only changes.** `typedef_removed`,
  `type_removed` and `tag_type_renamed` are `API_BREAK` (a typedef or type
  has no symbol; removed functions/instantiations are reported, and carry
  the binary verdict, on their own). `field_became_const` and
  `field_lost_mutable` are `API_BREAK`; `field_became_volatile` and
  `field_lost_volatile` are `COMPATIBLE_WITH_RISK`.
  `func_visibility_protected_changed` is `COMPATIBLE_WITH_RISK` (an
  interposed hook stops intercepting library-internal calls). Suppression
  or policy files that named `param_restrict_changed` for an *added*
  restrict should name `param_restrict_added`.
- **Audit reports name each cross-source finding's corroborating
  providers** (`findings[].providers` in `compare --no-baseline` JSON).

### Fixed

- A by-value field qualifier change (`int` → `const int`) no longer also
  reports `type_field_type_changed` (BREAKING); size and offset are unchanged.
- A compiler-generated special member (CastXML `artificial="1"`) that no
  export table confirms is reported as `inline_function_removed` when it
  disappears, never as a binary `func_removed`.
- A parameter respelled from a typedef to the canonical type its unchanged
  Itanium mangled name encodes (`size_type` → `std::size_t`) is no longer
  `func_params_changed`.
- Clang header backend: a top-level `restrict` no longer stays in the
  parameter's type spelling (it produced a BREAKING `func_params_changed`
  with the wrong mechanism alongside the real restrict finding).
- CastXML drops GNU x86-64 `ms_abi`/`sysv_abi` from its output; the
  calling convention is now recovered from the declaration it locates, so a
  GCC-built library (no `DW_AT_calling_convention`) reports
  `calling_convention_changed` when headers are supplied.
- C23 `_BitInt(N)`: Clang's DWARF names every width just `_BitInt`, and
  CastXML 0.7 emits it untyped. DWARF base types now keep the width
  (`_BitInt(N)` from `DW_AT_bit_size`, else the storage width),
  `bit_int_width_changed` also reads header-scoped debug layouts, and the
  type graph no longer reads the width as a referenced type (a bogus
  `declaration_renamed` `64` → `128`).
