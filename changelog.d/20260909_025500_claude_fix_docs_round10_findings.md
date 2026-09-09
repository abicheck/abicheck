<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **The GitHub Action's source-scans guide overstated `compare`'s pinned-depth
  guarantee** (Codex review, round 10). The guide said `mode: compare` reads
  `depth` "identically" to `mode: scan`, noting only the missing `auto` rung —
  but a pinned depth is a hard *contract* under `scan` (`scan --depth source`
  with no `--sources`/`--build-info` hard-fails, exit 7) and only a soft
  preference under `compare` (`compare --depth source` with the same missing
  evidence silently degrades to a binary-only comparison and can still exit
  0 — verified live). Documented the gap explicitly, so a copied
  `mode: compare` workflow that drops `sources:`/`build-info:` doesn't read as
  safe when it silently stops running the L3-L5 analysis it asked for.
- **`validation/scripts/fp_depth_demo.py`'s synthetic "full"/L5 column
  measured nothing beyond `source`** (Codex review, round 10, fresh
  evidence). The column's own comment claimed it exercised the L5
  source-graph/call-graph fold on top of `source`'s evidence, but no case
  builder actually constructed L5-distinct input for it — every case fell
  through to the same `(old, new)` pair `source` used, and `band_at()`
  applied the identical public-surface scoping to both — so the column
  always read identically to `source` and could misstate the validation
  experiment to a reader. Removed the column rather than fabricate genuine
  L5 evidence this pure-Python synthetic-snapshot demo has no model to
  construct (`check_tier_accuracy.py` already owns the real,
  artifact-driven per-tier comparison).
