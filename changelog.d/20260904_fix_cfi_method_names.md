### Fixed

- **CFI extraction (frame-register/callee-saved-register facts) now
  actually runs** — `dwarf_advanced._get_cfi_source()` called nonexistent
  pyelftools methods (`get_EH_CFI_entries()`/`get_CFI_entries()`; the real
  API has no `get_` prefix), silently caught by its own
  `except AttributeError`, so CFI extraction unconditionally returned no
  data against any real binary while the advanced DWARF channel still
  reported `parsed`. `FRAME_REGISTER_CHANGED`/callee-saved-register-backed
  detector families were never actually evaluated. Fixed to call the real
  `EH_CFI_entries()`/`CFI_entries()` methods, with a new
  `tests/test_cfi_extraction_integration.py` driving extraction against a
  genuinely compiled binary (the mock-only unit tests could not catch this
  class of bug, since an unspec'd `MagicMock()` answers any attribute
  name).
- **`_extract_cfa_reg_from_fde()`/`_extract_callee_saved_regs()` now
  report their own decode failures to the caller's completeness
  accounting** — each helper previously caught and swallowed its own
  decode error internally, returning a plain `None` indistinguishable from
  "this FDE legitimately has no CFA/saved-register data," so
  `_parse_frame_registers()`'s own per-FDE exception handler was
  unreachable for this failure shape and a genuinely failed decode was
  still reported as a complete (`parsed`) pass.
- **`AnalysisAssurance.debug_evidence` is now keyword-only** — it was
  inserted before `l3_context_status` and other pre-existing fields;
  since every field carries a default, a caller using the positional
  constructor would have had every subsequent argument silently shifted
  by one, bound to the wrong field, without an exception. Moved to the
  end of the dataclass and marked keyword-only.
- **CTF v2 `_parse_types()` now flags a truncated "large" struct/union
  size payload** — a v2 struct/union whose 16-bit size marker is at least
  `_CTF_V2_LSTRUCT_THRESH` must be followed by a mandatory 4-byte real
  size; ending the type section right after the marker previously fell
  through silently (neither appending to `truncated` nor stopping the
  parse), letting a malformed/cut-off entry be accepted as a complete
  parse.
