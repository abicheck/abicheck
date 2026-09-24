### Performance

- **The clang AST cache stores a compact, ASCII-only document.** clang's
  pretty-printed `-ast-dump=json` output was copied into the cache
  byte-for-byte; ~70% of it is indentation, and a single non-ASCII character
  (one em dash in a doxygen comment, on the measured 1.0 GB entry) made
  CPython decode the whole document at 2 bytes per character. The output is
  now streamed through `storage/json_compact.py` (strip each line, escape
  non-ASCII as `\uXXXX`) before it is parsed, offered to the header-graph
  projection and renamed into the cache, so both the cold parse and every
  warm read see ~1/3 of the bytes at 1 byte per character. Measured on a
  small libstdc++-heavy library: cache entry 140 → 44 MB, dump peak RSS
  617 → 357 MB, warm dump 6.9 → 5.9 s; the JSON value is unchanged
  (differentially tested against `json.loads`). Existing pretty-printed
  entries are still read as before.
- `find_by_value_types` derives each opaque candidate's leaf spelling once
  per call instead of once per declaration × candidate (4.2M
  `depth_aware_bare_name` calls over 1,682 names on a oneDAL compare).
- `qualified_declaration_name` reads the demangle batch cache directly
  instead of issuing one single-name `demangle_batch` per declaration
  (~225k calls over ~37k names). New `demangle.demangle_one_batched`.
- The clang template-parameter defaults index reuses the names index its
  caller already built, removing one of the four whole-document walks per
  root (and the same double walk in `build_specialization_index`).
- `_strip_bare_anonymous_type_location` skips its quote scan and regex for
  names holding none of `lambda`/`unnamed`/`anonymous` (97% of ~548k calls).
- **Memory trace:** AST intake is now bracketed (`ast.intake:start`/`:done`,
  cache and fresh paths) — the first event used to land ~35 s after a clang
  run's peak — every sample records `parent_rss_peak_bytes` (`VmHWM`), and
  `tree_processes` falls back to a `/proc/*/stat` parent scan on kernels
  without `/proc/<pid>/task/*/children`, where a 10-process fan-out was
  reported as one process.
