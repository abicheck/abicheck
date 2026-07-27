### Performance

- **`scan --depth source`: `--budget` now stops dispatching further
  translation units once already exhausted** — the L4 source-ABI replay's
  serial extraction loop and per-unit cache lookup (`buildsource/
  source_replay.py`'s `_extract_cache_misses`/`_replay_cache_lookup`) now
  check the shrinking scan-wide deadline *before* each unit, instead of
  relying solely on each already-dispatched unit's own extractor-level
  self-abort. Found via a real 62-translation-unit library scan where
  `--budget` did not visibly bound wall time on a clang-only (no castxml)
  host; the deeper question of whether that host's per-TU cost was itself
  pathological remains open (`docs/contribute/performance.md`'s new
  "`--budget` mid-step preemption gap" section).
