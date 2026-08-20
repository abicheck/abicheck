### Added

- **`dump --dry-run` now reports whether `build.query` will execute.** A new
  "Build query (trust)" section shows the effective query, the exact `argv`
  it would run, the source-tree `cwd`, and the resulting compile-DB path
  when one is configured — or, when the query is sourced from an
  auto-discovered (untrusted) `.abicheck.yml`, that it will *not* run and
  why, pointing at `--config` as the way to authorize it. The trust decision
  itself is unchanged (an explicit `--config` or an explicit CLI
  `--build-query` was already the only way to authorize execution); this
  makes that already-enforced decision visible before the real run, closing
  CLI cleanup phase two's PR 3C prerequisite 3
  (`docs/contribute/plans/cli-cleanup-phase-two.md`). Like every other
  `--dry-run` section, this never invokes the query.
