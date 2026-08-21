### Fixed

- **A `schema_version >= 2` `gate.scope` object with its own `targets` key
  missing (or explicitly `null`) is now rejected instead of silently
  defaulting to an empty target list.** This build's writer always emits
  `targets` (a real, possibly-empty array) whenever it emits a scope
  object at all, so a required scope missing that key is the same
  truncated-payload signal `gate.require_complete_analysis`/`gate.scope`
  absence already raises on one level up — extended one level down to the
  nested field via a new shared `_sequence_field` decoder (Codex review,
  fresh evidence, fourth round). A genuinely older (`schema_version < 2`)
  payload still degrades a missing `targets` key to `()`.
