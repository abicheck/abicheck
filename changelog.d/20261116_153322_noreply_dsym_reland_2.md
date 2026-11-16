<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **DWARF alignment walk keeps independent visits** — a by-value cycle cut no longer marks its (type, offset) as completed, so a misaligned sibling reached under another ancestor chain is still reported regardless of member order.
- **Unreadable adjacent dSYM is not cached** — a Mach-O dump whose adjacent `.dSYM` exists but cannot be read is returned without being stored in the dump cache, so a retry against a readable bundle is not answered from the failed result. The limitation reason is now a fixed sentence with no path or exception text.
- **Unparseable adjacent dSYM is not cached and is recorded** — an adjacent `.dSYM` whose DWARF read fails is recorded as the same fixed limitation as an unreadable one, and the header-free Mach-O extractor now collects that limitation into the snapshot, so the dump cache guard applies on that route too.
