### Fixed

- **The GitHub Action's `compare` mode no longer forwards L2 compile-context
  flags (`--ast-frontend`/`--gcc-path`/`--gcc-prefix`/`--gcc-options`/
  `--sysroot`/`--nostdinc`) for directory/package (release) operands.** The
  CLI's per-library release fan-out rejects these flags outright (a
  `UsageError`, exit 64) since it never threads a compile context to each
  pair's header dump — forwarding them unconditionally turned a working
  release comparison into a hard failure the moment any of these Action
  inputs were configured. They're now gated to the single-pair path, with a
  `::warning::` emitted instead of a silent drop when a release-style
  comparison configured one of them.
