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
  explicitly to make that guarantee visible on the model. A castxml
  constructor/destructor whose real mangled name castxml omitted (a
  `SYNTHETIC_CTOR_KEY_PREFIX`-prefixed/`~`-prefixed internal identity, never
  a real ABI symbol) gets a second, class-level rescue attempt
  (`buildsource/ctor_export_match.py`) against the real export table before
  being dropped, so an ODR-used implicit constructor/destructor with a real
  weak export is preserved too, not just `operator=` (which always carries a
  real mangled name). A source-only link with no export table yet (the
  Flow-2/parallel-baseline `merge` flow) no longer drops these declarations
  either — an empty export set means "not resolved yet", not "confirmed
  absent", so `relink_surface_exports`'s later pass against the real export
  table can still recover them (Codex review).
- **`diff_cxx_rules._read_length_prefixed_name` no longer trips Python's
  integer-conversion digit limit on an untrusted mangled symbol with
  thousands of digits in its length field.** The above ctor/dtor rescue is
  the first caller to feed a binary's own raw exported-symbol strings
  through this parser; it now accumulates the declared length digit-by-digit
  (capped at the input's own length), mirroring the identical guard
  `buildsource/source_link.py`'s own ctor/dtor folder already used, instead
  of `int(s[i:j])`.
