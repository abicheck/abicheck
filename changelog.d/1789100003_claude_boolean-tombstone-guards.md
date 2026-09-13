### Fixed

- **The Action's retired `audit`/`estimate` inputs are now refused for every
  value, not just the literal `true`.** Composite-action inputs are untyped
  strings, so `audit: yes`, `estimate: 1` or `audit: TRUE` are all a
  workflow explicitly asking for the retired behaviour — but both guards
  matched only `"true"`, so those spellings passed preflight and the run
  proceeded with the setting silently ignored: a narrower analysis than the
  workflow asked for, with no error. Both `action/validate-inputs.sh` and
  `action/run.sh` now reject any value other than the `false` default, the
  shape `require-complete-analysis` already used. Writing the default out
  explicitly (`audit: false`) stays inert.
