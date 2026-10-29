### Fixed

- **A retype between ABI-identical fundamental types is no longer an ABI break** —
  a parameter, field, return or variable whose type changed spelling between two
  types the compiler passes, stores and returns alike (`long` to `long long` on
  LP64, `char` to `signed char`, `enum E` to `unsigned int`, `__int128` to
  `unsigned __int128`) was reported as `func_params_changed` / `type_field_type_changed` /
  `func_return_changed` / `var_type_changed` and exited 4. The DWARF and castxml
  extractors now record each fundamental slot's ABI fact (encoding class, size,
  alignment, vector lanes), and such a retype is reported as the new
  `abi_equivalent_type_changed` kind (compatible with risk, exit 0). A sign change
  at 4 bytes or below, a boolean against an integer, an integer against a
  float, `_Atomic` against plain, and a vector whose lane count or element type
  differs (`float4` to `int4`, two floats to a double) stay breaks. A slot without a fact on either side keeps its break, so an
  older baseline classifies as before. A DWARF pointer declared `restrict` is also
  no longer counted one level deeper, and a restrict-only difference is no longer
  reported as a parameter change. The extractor generation is 32 and the snapshot
  schema is 69. A typedef retargeted between ABI-identical types is refined the
  same way (`typedef_base_changed` becomes the compatible retype), and the clang
  frontend (`ABICHECK_AST_FRONTEND=clang`, and the castxml sidecar) records the same
  facts from its builtin types. A `restrict` qualifier on a parameter stays a
  header-tier finding (`param_restrict_added` / `param_restrict_changed`): DWARF
  does not report it, since the declared interface is only in the headers.
