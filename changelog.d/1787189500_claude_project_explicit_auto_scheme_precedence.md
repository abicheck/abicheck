### Fixed

- **CLI cleanup phase two, PR B follow-up (Codex review, fresh evidence)**:
  a project's `.abicheck.yml` explicitly writing `exit_code_scheme: auto`
  now outranks a selected gate pack's concrete `exit_code_scheme`, on both
  `compare` and `scan --against`. `BuildConfig.exit_code_scheme` defaults
  an absent key to the same string `"auto"` a written-but-unresolved
  `auto` parses to, so the ADR-049 receipt resolver previously could not
  tell "stated `auto`" from "never stated" and let a lower-precedence pack
  silently fill the field. `BuildConfig` (and
  `ProjectCompatibilityInputs`) now carry a new
  `exit_code_scheme_explicit` flag, set from whether the YAML key was
  literally present, so a real, stated `auto` resolves (and pins) its
  severity-active-derived scheme the same way an explicit
  `--exit-code-scheme auto` flag already does.
