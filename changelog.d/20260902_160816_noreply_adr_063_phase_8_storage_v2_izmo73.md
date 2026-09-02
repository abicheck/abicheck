### Added

- **`dump --project-snapshot-dir PATH`** — experimental, opt-in: additionally
  writes the dump as a real, directory-backed `ProjectSnapshot` package at
  `PATH` (ADR-062/ADR-063 storage-v2), alongside — never instead of —
  `-o`/`--output`'s existing `.abi.json` output. `compare` and
  `scan --against` now accept such a package directory as an input path,
  resolved into the identical in-memory snapshot a `.abi.json` file
  resolves to. Every existing invocation that never uses these is
  unaffected — the default snapshot format is unchanged.
