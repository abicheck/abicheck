### Fixed

- **PE `__vectorcall` exports keep a leading underscore that is part of the
  name.** The export-symbol identity decoder stripped an optional `_` before
  `name@@N`, giving `_f@@8` (function `_f`) and `f@@8` (function `f`) one
  entity id. MSVC decorates `__vectorcall` as `name@@N` with no prefix on any
  machine; the decoder now keeps the underscore, agreeing with
  `pe_c_decoration_base`.
