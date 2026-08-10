### Fixed

- **A dropped export the build proved to be vague-linkage is a risk, not a
  break** — `func_vague_export_dropped` (`COMPATIBLE_WITH_RISK`) replaces
  `func_removed`/`func_deleted_elf_fallback` when the *old* build's object
  files show the symbol was emitted into a COMDAT group and the new headers
  still declare the entity. The language requires every using translation unit
  to define such an entity, so an already-linked consumer carries its own copy
  and keeps resolving.

  The proof is the compiler's own record rather than an inference: `is_inline`
  is only the specifier (an earlier attempt keyed on it was reverted), and
  `WEAK` in `.dynsym` does not distinguish vague linkage from an out-of-line
  `__attribute__((weak))` function whose consumers hold no copy. Both
  detectors that observe this event share one leaf predicate, so they cannot
  disagree about it.

  Requires L3 build evidence (`--sources`/`--build-info`) and is ELF-only;
  without proof the removal is reported as a plain break, unchanged. Note that
  the L3 collection path does not yet carry usable object paths on the common
  `compile_commands.json` flow, so the demotion is currently inert there — see
  `AGENTS.md` for what closing that needs.
