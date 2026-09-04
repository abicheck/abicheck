### Fixed

- **`.debug_frame` CFI fallback no longer erases a recorded `.eh_frame`
  decode failure** — `_get_cfi_source()`'s `.debug_frame` branch accepted
  `CFI_entries()`'s result unconditionally, unlike its `.eh_frame` sibling's
  own `_has_fde()` gate. A malformed `.eh_frame` (a real decode failure,
  recorded via `source_failed`) falling back to a present but real-FDE-empty
  `.debug_frame` (CIE-only, or genuinely no frame data) still returned that
  unusable list as a non-`None` source, making `_parse_frame_registers()`'s
  own `cfi_src is None` completeness check unreachable and silently
  reporting the advanced channel as fully parsed. Now symmetric: only a
  `.debug_frame` result with a real FDE is accepted as a usable source.
- **A `has_dwarf=True` snapshot with the default `evidence_state` is no
  longer silently trusted as "no evidence available"** —
  `_debug_evidence_receipt()`'s `getattr(channel, "evidence_state", None) or
  (...)` fallback could never actually fire against a real `DwarfMetadata`/
  `AdvancedDwarfMetadata` instance, since `evidence_state` is a dataclass
  field whose own default is the non-empty string `"not_available"`, not
  `None`. A legacy caller still constructing `DwarfMetadata(has_dwarf=True)`
  without the newer `evidence_state` kwarg therefore read back as
  `"not_available"` — a state `debug_parse_incomplete` treats as
  legitimately absent evidence, not incomplete, even though `has_dwarf`
  says data was actually found. The contradiction is now normalized to
  `"presence_only"`, mirroring the identical degrade already applied to a
  legacy pre-v44 serialized block.
- **PDB TPI record parsing now bounds a record by the header's own declared
  type-data boundary, not just the whole stream buffer** — `parse_tpi_stream()`'s
  per-record bounds check compared a record's declared `rec_len` against
  `len(data)` (the entire stream) rather than `end` (`header_size +
  type_bytes`, the header's own declared type-section boundary). A record
  whose length crossed that boundary but still fit within trailing
  hash/index substream bytes the PDB stream carries past the type section
  was accepted, consuming those non-type-record bytes as the record's own
  payload and potentially reaching `ti_end` with `truncated=False` despite
  the type section never actually holding that many well-formed records.
  Now bounded by both `end` and `len(data)`.
