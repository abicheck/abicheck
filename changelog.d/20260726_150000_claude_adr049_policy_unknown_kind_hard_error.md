<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Changed

- **`--policy-file` rejects unknown `ChangeKind` slugs instead of
  warning-and-skipping them** (ADR-049 D8, now accepted): a policy YAML
  file's `overrides:` block previously logged a warning and silently
  dropped any slug that didn't match a real `ChangeKind` — intended to
  tolerate typos, but it meant a renamed or misspelled kind (e.g.
  `func_removed` typo'd as `func_remvoed`) could silently disable a release
  rule with no error. `PolicyFile.load()` now raises `PolicyError` (a hard
  load error, mapped to the existing CLI usage-error exit) listing every
  unknown slug found. If your policy file currently has a stale/misspelled
  slug that was being silently ignored, this now fails the load — fix or
  remove it.
