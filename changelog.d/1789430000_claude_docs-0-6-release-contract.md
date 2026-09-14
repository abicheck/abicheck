### Fixed

- **Removed stale `scan` references from user-facing `--help` text.** `compare
  --pack`'s help described how a pack applied to `scan --against`, and the
  release `--depth` dial's help named `scan --depth` as its vocabulary
  reference — both naming a command retired in ADR-068 Phase 6. The published
  [CLI Reference](https://abicheck.github.io/abicheck/reference/cli-reference/)
  is generated from this text, so it carried them too.
