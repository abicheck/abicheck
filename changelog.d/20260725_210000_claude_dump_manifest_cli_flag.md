<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Added

- **`dump --dump-manifest PATH` (ADR-050 D3, G32 Phase B)**: dumps an ELF
  binary from a real multi-translation-unit manifest instead of a single
  `-H/--header` list, running one castxml/clang invocation per translation
  unit and merging the results. Mutually exclusive with `-H/--header` and
  `--public-header`/`--public-header-dir` (declare those in the manifest's
  own base profile instead); rejected outright for PE/Mach-O binaries
  (ELF-only so far). `compare`'s own side-scoped `--dump-manifest` is a
  follow-up, not included here.
