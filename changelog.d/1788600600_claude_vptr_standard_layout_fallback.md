### Fixed

- `VPTR_INTRODUCED` (`diff_layout.py`) no longer unconditionally declines when
  the old side's `vtable_fact`/`vptr_offset_bits_fact` weren't collected: a
  confirmed `is_standard_layout=True` is independent, conclusive evidence the
  old class owned no vtable anywhere in its hierarchy (the C++ standard-layout
  requirement excludes virtual functions/bases transitively), so it now lets
  the detector proceed instead of missing the diagnostic on such
  mixed-evidence snapshots.
