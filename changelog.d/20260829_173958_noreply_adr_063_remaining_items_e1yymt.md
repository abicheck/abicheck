### Fixed

- **`dumper_hybrid.py` no longer leaves a stale identity carrier after
  rewriting a declaration's mangled name.** Reconciling a castxml ctor/dtor
  synthetic placeholder key to clang's real mangling, and normalizing a
  Mach-O linker symbol's leading underscore, both rewrote `Function`/
  `Variable.mangled` without updating the parallel `entity_id` carrier
  (ADR-063 Phase 2) — leaving it pointing at a stale or nonexistent
  spelling. Fixed via a new, general `model.identity.with_mangled_name`
  helper.
