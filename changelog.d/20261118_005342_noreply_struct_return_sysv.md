<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Added

- **By-value return convention is read from the header layout** — a record that a declared function returns by value now emits `STRUCT_RETURN_CONVENTION_CHANGED` when its x86_64 SysV return class changes (registers up to 16 bytes, hidden pointer above that), at stripped binary plus headers. The class is decided from the recorded member offsets and spellings on LP64 x86_64 ELF; an undecided member gives no finding rather than a guess.
