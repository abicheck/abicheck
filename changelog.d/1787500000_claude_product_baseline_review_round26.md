### Fixed

- **`abicheck.binary_utils._canonical_library_key`**: two follow-on gaps in
  round 25's stored-PE-snapshot fix. (1) It matched any `.dll` segment
  anywhere in the (unwrapped) filename, which wrongly case-folded a
  genuinely case-sensitive ELF name that happens to contain the literal
  substring `.dll.` (e.g. a compatibility shim), hiding a real
  case-only-rename break. (2) A stored Mach-O snapshot with a version bump
  (`libfoo.1.dylib.abicheck.json` → `libfoo.2.dylib.abicheck.json`) never
  paired, since `_DYLIB_VERSION_RE` is anchored to the end of string and
  never matched through a snapshot's own wrapper suffix. Both are now
  resolved against the *represented* library name — the stored filename
  with one recognized wrapper suffix (`.abicheck.json`, or the plainer
  `.json`/`.pl`/`.pm` forms) stripped — rather than the raw stored-snapshot
  name, so format/case-folding decisions require a genuine terminal match
  instead of a substring anywhere in the name.
