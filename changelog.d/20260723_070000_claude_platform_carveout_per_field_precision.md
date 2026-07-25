<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **The platform-identity carve-out waived a profile mismatch on the wrong
  evidence** (Codex review, PR #624): `check_contracts_comparable`'s
  cross-architecture carve-out compared old/new binary metadata as a whole
  tuple (`("elf", machine, ei_data, elf_class)`) and waived the profile
  mismatch as soon as *any* component differed. That let a bogus
  `pointer_width` extraction (e.g. `32` vs `64` from a misconfigured run)
  hide behind an unrelated, genuine `machine`/architecture change on the
  same pair — e.g. old/new ELF binaries genuinely differing `EM_X86_64` →
  `EM_AARCH64` while both are actually 64-bit, with a bogus `pointer_width`
  mismatch the real cause of the profile drift. `_binary_platform_axis` is
  replaced with `_binary_platform_components`, which maps each profile field
  (`target_triple`/`pointer_width`/`endianness`) to its own corresponding
  binary component; the carve-out now waives a mismatch only when *every*
  differing profile field is individually corroborated by a genuine
  difference on its own component — never merely "some" component of the
  platform identity changed. PE/Mach-O metadata has no distinct word-size or
  endianness field (unlike ELF's `elf_class`/`ei_data`), so a
  `pointer_width`- or `endianness`-only mismatch on those platforms can
  never be corroborated this way and correctly still raises.
