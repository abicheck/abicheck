### Fixed

- **The castxml L4 source-ABI extractor no longer treats compiler-synthesized
  implicit special members (default/copy/move constructors, destructor,
  copy/move `operator=`) as public API.** A new `Function.is_compiler_generated`
  field (schema v27) records castxml's own `artificial="1"` marker for any
  function-like declaration; `entity_from_function` now excludes a confirmed
  compiler-generated declaration from the reachable source-ABI surface
  regardless of origin/access. Previously these phantom declarations leaked
  into the L4 declaration-to-binary-symbol match ratio and could trip a
  false-positive `source_binary_provenance_mismatch` finding — reproduced end
  to end against real castxml/g++ for a minimal class with implicit special
  members (6 of 7 exportable declarations never mapped to any exported
  symbol) and confirmed fixed (a clean 1/1). The direct-clang L2 backend was
  already unaffected (it never emits an implicit declaration as a `Function`
  at all) and now stamps `is_compiler_generated=False` explicitly to make
  that guarantee visible on the model.
