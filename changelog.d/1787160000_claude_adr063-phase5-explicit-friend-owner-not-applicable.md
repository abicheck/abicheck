### Fixed

- **DWARF's `is_explicit_fact` now distinguishes "confirmed not explicit"
  from "explicit is inapplicable."** `dwarf_utils.attr_bool()` returns
  `False` (never `None`) for a missing `DW_AT_explicit` attribute, and the
  compiler only ever emits that attribute on a ctor/conversion-operator DIE
  in the first place — so a bare read couldn't tell a genuinely implicit
  ctor/conversion operator (real evidence) apart from an ordinary
  method/free function/destructor, where the specifier doesn't apply at
  all. Eligibility is now derived from the mangled name's own Itanium
  ctor/conversion-operator encoding (`model.mangled_name.
  itanium_scope_components`), matching the `NOT_APPLICABLE`-vs-
  `NOT_COLLECTED` distinction the castxml/clang producers already draw.
- **`hidden_friend_owner_fact` now reports `NOT_APPLICABLE` for an ordinary
  (non-friend) function, in both header backends**, instead of falling
  through the generic bridge into `NOT_COLLECTED` — an owner is
  conceptually inapplicable when a function isn't a hidden friend at all,
  not a missing-evidence gap, the same shape as the existing
  `is_explicit`/`is_override` fix.
