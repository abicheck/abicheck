### Fixed

- **DWARF CFI per-function coverage no longer false-flags ARM Thumb-mode
  functions as missing unwind data** — a Thumb-mode function symbol's ELF
  `st_value` has bit 0 set by AAPCS convention (a documented "branch here
  in Thumb state" marker), while a DWARF FDE's `initial_location` always
  names the true, bit-0-clear aligned code address; the coverage check
  compared these directly, so every Thumb-tagged export on an ARM32
  binary appeared to have no matching FDE. Fixed by normalizing a
  function symbol's address through a new architecture-scoped helper
  before comparing it against FDE addresses — scoped to ARM only, since
  other architectures (notably x86/x86_64) have no code-alignment
  guarantee and a real function symbol can legitimately have an odd
  address.
- **BTF `BTF_KIND_INT` member size now uses the type's declared storage
  size** — it previously derived the byte size from the encoding word's
  `nr_bits` field, but per `include/uapi/linux/btf.h` that field is only
  the occupied bit width within the type's real storage size
  (`size_or_type`), which can legitimately be narrower than the storage
  it occupies. Fixed to use `size_or_type` directly.
