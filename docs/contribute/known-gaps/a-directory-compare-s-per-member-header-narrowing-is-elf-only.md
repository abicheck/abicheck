---
doc_type: contributor
audience:
  - contributor
level: advanced
lifecycle: active
generated: false
---

# A directory `compare`'s per-member header narrowing is ELF-only

Index: [Known gaps](../known-gaps.md)

**Status (2026-10-11): OPEN.** A PE or Mach-O member of a directory/package `compare` still parses the union `-H` set.

`workflows/release_member_headers.py` gives each release member the headers it claims: a header claims a member when one of the member's exported names appears in it as an identifier. The export names come from `workflows.extraction.parse_elf_metadata` and `elf_symbol_filter.exported_symbol_names`, so only ELF members have any. A PE or Mach-O member has no export tokens and keeps the union operand. A member that claims no header at all also keeps the union. That fallback is the safe direction (it over-reports and never drops a declaration the member owns), and the verdict rule (`unreached_types_owned_elsewhere`) still applies. On those platforms, though, the per-member union walk and the duplicated type findings this narrowing removes on ELF are still there.

To close it, read the export names from `pe_metadata`/`macho_metadata` the same way. Mach-O needs its leading underscore stripped first. The assignment itself (`assign_side_headers`) does not depend on the platform.

The parent entry ("A directory `compare` still feeds the union `-H` set to every member") is closed in [the archive](../archive/known-gaps-closed.md).

## Second limitation: an export name built by token pasting

A header is assigned to a member when one of that member's exported names
appears in it as an identifier. A declaration whose name is produced only by
macro token pasting (`#define DECL(n) int lib_##n(void)` / `DECL(open);`)
never spells the export literally, so a header that declares it only that way
is not claimed by its owner. If another member claims that header, the owner
does not parse it and a change there is missed for the owner; if no member
claims it, the header falls back to the union and nothing is lost. Reading the
preprocessed text (as the CastXML calling-convention reader now does) instead
of the raw header would close it.
