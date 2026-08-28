### Fixed

- **`dump --dry-run` now validates a `-H`/`--header` directory the same way
  the real run does.** Previously a `-H` argument naming a nonexistent path,
  an empty header directory, or a path that is neither a file nor a
  directory could report a clean `--dry-run` success even though the real
  run would reject it outright (`Header directory contains no supported
  header files: ...`) — the check already existed for real execution but
  was never run on the `--dry-run` path. Both paths now share the identical
  check, raised before either branch, so `--dry-run` never predicts success
  for an invocation the real run would immediately fail. A source-only dump
  (no `SO_PATH`) is unaffected — `-H` is already documented as inert there
  (warn, never reject), and this fix does not change that. Closes the last
  open case of CLI cleanup phase two's PR 3C prerequisite 3
  (`docs/contribute/plans/cli-cleanup-phase-two.md`).
