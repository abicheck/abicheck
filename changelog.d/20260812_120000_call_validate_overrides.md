<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **`PolicyFile.validate_overrides()` was dead code — a risky `--policy-file`
  override never warned anyone.** The method already flagged, e.g.,
  downgrading `func_removed` to `ignore` or `type_vtable_changed` to `risk`,
  but nothing in the CLI ever called it, so its warnings never reached a
  user. `_load_suppression_and_policy()` — the one loader shared by
  `compare`, `compare-release`, `scan --against`, and `appcompat` — now
  calls it after loading a `--policy-file` and echoes every warning to
  stderr, the same place the existing `--policy` override notice already
  goes.
