### Fixed

- **`diff_types` no longer diffs typedefs through the bare-name-collapsed
  `AbiSnapshot.typedefs` dict when the qualified twin is available.**
  `AbiSnapshot.typedefs` is keyed by unqualified alias name on both header
  backends, so two unrelated member typedefs sharing a bare spelling in
  different classes (e.g. `X::impl_value_t` declared on many unrelated
  classes — an ordinary STL-container-shaped pattern) collapse onto one dict
  entry, with whichever declaration the backend visited last winning. Diffing
  that collapsed dict directly could flip the surviving entry's recorded
  value whenever an *unrelated* class gained or lost its own same-named
  alias, fabricating a spurious `typedef_base_changed` for a typedef that
  never itself changed. `_diff_typedefs` now prefers
  `AbiSnapshot.typedefs_qualified` (schema v25, unique per declaration)
  whenever both sides populate it, falling back to the legacy bare-keyed
  diff for a DWARF-only or pre-v25 snapshot.
- **The direct-clang header-AST backend now strips an embedded absolute
  source path out of a field/parameter/variable/function's own recorded
  type spelling.** A lambda closure type used directly as a template
  argument (e.g. a function template instantiated with a lambda,
  `call_with([]{})`) makes clang print that instantiation's own parameter
  type as `"(lambda at <path>:<line>:<col>)"` — confirmed against real
  Clang 18 output. Left unstripped, that absolute, checkout-dependent path
  leaked into `TypeField.type`/`Param.type`/`Variable.type`/
  `Function.return_type`, so two checkouts of the identical, unchanged
  declaration could disagree and manufacture a spurious finding on the
  field/parameter/variable/function carrying it — the same class of bug
  `dumper_castxml.py`'s own `strip_anonymous_type_location` (commit
  3aca095) guards against for its `RecordType`/`EnumType` `name`/
  `qualified_name`, reached here through the clang backend's shared
  `qualType`-spelling choke point instead.
