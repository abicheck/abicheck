### Fixed

- **DWARF `_get_type_align` (packing detection) now forwards its
  `incomplete` accumulator through qualifier unwrapping** — a member
  whose own `DW_AT_type` resolved fine but named a typedef/qualifier
  chain with an unresolvable *inner* `DW_AT_type` had that failure
  recorded by `_unwrap_qualifiers`' own accumulator, then silently
  discarded because `_get_type_align` never passed one through to
  receive it, letting the member's alignment read as an unremarkable 0
  while `evidence_state` still reported `"parsed"`.
- **CTF pointer members now use the container's real ELF pointer size,
  not a hardcoded 64-bit assumption** — `_read_ctf_section` discarded the
  ELF class entirely, so a 32-bit ELF carrying CTF debug info reported
  every pointer member as 8 bytes regardless. Fixed by threading the
  derived pointer size through `parse_ctf_metadata` →
  `parse_ctf_from_bytes` → `_TypeResolver`, mirroring the fix already in
  place for BTF.
- **A BTF function prototype (`BTF_KIND_FUNC`) that references a missing
  or non-`BTF_KIND_FUNC_PROTO` record is now marked incomplete** — this
  case was silently dropped from `func_protos` with
  `extraction_partial` left `False`, indistinguishable from a binary that
  genuinely has no such function at all.
