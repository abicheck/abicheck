---
doc_type: contributor
audience:
  - contributor
level: advanced
lifecycle: active
generated: false
---

# A snapshot's `build_mode` is captured only for ELF, and only from the main image

Index: [Known gaps](../known-gaps.md)

**Status (closed 2026-10-14): CLOSED.** All three open parts are fixed:

1. **Compiler-family switch.** `comparability_profile.check_build_mode_comparable`
   is the last axis of `comparability.check_contracts_comparable`. When both
   snapshots record a known `BuildMode.compiler_family` and the families
   differ, it returns a non-fatal `ComparabilityMismatch`, the mechanism an
   untagged `dependency_scope` already uses. The run keeps its verdict, but
   `comparability_assurance` marks `layout`/`runtime` unverified (the
   dimensions `_PROFILE_FIELD_DIMENSIONS["compiler_family"]` names) and
   `coverage_warnings` carries the reason. A family that is unrecorded or
   `unknown` on either side is unknown, never a mismatch. A header parse under
   a different compiler is still refused first, by the profile fingerprint's
   own fatal `compiler_family` field. The other build-mode dimensions were
   already compared as findings: stdlib family and libc++ ABI version by
   `diff_stdlib_impl`, and the libstdc++ dual ABI by
   `diff_platform._diff_glibcxx_dual_abi`.
2. **Detached debuginfo.** `dumper.dump` passes its `debug_info_path` through
   `finish_binary_dump`. When the ELF main image has no
   `DW_AT_producer`/`DW_AT_language`, `read_elf_build_signals` reads them from
   that file. A shallow dump (`read_dwarf=False`) reads neither file's DWARF.
3. **Mach-O and PE.** `extract/macho_build_signals.py` walks the load commands
   to find the `__DWARF` sections and builds a pyelftools `DWARFInfo` from
   them. It reads an object file's own segment, the dSYM passed as debug
   info, or the adjacent `<image>.dSYM`, and handles thin little-endian
   images and the first slice of a fat binary. `LC_BUILD_VERSION` tool
   entries are not used, because `ld` writes them for the tools it knows
   about and they do not name the compiler of every object. PE stays `None`:
   the Rich header lists the MSVC tools behind every linked object, including
   the MSVC CRT objects a clang-cl build also links, so it does not identify
   the compiler family. PDB compile records are out of scope.

Tests: `tests/test_build_mode_complete.py` covers the
{gcc/clang/icx switch, same family, unknown} x {recorded both, one side
unrecorded} matrix against a literal expected-assurance table, a real
gcc + `objcopy --only-keep-debug` detached-debug dump, synthetic thin, fat
and 32-bit Mach-O images, and a real `clang -target x86_64-apple-macos`
object.

One limit remains, and it is not a gap. `check_contracts_comparable` keeps
only the first non-fatal mismatch it finds. When an untagged
`dependency_scope` bound fires first, the compiler-switch reason is not
listed beside it, but the run is still reported as bounded, not clean.

## History (before the fix)

**Status (re-verified 2026-10-11 at `46c127111`): PARTIAL.** `extract/build_mode_capture.py` was ELF-only. PE and Mach-O snapshots got no `build_mode`, detached debuginfo was not read, and nothing compared compiler family across snapshots.

From Stage E: `build_mode.build_mode_from_signals` takes `raw_producer`
(`DW_AT_producer`), `raw_comment` (ELF `.comment`) and `dwarf_language`, and
no production call passes any of them. Its one caller,
`diff_stdlib_impl`, passes mangled symbols only, so the stdlib dimensions
(family, libstdc++ dual ABI, libc++ ABI version) are inferred at compare time
while `compiler_family`, `language_std` and the provenance strings are always
`UNKNOWN`/empty. No dump path sets `AbiSnapshot.build_mode` either: the field
is only read back from a stored document that already carries one.
`detect_compiler_family` and `detect_cxx_standard` are tested but have no
production producer feeding them.

The parameters are kept rather than removed, because removing them would
leave both detectors reachable only from tests. The evidence exists
elsewhere: the L3 compiler record (`buildsource/compiler_record.py`) parses
the producer string, and the DWARF parse reads each CU's attributes. Proposed:
carry `DW_AT_producer`/`DW_AT_language` out of the DWARF parse (and `.comment`
out of `elf_metadata`), populate `build_mode` at dump time, and decide which
detector reads it, with a test over {GCC, Clang, ICX} x {producer present,
stripped} against the compiler's own banner. Owner: the build-mode work
(`abicheck/build_mode.py`).
