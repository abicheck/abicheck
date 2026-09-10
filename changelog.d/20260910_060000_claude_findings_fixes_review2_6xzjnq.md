### Fixed

- **A project-config-stated policy override could silently outrank an
  explicit `--pack`'s override for the same `ChangeKind`.** `.abicheck.yml`'s
  `policy.overrides` was merged into the loaded `PolicyFile.overrides`
  *before* `--pack` folding ran, so the pack-application step read the
  project-config value as if it were an explicit `--policy <file>` entry and
  refused to let a selected `--pack` override it — a `policy: {overrides:
  {func_removed: ignore}}` in `.abicheck.yml` could keep a `compare` run
  exiting `0` even with an explicit `--pack` asserting `func_removed: break`.
  Fixed generally: project-config overrides now fold in strictly *after*
  every fold with real precedence over them (an explicit `--policy <file>`
  entry, then a selected `--pack`), filling only a `ChangeKind` neither
  already claimed — matching ADR-049 D7's stated precedence order
  (`explicit_cli/api_request > legacy_alias > run_recipe > run_profile >
  project_config > built_in_default`). The same fix reaches the
  directory/package release fan-out, which previously dropped
  `.abicheck.yml`'s `policy.overrides` entirely for that operand shape (a
  one-library directory/package `compare` now honors the identical
  project-config override an equivalent scalar `compare` of that library
  already applies).
- **A `--policy`/`versioning:` YAML document with a non-string top-level key
  (e.g. a bare `1:` alongside an unknown key) crashed with an uncaught
  `TypeError` instead of the intended `PolicyError`.** YAML parses a
  non-string mapping key at any level; the shared unknown-key validator's
  `sorted(unknown)` call assumed every key was a `str` and raised
  (`int`/`str` aren't orderable) rather than reporting the malformed
  document as a clean usage error. Fixed by sorting on each key's `repr()`.

### Changed

- `abicheck/policy_file_top_level.py` and
  `abicheck/policy_file_project_overrides.py` moved to
  `abicheck/policy/policy_file_top_level.py` and
  `abicheck/policy/policy_file_project_overrides.py` (ADR-061 task routing —
  deciding a policy document's validity or an override's precedence tier is
  `policy/`'s job, not the flat legacy root); `policy_file.py`'s own
  `base_policy` parsing moved alongside its sibling top-level-key check for
  the same reason, freeing enough headroom that neither move needed its
  `architecture/debt.yaml` no-growth baseline raised.
