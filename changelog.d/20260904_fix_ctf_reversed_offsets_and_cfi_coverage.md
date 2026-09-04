### Fixed

- **CTF parser now rejects a header with reversed section offsets**
  (`type_off > str_off`) — previously accepted, since
  `data[type_start:type_end]` with `type_start > type_end` silently
  yields an empty slice rather than raising, discarding every type
  record with no truncation signal at all while still reporting
  `has_ctf=True`/`extraction_partial=False`.
- **DWARF advanced-channel CFI extraction now tracks per-function
  coverage, not just per-FDE decode success** — the previous fix marked
  a decode failure or a total absence of unwind sections incomplete, but
  an exported function whose address never appears as any FDE's own
  `initial_location` (partial coverage — e.g. one translation unit built
  without unwind tables) was invisible to the per-entry loop entirely, so
  `evidence_state` still reported `parsed` despite that function's
  frame-register/callee-saved facts never being evaluated at all.
