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
  table can still recover them (Codex review). That later relink pass now
  also applies the identical `compiler_generated`-miss drop rule
  `_route_declaration` applies at first link (`ctor_export_match.
  rematch_declarations`, shared by both call sites) — previously a
  candidate kept by the empty-export first link stayed in
  `reachable_declarations`/`decls_without_symbol` forever, even once
  relinked against a real export set that genuinely never mentions it
  (Codex review).
- **A `compiler_generated` declaration now gets `_demangled_rematch`'s
  second-tier ABI-tag/substitution-drift rescue too, not just an ordinary
  declaration.** Both drop paths (first link's `_route_declaration`,
  the relink's `rematch_declarations`) previously excluded an unmatched
  `compiler_generated` candidate *before* `_demangled_rematch` ran over
  `reachable_declarations` — since that function only rematches entities
  already in the list, a dropped candidate could never reach it. So an
  implicit special member whose real mangled spelling differs only
  textually from its export (e.g. castxml's own `_ZN1AaSERKS_`
  self-substitution form vs. the export's `_ZN1AaSERK1A`, equivalent once
  demangled) was wrongly dropped even though the export genuinely exists.
  Fixed by deferring the drop to a new third tier
  (`ctor_export_match.drop_unmatched_generated_declarations`), run once,
  after `_demangled_rematch` in both `link_source_abi` and
  `relink_surface_exports`, instead of inline during routing (Codex
  review).
- **`relink_surface_exports` now refreshes `coverage["reachable_declarations"]`
  after its deferred generated-candidate drop, instead of leaving it at the
  empty-export first link's unfiltered stamp.** `crosscheck.
  _surface_boundary_counters` prefers that coverage value over the live
  `reachable_declarations` list length whenever it's nonzero, so a stale
  count kept reporting the removed phantom declarations as still present
  on the surface even after the relink correctly dropped them (Codex
  review).
- **The ctor/dtor owner-index rescue now also recognizes MSVC-mangled
  plain constructor/destructor exports (`??0Widget@@...`/`??1Widget@@...`),
  not just Itanium.** On a Windows/MSVC L4 run (castxml's own
  `--castxml-cc-msvc` emulation mode), an ODR-used implicit special
  member's real export is Microsoft-mangled -- `itanium_scope_components`
  alone never recognizes it, so the rescue previously dropped the
  candidate and left the genuine export unmatched in `symbols_without_decl`
  on that platform. Vector/scalar deleting destructors (`??_E`/`??_G`) and
  other clone forms remain unrecognized, the same conservative-miss bias
  already documented for a templated Itanium owner (Codex review).
- **Known, accepted residual on the ctor/dtor rescue above**: the rescue is
  class-level, not per-overload — it asks "does this class have *any*
  matching ctor/dtor export at all", not "does *this specific* candidate
  (default/copy/move) have one". So a class whose only real export is its
  implicit copy constructor still keeps its default- and move-constructor
  candidates too, each recorded as reachable-but-unmatched rather than
  dropped, and the real export itself stays unmatched in
  `symbols_without_decl` (Codex review). Resolving the actual overload-to-
  export mapping needs decoding an Itanium ctor/dtor's mangled parameter
  types structurally and comparing them against castxml's own spelled
  parameter list — a materially larger parser than this fix's owner-scope
  matching, not attempted here. The chosen direction (keep, unmatched) is
  the safer of the two failure modes available: per this package's own
  ADR-028 D3 rule ("L3/L4/L5 evidence must never silently delete a
  genuine declaration"), a visible-but-unmatched candidate is preferable
  to the pre-fix behavior of silently vanishing it from the surface
  entirely.
- **`diff_cxx_rules._read_length_prefixed_name` no longer trips Python's
  integer-conversion digit limit on an untrusted mangled symbol with
  thousands of digits in its length field.** The above ctor/dtor rescue is
  the first caller to feed a binary's own raw exported-symbol strings
  through this parser; it now accumulates the declared length digit-by-digit
  (capped at the input's own length), mirroring the identical guard
  `buildsource/source_link.py`'s own ctor/dtor folder already used, instead
  of `int(s[i:j])`.
- **The ctor/dtor rescue's owner-index now strips Itanium `[abi:tag]`
  annotations before matching.** `itanium_scope_components` renders an
  ABI-tagged owner (a real `__attribute__((abi_tag("v1")))`) as
  `"Widget[abi:v1]"`, but castxml's own synthetic ctor/dtor key encodes
  only the plain source-level class name it parsed, never the tag — so an
  ODR-used implicit constructor/destructor of an ABI-tagged public class
  previously failed the owner-index lookup and was wrongly dropped even
  though its real weak `C1`/`C2`/`D1`/`D2` exports genuinely existed
  (Codex review).
- **Known, accepted residual on the empty-export-set "unresolved, not
  confirmed-miss" rule**: `bool(exported)` is the only signal
  `should_drop_generated_candidate` has for "has the export table
  actually been resolved" — `link_source_abi`/`relink_surface_exports`
  accept a bare `Iterable[str]` with no separate resolved/unresolved
  flag, so a genuinely zero-export dynamic library (unusual but real —
  e.g. an executable with no public symbols, or a fully
  LTO/dead-code-eliminated `.so`) is indistinguishable from "the binary
  side hasn't been linked yet", and every generated candidate is kept
  rather than dropped in that case too (Codex review). Closing this needs
  a real tri-state export-resolution signal threaded through both
  functions' public signatures and every one of their callers — a
  genuine API-shape change, not a follow-up to this predicate.
- **The whole-snapshot disk cache (`snapshot_cache._SNAPSHOT_CACHE_VERSION`)
  is bumped alongside `CASTXML_EXTRACTOR_VERSION`.** That constant gates a
  separate, cross-process cache from `AbiSnapshot.SCHEMA_VERSION` -- a warm
  cache entry from before this fix would replay `is_compiler_generated=None`
  on every declaration (silently masking this whole fix) even though the
  entry re-serializes under the new schema version on read, since
  `SCHEMA_VERSION` only gates the on-disk snapshot JSON shape, not whether a
  cached snapshot's own content is stale relative to the extraction logic
  that produced it (Codex review).
