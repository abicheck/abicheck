### Fixed

- **`bundle_facts_from_dict()` no longer silently corrupts a malformed
  `filesystem_aliases` value (Codex review, fresh evidence).** A string
  value where the format documents a list (e.g. `"libfoo.so":
  "libfoo.so.1"` instead of `"libfoo.so": ["libfoo.so.1"]`) previously hit
  a bare `tuple(aliases)`, which silently iterates the string's
  *characters* rather than raising -- reconstruction then indexed
  single-letter aliases (`"l"`, `"i"`, ...) instead of the real one, so a
  genuine `DT_NEEDED` edge could quietly fail to resolve with no load-time
  error at all. `filesystem_aliases` is now validated as a mapping whose
  values are lists of strings before conversion, raising `ValueError`
  otherwise.
