### Added

- `ABICHECK_EXTRACTION_ISOLATION=process` (Linux) resolves each side of a
  comparison in its own forked child process and returns only the finished
  snapshot, so allocator fragmentation left by a side's header-AST parse no
  longer sets the comparison's peak RSS. Measured on a oneDAL comparison:
  peak 1.338 -> 0.888 GiB with identical findings, at +17% wall time on that
  small input. Opt-in; see `docs/contribute/memory.md`.
