### Fixed

- `VPTR_INTRODUCED` (`diff_layout.py`) and `SECONDARY_VTABLE_GROUP_CHANGED`
  (`diff_vtable_layout.py`) now also accept a confirmed
  `is_trivially_copyable=True` as conclusive fallback evidence a record owns
  no vtable, alongside the previously-added `is_standard_layout=True`
  fallback — trivial copyability requires every special member function to
  be trivial, which itself requires no virtual functions/virtual base
  classes. Factored the shared check into `model.fact_confirmed_true`.
