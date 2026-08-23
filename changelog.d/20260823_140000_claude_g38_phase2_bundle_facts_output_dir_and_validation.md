### Fixed

- **`--bundle-facts-out` could still collide with `--output-dir`'s own
  `summary.json` or per-library `<stem>.json` files (Codex review, fresh
  evidence).** The previous `--output`/`--write` collision check couldn't
  see `--output-dir`'s own output paths, since those depend on
  `--output-dir` itself and (for the per-library case) the resolved
  OLD-side library map, neither known at that earlier validation point.
  Added `reject_bundle_facts_out_dir_collision()`, called once the library
  map is resolved and before `--output-dir` is created or written into.
- **A malformed `library_filenames` value in a hand-edited or corrupt
  bundle-facts file was silently coerced instead of rejected (Codex
  review, fresh evidence).** `bundle_facts_from_dict()` converted every
  value with a bare `str(filename)`, so JSON `null` silently became the
  invented basename `"None"` and a number became its own string form —
  with a no-`DT_SONAME` library and cohort checking enabled, replay would
  then derive a fabricated SONAME major from that invented name instead of
  surfacing the invalid input. Added `_validated_filename_map()`,
  mirroring the existing `filesystem_aliases` validation.
