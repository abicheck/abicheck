### Fixed

- `SECONDARY_VTABLE_GROUP_CHANGED` (`diff_vtable_layout.py`'s `_is_polymorphic`)
  no longer reads a record as indeterminate when its own `vtable_fact` wasn't
  collected but a confirmed `is_standard_layout=True` conclusively proves it
  owns no vtable — mirrors the identical fallback added to `diff_layout.py`'s
  `VPTR_INTRODUCED` guard.
