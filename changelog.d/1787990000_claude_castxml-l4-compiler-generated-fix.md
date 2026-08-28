### Fixed

- **The castxml L4 source-ABI extractor no longer treats a compiler-
  synthesized implicit special member (default/copy/move constructor,
  destructor, copy/move `operator=`) with no real exported symbol as public
  API.** A new `Function.is_compiler_generated` field (schema v27) records
  castxml's own `artificial="1"` marker for any function-like declaration.
  `link_source_abi` gives such a declaration one export-match attempt and
  drops it outright on a miss — the common case, a trivial implicit member
  never emitted as its own out-of-line symbol — rather than counting it
  reachable-but-unmatched; an ODR-used implicit member that genuinely does
  have a real weak export (e.g. a public function returning a type by value
  calls its implicit copy/move constructor) is still linked normally, not
  lost (Codex review). Previously every phantom declaration leaked into the
  L4 declaration-to-binary-symbol match ratio and could trip a
  false-positive `source_binary_provenance_mismatch` finding — reproduced
  end to end against real castxml/g++ for a minimal class with implicit
  special members (6 of 7 exportable declarations never mapped to any
  exported symbol) and confirmed fixed (a clean 1/1). The direct-clang L2
  backend was already unaffected (it never emits an implicit declaration as
  a `Function` at all) and now stamps `is_compiler_generated=False`
  explicitly to make that guarantee visible on the model.
