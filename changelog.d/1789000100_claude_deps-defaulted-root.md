### Fixed

- **`deps` now says when an environment root was defaulted rather than
  chosen.** With no `--sysroot`/`--old-root`/`--new-root`, the analysis runs
  against the current host filesystem; the resolved plan (`--dry-run`) and
  the JSON (`baseline_env_defaulted`/`candidate_env_defaulted`), Markdown and
  HTML reports now label that root `defaulted`, where a bare `/` previously
  read like a deliberately selected deployment environment. An explicitly
  given `--old-root /` is a choice and is never labelled that way.
  `deps tree` with no `--sysroot` also reports its root as `/` instead of an
  empty string.
