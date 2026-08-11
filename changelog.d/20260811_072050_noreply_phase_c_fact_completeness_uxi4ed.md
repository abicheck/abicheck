<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **`EnumType.underlying_type` is now populated on the castxml (and hybrid)
  header backend** — previously only the direct-clang backend read the
  compiler-resolved underlying integer type, so a castxml-produced enum
  silently kept the dataclass default `"int"` regardless of its real
  underlying type (a fixed `enum E : short`, or the implementation-chosen
  type for an unfixed enum). This made `tu_merge.py`'s cross-TU ODR
  agreement check for enums trivially pass on castxml input even when two
  translation units genuinely disagreed on an enum's underlying type.
