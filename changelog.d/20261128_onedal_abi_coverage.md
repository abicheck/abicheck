### Fixed

- **Stripped ELF RTTI.** Detect loss of a public direct base in x86-64 Itanium multiple-inheritance RTTI when the frozen client can lose a cross-cast or exception handler.
- **C++ inheritance reporting.** Binary-only comparisons now report the correct base count for Itanium RTTI descriptors with a single VMI base and for 32-bit VMI descriptors.
- **Public-surface review warnings.** CLI and review summaries explain when the comparison used the full export table, preserving the reported compatibility verdict.
