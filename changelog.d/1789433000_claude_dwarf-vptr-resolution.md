### Changed

- **Internal: DWARF inherited-vptr resolution moved to `extract/`** (ADR-061) — the whole-binary pass that resolves `vptr_offset_bits` for classes whose vtable is inherited now lives in `abicheck/extract/dwarf_vptr_resolution.py` (`finalize_vptr_offsets`) instead of a 246-line method on the `dwarf_snapshot.py` builder. No behavior or output change; `dwarf_snapshot.py`'s debt baseline drops from 1999 to 1748 lines.
