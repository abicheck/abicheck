### Removed

- **`compare --profile` is gone (ADR-068 D5 / plan Phase 7e).** It bundled
  evidence depth, report rendering, and CI gate policy behind one word,
  which D4/D5 forbid — a rendering choice may never carry a gate setting.
  State `--depth`, `--format`, and `--severity-preset` (or `.abicheck.yml`'s
  `severity:` block) independently instead. `--profile quick`'s one-line
  summary survives as the first-class `--format oneline` choice, so no
  capability is lost.
- **`-j`/`--jobs` is gone (plan Phase 7h).** The directory/package release
  fan-out already auto-detects the CPU count and clamps it to available
  memory; the manual override was a tuning detail, not a per-run decision.
- **`--required-symbols FILE` is merged into `--required-symbol @FILE`**
  (plan Phase 7h). `--required-symbol` now accepts `@FILE` as one of its
  repeatable values, combinable with plain symbol values — two spellings of
  "name a required symbol" collapse into one flag.

### Changed

- **`--format` gains `oneline`** on `compare`: the one-line CI-log summary
  (previously reachable only via the removed `--profile quick`) is now a
  first-class, directly selectable output format.
