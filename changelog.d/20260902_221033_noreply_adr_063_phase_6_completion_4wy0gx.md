### Added

- **`AbiSnapshot.semantic_ir` is now populated for a header-less, DWARF-only
  `dump()`/`compare()`** (ADR-063 Phase 6's fifth slice) — the first
  non-header-AST producer. `dwarf_snapshot.build_snapshot_from_dwarf`'s
  functions/variables/types/enums/typedefs already carry a real `entity_id`
  since ADR-063 Phase 2; `extract.semantic_normalizer.normalize_header_ast`
  is now also called for that data (via a new
  `dumper_elf_fallback._dwarf_semantic_ir`), with two DWARF-specific
  `cv_qualification` carve-outs: a function's is unconditionally
  `NOT_COLLECTED` (DWARF's own DIE walk never reads a method's own
  const/volatile qualifier, so reporting a confirmed empty tuple would
  misrepresent "never looked" as "confirmed not const"), and a variable's is
  read from the already-extracted, structurally-sound `Variable.is_const`
  field rather than a text scan over its type spelling — verified against a
  real compiled fixture that DWARF's own type-name reconstruction renders a
  const pointer (`int* const`) and a pointer to const data (`const int*`)
  with the *identical* text, so only the structural field can tell them
  apart. DWARF's own `constant_entity_ids` stays empty (a constexpr
  initializer is a header-AST-only fact DWARF does not carry), so no
  `CONSTANT`-kind occurrence is ever produced for a DWARF-only snapshot.
  `snapshot_cache._SNAPSHOT_CACHE_VERSION` bumped (26→27) so a snapshot
  cached by an older abicheck build does not silently keep serving
  `semantic_ir=None` forever.
