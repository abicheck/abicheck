### Fixed

- **`fact_registry.py`'s `Function.is_explicit` entry now lists `dwarf`
  among its producing backends** (Codex review): `dwarf_snapshot.py`
  reads `DW_AT_explicit` and passes a real bool to `Function(is_explicit=
  ...)`, so DWARF-derived snapshots populate this fact too, not only the
  two header-AST backends.
