### Changed

- **`ObservabilityCondition` gains an `arch` axis** (ADR-093 D7, amendment) — a kind whose emission depends on the processor can be declared for that architecture only. `struct_return_convention_changed` is now conditioned on ELF x86-64, so an aarch64 cell does not expect it. Existing conditions and call sites are unchanged; the abiforge observable adapter passes the axis through.
