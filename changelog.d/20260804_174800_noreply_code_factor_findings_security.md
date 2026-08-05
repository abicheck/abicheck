### Security

- **Two silently-swallowed exception handlers now log instead of vanishing**
  (bandit B110/B112) — a failed cleanup in `buildsource.inline._run_cleanups` and
  an unclassifiable `compare` operand in `cli_options` each record a `debug`
  entry, so a leaked scratch directory or a mis-applied `--profile` default is
  diagnosable rather than invisible. Both stay best-effort; nothing else changes.
  The two `yaml.load(..., Loader=_StrictLoader)` call sites (`dump_manifest`,
  `compatibility_evaluation_packs`) additionally carry an explicit `# nosec B506`
  stating why they are safe: `_StrictLoader` is a `yaml.SafeLoader` subclass,
  which bandit cannot name-match, so scanners reported the safe-loader path as an
  arbitrary-object one.
