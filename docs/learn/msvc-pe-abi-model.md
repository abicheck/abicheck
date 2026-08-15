---
doc_type: explanation
audience:
  - library-maintainer
level: intermediate
canonical_for:
  - msvc-pe-abi-model
depends_on:
  - abicheck/diff_cxx_rules.py
  - abicheck/pdb_metadata.py
  - abicheck/pe_metadata.py
  - abicheck/dumper_ast_config.py
lifecycle: active
generated: false
---

# The MSVC/PE ABI Model

Parts [3](abi-series/03-type-layout.md), [4](abi-series/04-cpp-abi.md), and
[5](abi-series/05-linker-elf.md) of the Learning Series build their mental
model from the **Itanium C++ ABI** — the model Linux/macOS Clang/GCC share.
Windows doesn't use it. MSVC's decoration scheme, vtable/RTTI layout, and
calling-convention encoding are a *genuinely* different system, not a
relabeled Itanium. This page is that missing mental model: what
actually differs, and — as important — exactly where abicheck's own evidence
for a Windows/PE binary is narrower than what the ELF/Itanium parts assume.

This page is a **narrative companion**, not a new fact source. The exact
capability matrices already have owners — [Platform Support](../reference/platforms.md)
(what abicheck can extract per host/format, and CI validation depth) and
[Part 5's PE/COFF & Mach-O parallels table](abi-series/05-linker-elf.md#pecoff-and-mach-o-parallels)
(the ELF-concept-to-Windows/macOS-peer mapping) — this page explains *why*
those tables say what they say, and links back to them rather than repeating
them.

## Why "just relabel Itanium" doesn't work

Every Itanium-ABI fact Part 4 teaches — the `_ZN...` mangling shape, a single
vtable pointer at object offset 0 for single inheritance, RTTI reached through
a `type_info` object referenced from the vtable — has a genuinely different
MSVC counterpart, not a cosmetically renamed one:

- **Decoration, not mangling.** MSVC's own term is *decoration*, and the
  scheme is unrelated to Itanium's: a leading `?`, scope components in
  **innermost-first** order (the reverse of Itanium's outermost-first),
  `@`-separated, terminated by the first `@@`, with a name-backreference
  table for repeated components. abicheck parses this directly —
  `diff_cxx_rules.msvc_scope_components()`/`msvc_qualified_name()` recover
  scope for owner-class seeding and stdlib-namespace checks the same way
  `itanium_scope_components()` does for Itanium — but the parser is
  deliberately conservative: special member functions/operators (`??0`
  ctor, `??1`/`??_D` dtor, `??4` `operator=`, …), template instantiations
  (`?$Name@Args@`), and the anonymous-namespace marker (`?A`) are all
  rejected rather than guessed at, since a wrong guess there is worse than
  the pre-existing gap (see `msvc_scope_components()`'s own docstring for
  the full unmodelled list).
- **Vtable/RTTI layout is a different structure, not a relocated Itanium
  one.** Multiple and virtual inheritance under MSVC introduce **vbtables**
  (virtual base tables) and, for a class with more than one polymorphic
  base, more than one vtable pointer — a shape Itanium's single-vtable-slot
  model doesn't have at all. RTTI is reached through a `CompleteObjectLocator`
  chain rather than Itanium's `type_info`-via-vtable-slot approach.
  [Platform Support](../reference/platforms.md#known-limitations-by-platform)
  already states the practical consequence plainly: *"MSVC vtable layout
  differs from Itanium ABI; vtable diff results may be inaccurate."* Treat
  a `TYPE_VTABLE_CHANGED` finding on an MSVC-compiled type as a signal to
  investigate, not as evidence carrying the same weight it has on ELF/Itanium.
- **Calling convention is part of the decorated name, not a separate ABI
  fact abicheck classifies.** `__cdecl`/`__stdcall`/`__fastcall`/`__thiscall`
  each decorate differently (leading underscore, `@N` stack-cleanup suffix,
  or neither) — so a convention change on an otherwise-unchanged function
  changes the decorated name itself. abicheck has no dedicated
  calling-convention `ChangeKind`; it reports this the same way it would
  report an unrelated rename: `func_removed` + `func_added` on the two
  distinct decorated names. This is tracked as a known upstream-parity gap
  (abicc issue #50, cross-referenced from
  [Platform Support](../reference/platforms.md#windows-toolchain-support-matrix)),
  not a bug specific to this page's topic — just worth knowing *why* a
  convention flip doesn't read as a clean, single finding.

## What evidence a Windows/PE scan actually has

The [PDB vs. DWARF capability table](../reference/platforms.md#what-no-headers-actually-means)
is the exact, fact-owning source for this — read it before relying on a
specific claim — but the shape worth internalizing here is: **PDB's TPI
stream is not a drop-in replacement for DWARF.** `pdb_metadata.py`'s
`_extract_method_calling_conventions()` populates
`AdvancedDwarfMetadata.calling_conventions` keyed `Class::method` — **class
methods only**. A PDB-only (no `-H`/castxml) scan recovers struct/class
layout, field offsets, and enum values the same way an ELF DWARF scan does,
but does **not** reconstruct free-function signatures or vtable slots at
all — those need the header-AST path (`castxml` + `cl.exe`, or `clang-cl`).
Concretely, on Windows the "debug info alone recovers most layout breaks"
shortcut that [Part 8](abi-series/08-detection.md) documents as generally
true for a `-g` ELF build is **narrower**: it holds for struct/enum layout
and method calling convention, not for parameter/return types or vtables.

## The DLL/CRT boundary rule Itanium has no analog for

One Windows-specific hazard has no ELF counterpart at all, and it's easy to
miss because nothing about it looks like an ABI break in the Itanium sense:
**each DLL may link its own CRT**, so memory allocated with `malloc`/`new` in
one module must not be `free`d/`delete`d across a DLL boundary — a rule with
no equivalent on Linux's single-system-libc model. This is documented as a
row in [Part 5's PE/COFF parallels table](abi-series/05-linker-elf.md#pecoff-and-mach-o-parallels);
it's called out again here because it's the kind of hazard a reader arriving
from the Itanium-first Parts 3–5 has no prior mental model to expect at all —
it isn't a layout change, a symbol change, or a decoration change; it's a
runtime contract with no static signature abicheck (or any static ABI
checker) can verify.

## Practical guidance

- **For the most complete MSVC/PE comparison abicheck can produce**, supply
  both a PDB (`/Zi`) *and* headers (`-H`, with `castxml --castxml-cc-msvc
  cl.exe` or `--ast-frontend clang` against `clang-cl`) — see the
  [Windows Toolchain Support Matrix](../reference/platforms.md#windows-toolchain-support-matrix)
  for which combinations are CI-validated versus best-effort today.
  MinGW/GCC-built DLLs are the CI-validated cross-platform baseline; native
  MSVC (`cl.exe`) header parsing is untested in CI and may need local
  verification before you rely on it in a release gate.
- **Treat a Windows-side `TYPE_VTABLE_CHANGED` as a prompt to look closer**,
  not as the same strength of evidence a matching ELF/Itanium finding
  carries — the layout model itself is different (vbtables, potentially
  multiple vtable pointers), and abicheck's vtable diffing was built and
  validated against the Itanium shape first.
- **A `func_removed` + `func_added` pair with an otherwise-unchanged
  signature** is worth checking for a calling-convention flip before
  assuming it's a real rename — see the calling-convention section above.
- **Cross-DLL allocator ownership** is a design discipline, not something
  a scan can catch: never let one module's `malloc`/`new` be
  `free`d/`delete`d by another; route allocation/deallocation through a
  single owning module (or a matched pair of exported functions) instead.

---

_See also: [Platform Support](../reference/platforms.md) · [Part 4 — C++ ABI](abi-series/04-cpp-abi.md) ·
[Part 5 — ELF & Linker-Level Concerns](abi-series/05-linker-elf.md) ·
[Detecting Breaks](abi-series/08-detection.md)._
