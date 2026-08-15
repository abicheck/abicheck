### Documentation

- **Corrected seven technical claims in the ABI/API learning track that
  contradicted each other or the implementation.** The `noexcept` section of
  Part 4 no longer asserts a single unconditional unwinding outcome (which
  contradicted the canonical Exception Unwinding page) and instead separates
  linkage, type-system, behavioral, and scanner-behavior facts. Triviality
  evidence is now stated once and correctly: `value_abi_trait_changed` is
  *inferred* from DWARF DIE structure (DWARF has no trivially-copyable
  attribute), while `trivially_copyable_lost` reads a real trait supplied
  only by the direct-clang AST path — not castxml, which deliberately leaves
  it `None`. Binary-only (L0) `_ZTV`/`_ZTI` findings are described as
  "the emitted vtable/RTTI object changed size", with an explicit note that a
  pure virtual-function reorder and a size-preserving base-class change are
  invisible at L0 and that slot/base *identity* needs L1/L2 evidence. The
  MSVC/PE page no longer claims multiple vptrs are absent from the Itanium
  model (Itanium has secondary virtual tables), scopes the
  `__cdecl`/`__stdcall`/`__fastcall`/`__thiscall` distinctions to Windows
  x86, and states the CRT-boundary hazard per runtime configuration rather
  than as "every DLL has its own heap". `NO_CHANGE` no longer maps to a patch
  release — a verdict constrains a version bump but cannot select one. Part 8
  no longer quotes a hand-written accuracy ladder that disagreed with the one
  measured table. Part 1 stops equating "defined" with "exported", stops
  describing stored symbol names as demangled, scopes "the name is the only
  lookup key" to the unversioned ELF case, and no longer claims static
  linking ends the compatibility question.

### Fixed

- **`vtable_slot_count_changed` no longer reports the `_ZTV` group size as a
  count of virtual slots.** The symbol spans the primary table plus any
  vcall/vbase offsets and secondary tables, so under multiple or virtual
  inheritance the derived figure exceeded the class's virtual count — the
  catalog's own `case174_secondary_vtable_group_changed` read
  `~3 -> ~8 virtual slots` for a `Derived` declaring no virtuals of its own.
  The finding's detail now reads `~N -> ~M vtable-group entries`, and the
  `change_registry.py` `impact`/`description_template` that wrap it no longer
  append "A virtual method was added, removed, or reordered" unconditionally —
  they state that the symbol size cannot distinguish a net virtual change from
  an inheritance-shape change. The byte sizes reported were already exact and
  are unchanged, as are the `ChangeKind`, verdict, and exit code.

### Changed

- **`diff_elf_layout.py`'s module docstring now states the L0 vtable/RTTI
  signal's actual limits** — a pure virtual-function reorder preserves the
  `_ZTV` symbol size and is not detected, a virtual destructor occupies two
  entries rather than one, and the detector answers "the emitted group
  changed size", not "which slot moved".
