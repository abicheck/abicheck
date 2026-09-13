### Fixed

- **A comparison whose two sides were captured with different public-header
  evidence no longer manufactures ABI breaks.** Each side's compared public
  surface is built from that side's own facts, and one of them --
  `in_public_contract` -- is only established when that side's producer was
  given a public-header set. Two captures of an *unchanged* library, one
  with that set and one without, therefore disagreed about every
  promised-but-unexported declaration (a public inline member, one a version
  script keeps out of `.dynsym`), and the removal path read the disagreement
  as a change: `func_visibility_changed` findings whose `old_value` and
  `new_value` were the same value (`"hidden"`), and `var_removed` for
  variables still declared on both sides. Measured on a six-member release
  bundle compared against a stored `BundleFacts` baseline: 1,591 breaking
  findings and a `BREAKING` verdict, against zero on the identical evidence
  through the single-pair and stored-member-directory routes.

  Such a disagreement is now read as the evidence gap it is -- the same rule
  the matched-pair export path already applied ("exported before, unknown
  now" is a gap, not a transition) -- in both directions, since the same
  unchanged declaration reads as an *addition* when it is the old side that
  lacks the evidence. The surviving declaration is then compared rather than
  dropped, so a real return-type or variable-type change on it is still
  reported -- in every per-pair detector, including the nine that select
  their own surface rather than the shared one: the internal-template-leak
  pass, the ELF deleted-symbol fallback, the global-data-value comparison
  (where a `const` variable's value moving from `1` to `2` had reported
  nothing at all), and the type-spelling and integer-model detectors (which
  lost a real `char8_t_migration` and `integer_model_changed`). The
  reconciliation is memoised per comparison and invalidated on entry to
  `compare()`, so a caller that mutates a snapshot between two comparisons
  of the same objects is never served the earlier call's surfaces. The fix narrows conclusions only: a declaration
  genuinely gone
  from the new side, an export the old artifact's own table confirms it had,
  and a new side whose producer *observed* the declaration out of the public
  contract all still report exactly as before.

- **The release fan-out's per-worker memory budget follows the evidence depth
  the run actually reaches.** It was a single 1.0 GiB constant sized for
  binary-depth workers, so a directory/package `compare` at header depth
  started four to six times as many workers as fit — an OOM-killed job rather
  than a wrong answer, measured at ~20.4 GiB peak RSS on a six-member bundle.
  The budget is now per-rung (4.0 GiB at `headers`, 6.0 at `build`/`source`),
  and the depth it is sized from is the one a worker *reaches*, not the raw
  `--depth`: that flag is a floor and is `None` for an ordinary
  `compare OLD_DIR NEW_DIR --header ...`, so header roots on either side imply
  header depth on their own. `ABICHECK_RELEASE_JOB_MEM_GIB` still overrides
  the default at every depth. A release whose members are *stored* snapshots
  is still sized as binary depth — see `docs/contribute/known-gaps.md` for
  the measurement behind leaving that to its own change.

### Added

- **`ABICHECK_SNAPSHOT_MAX_DECODED_BYTES` and
  `ABICHECK_SNAPSHOT_MAX_STORED_BYTES` are public, documented knobs** for the
  snapshot-envelope decompression-bomb ceilings, which a real bundle member's
  snapshot can legitimately exceed. The underscore-prefixed private spellings
  keep working (and win when both are set), and a malformed or non-positive
  value is now ignored rather than turned into a ceiling that rejects every
  read. See [Environment variables](docs/reference/environment.md).

- **`actions/baseline` takes a `build-config` input**, forwarded as `--config`
  to every library's `dump` call -- the same input `actions/check-target`
  already accepts. Without it a baseline-set was dumped under abicheck's
  built-in defaults while the candidate side was analyzed under the project's
  own config, so any config key affecting what a dump extracts made the two
  sides structurally non-comparable with nothing in either artifact saying so.
