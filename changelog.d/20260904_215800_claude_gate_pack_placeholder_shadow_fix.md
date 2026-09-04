### Fixed

- **A `kind: gate` pack's own `gate.severity.<category>` assignment could be
  silently dropped when combined with `--profile ci-gate`.** The receipt
  resolver's `preset_stated` predicate (which pins every severity category
  as "stated elsewhere" once a preset is in effect, so a pack cannot
  silently override a value the user or project really asked for) counted
  `ci-gate`'s injected placeholder `severity_preset: "default"` unconditionally
  — even when that same placeholder is discarded (once the project states
  its own severity policy for a *different* category) before it ever reaches
  the resolved config. That pinned every category, including ones the
  project never touched, and blocked a gate pack's assignment to one of
  them from ever applying — an additive-only comparison with
  `gate.severity.addition: error` from a pack stayed exit `0` instead of
  `1`. Fixed by computing `preset_stated`'s profile clause with the same
  "project already configures its own severity policy" guard the candidate
  list itself already applies, so the two predicates cannot drift apart.
  Found by Codex review on PR #1062.
- **`--profile`'s rejection message for directory/package (release)
  operands** still told the reader ".abicheck.yml (the fan-out reads
  format/severity/scheme from it)" — stale since PR G2 deleted the
  `exit_code_scheme` config key. Found by Codex review on PR #1062.
