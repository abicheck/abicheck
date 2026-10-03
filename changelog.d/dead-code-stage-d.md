### Removed

- **`ChangeKind.FRAME_REGISTER_CHANGED` (`frame_register_changed`) and the
  CFI pass behind it (409 → 408 kinds).** The `.eh_frame`/`.debug_frame` pass
  that fed it, and the callee-saved-register fallback for
  `calling_convention_changed`, never ran on a real comparison: the unified
  DWARF parse did not call it, and it asked pyelftools for methods that do not
  exist, so it returned nothing when it was called. Enabling a corrected pass
  over the 169 built catalog cases added one finding, a false
  `frame_register_changed` that turned `case15_noexcept_change` from
  `COMPATIBLE_WITH_RISK` into `BREAKING`, and did not detect the GCC `ms_abi`
  change it was meant to (case64 stays a known gap). A policy file naming the
  kind now fails to load like any unknown kind. `AdvancedDwarfMetadata` loses
  the always-empty `frame_registers`/`callee_saved_regs` fields; snapshots that
  carry them still load.
- **`abicheck.dwarf_advanced.parse_advanced_dwarf` and the
  `abicheck.dwarf_unified.parse_advanced_dwarf` shim.** Use
  `abicheck.dwarf_unified.parse_dwarf`, which returns both halves from one
  ELF open.
