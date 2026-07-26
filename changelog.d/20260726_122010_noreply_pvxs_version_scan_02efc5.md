### Changed

- **Clearer usage error for `--format sarif`/`html`/`review` on a
  directory/package `compare`** — the message now states *why* (those
  formats require a single-pair comparison) instead of only listing the
  supported alternatives, and points at comparing one library at a time as
  the fix. Found while writing a real GitHub Action recipe for a
  multi-library project during a pvxs full-version-matrix validation scan.
