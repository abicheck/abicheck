### Fixed

- **Concurrent multi-library `compare` no longer fails members with an internal
  `KeyError` from the shared type-spelling caches.** `MATCH_CACHE` and
  `VOCABULARY_CACHE` (`abicheck/compare/spelling_match_cache.py`) are
  process-wide globals every worker thread of a directory/package release
  fan-out reads and writes, and their compound operations were unsynchronized:
  one worker's lookup read a value, a sibling's admission evicted that key, and
  the lookup's own recency update then raised
  `KeyError((id(pattern), text, start, end))` — aborting that member's whole
  comparison. A real six-member release run reported three members as
  `verdict: ERROR` / `operational: extraction_error` / `scope: incomplete` on
  one run and completed cleanly on the next. Every multi-step transition
  (lookup-plus-recency-update, admission-plus-accounting-plus-eviction, pattern
  reference counting, vocabulary publication) is now atomic. Regex compilation
  and matching deliberately stay outside the locks, so duplicate computation on
  simultaneous misses is possible by design and member comparisons are never
  serialized; publication is rechecked afterwards so one vocabulary still
  resolves to exactly one compiled pattern. `clear_caches()` is now a stated
  lifecycle reset rather than a barrier: it advances a generation counter, so a
  computation already in flight still completes and serves its caller but is
  never resurrected into the new epoch.

- **A failed release member keeps its identity and a usable diagnostic.** An
  unexpected internal failure in one library's comparison previously became
  `{"verdict": "ERROR", "error": str(exc)}` — for a bare `KeyError` that is its
  argument alone, naming neither the exception class nor the failing subsystem.
  Such an entry now also carries `error_type`, and the full traceback is
  emitted on the `abicheck.release` logger. The classification itself is
  unchanged: `OperationalStatus.EXTRACTION_ERROR` already covers "a library
  failed to dump/extract/compare", so no schema change was made, and expected
  outcomes (`not_comparable`, `unsupported`) are not reclassified.
