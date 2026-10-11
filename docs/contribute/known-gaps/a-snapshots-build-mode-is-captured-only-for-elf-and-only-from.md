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

**Status (re-verified 2026-10-11 at `46c127111`): PARTIAL.** Unchanged since 2026-10-10: `extract/build_mode_capture.py` is ELF-only and its only caller is `workflows/snapshot_factory.py`. PE/Mach-O get no `build_mode`, detached debuginfo is not read, and nothing compares compiler family across snapshots.


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
