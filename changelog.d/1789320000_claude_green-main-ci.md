### Fixed

- **A DWARF-sourced snapshot's qualified typedef keys are exercised again.** The
  regression test for that path built its old side with a bare
  `DwarfMetadata()`, whose `has_dwarf` is `False`, so `debug_info_present`
  correctly declined to treat the side as DWARF-sourced and the pair silently
  fell back to the bare-keyed maps — reporting a genuine `typedef_base_changed`
  as a `typedef_removed`. The fixture now sets the content flag it meant to.

### Changed

- **`abicheck.model.__all__` is sorted again**, restoring the invariant
  `tests/test_model_package_surface.py` states.
- **The three oversized change-catalog taxonomy modules are split into numbered
  parts.** `symbols.py`, `types.py` and `platform.py` had grown past ADR-061's
  800-line ceiling; each is now an assembly point over `<name>_1.py`/`<name>_2.py`,
  exactly as `kind_names_{1,2,3}.py` already is. Entry order and content are
  unchanged, and the public `*_ENTRIES` names still resolve where they did.
