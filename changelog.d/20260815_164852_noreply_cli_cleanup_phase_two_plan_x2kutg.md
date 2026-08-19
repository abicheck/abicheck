<!-- CodeRabbit review follow-ups on CLI cleanup phase two, PR 1. -->

### Fixed

- **`--profile quick --use-cases` with a non-carrying secondary output no
  longer names a nonexistent `--format oneline` flag.** The usage-error
  message picked the wrong branch whenever a `--write` secondary format was
  also given, naming `oneline` -- the internal-only value `--profile quick`
  injects, never a spelling on the public `--format` list -- as if the user
  had typed it. The quick-profile message now fires regardless of the
  secondary format, and mentions it when it's also ledgerless.
