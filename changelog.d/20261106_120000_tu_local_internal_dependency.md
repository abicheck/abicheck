### Fixed

- **A public function using its own `.cpp`'s private state no longer reads as
  a new internal dependency** — `public_api_internal_dependency_added` and
  `public_to_internal_dependency` fired at build-source depth when a public
  function's body began using an entity with internal linkage defined in its
  own source file (an anonymous-namespace cache, a file-scope `static`, or a
  type declared there; catalog case232). No consumer or other translation
  unit can name such an entity. The clang type-graph pass now marks these
  graph nodes `tu_local`, an Itanium internal-linkage mangled name
  (`_GLOBAL__N_`, `_ZL…`) counts as the same evidence for other producers,
  and neither is an internal-dependency target any more. A dependency reached
  *through* one is still reported, and an internal-linkage entity defined in
  a header, which every includer compiles, still counts.
- **`std::size_t` and other `std::` aliases of fundamental types are no
  longer reported as STL types passed by value** —
  `public_api_exposes_stl_by_value` fired on clang dumps only, because clang
  keeps the as-written `std::size_t` where castxml writes `unsigned long`
  (catalog case17).
