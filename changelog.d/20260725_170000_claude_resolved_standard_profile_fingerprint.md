### Fixed

- **Two header-AST dumps with no explicit `-std=`, differing only by
  whether one side's headers silently triggered the C++20 requires/concept
  heuristic, no longer share a `profile_fingerprint`.** `comparability.py`'s
  ADR-050 D1 `language_standard` profile field was fed only by
  `_compiler_options.language_standard_field()`, which reads the explicit
  `--lang`/`-std=` CLI inputs alone — its own docstring flagged this as a
  known gap ("pure content-based auto-detection... is not captured here...
  deferred as a narrower follow-up"). Now that `AbiSnapshot.ast_resolved_standard`
  (schema v15) records the frontend's actual resolved standard — including a
  heuristic-forced `gnu++20` — `language_standard_field()` takes an optional
  `resolved_standard` parameter and `dumper_contract._attach_extraction_contract`
  threads `snapshot.ast_resolved_standard` through it. Two snapshots parsed
  under a genuinely different C++ dialect this way now correctly produce
  different `profile_fingerprint`s, so `checker.compare()`'s existing ADR-050
  D2 comparability gate (`check_contracts_comparable`) raises
  `ProfileMismatchError` instead of silently comparing them — the same
  explicit-`--diagnostic-comparison` escape hatch still lets a caller force
  the comparison through with the mismatch surfaced in `coverage_warnings`.
  Backward compatible: a non-header dump, or a caller that hasn't threaded a
  resolved value through, keeps the prior explicit-`-std=`-only behaviour.
