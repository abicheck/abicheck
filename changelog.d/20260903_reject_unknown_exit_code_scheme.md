### Fixed

- **A typed `CompareRequest`/`ScanRequest` with a misspelled or invalid
  `exit_code_scheme` (e.g. `"legacy "` with trailing whitespace, or a wrong
  case) is now rejected outright instead of silently misclassifying.**
  `resolve_release_gate_options` (the resolver every `severity_preset`/
  `exit_code_scheme` typed field feeds, shared with the CLI and the
  directory/package release fan-out) previously compared the raw scheme
  string only against `"severity"`/`"legacy"`; anything else fell through
  unchanged, so a typed caller that also set a `severity_preset` could
  silently get the severity gate algorithm for a scheme that was never
  actually `"severity"` — a breaking change could then exit `0` instead of
  the typo being caught. `exit_code_scheme` is now validated against
  `{None, "auto", "legacy", "severity"}` and raises `ValueError` otherwise,
  matching the CLI's own `click.Choice` and a pack's load-time validation,
  which a typed caller previously had no equivalent of.
