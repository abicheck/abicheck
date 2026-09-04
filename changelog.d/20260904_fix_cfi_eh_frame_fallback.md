### Fixed

- **`_get_cfi_source()` now correctly falls back from `.eh_frame` to
  `.debug_frame` when the former carries no real CFI data** — a second
  round of fresh evidence on the just-fixed pyelftools method-name bug:
  calling the real `EH_CFI_entries()`/`CFI_entries()` methods exposed two
  more real absent/empty-section semantics the previous unconditional
  `if src is not None: return src` could not handle. First, pyelftools
  raises `AssertionError` (not `AttributeError`) when the underlying
  section is entirely absent, which would have violated
  `parse_advanced_dwarf()`'s documented "never raises" contract. Second, a
  binary built with `-fno-asynchronous-unwind-tables` can still emit an
  `.eh_frame` section containing only a CIE/ZERO terminator (no real FDE);
  the old code accepted that non-`None` but useless result immediately
  instead of falling back to a `.debug_frame` section that does carry real
  frame data — silently leaving `frame_registers`/`callee_saved_regs`
  empty while the advanced channel still reported `parsed`. Fixed by
  checking section presence via `has_EH_CFI()`/`has_CFI()` before calling
  either entries accessor, and only accepting the `.eh_frame` result when
  it actually contains an FDE. New `@pytest.mark.integration` test drives
  this against a genuinely `-fno-asynchronous-unwind-tables`-compiled
  binary, confirming the fallback populates real facts.
