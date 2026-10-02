### Changed

- **Every option-bearing CLI command now carries per-option ADR-068 D5 rulings.**
  `abicheck/frontends/cli/options/rulings.py` extends the `compare`/`dump`
  ruling tables to `aggregate`, `project validate`/`plan`/`history`/
  `capture-variants` and `deps tree`/`compare`, keyed by command path. The
  bijection test now also walks the real Click tree, so a command with
  visible options and no ruling table fails CI (`compat` stays excluded:
  its ABICC spellings are frozen). No option was added, removed or renamed.
