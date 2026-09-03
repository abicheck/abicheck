### Documentation

- **`abicheck/compare/opaque_types.py`'s module docstring now accurately
  describes where the opaque-type suppression-eligibility decision lives.**
  It previously claimed `diff_filtering.py` "keeps the policy half — which
  types count as opaque" — but that determination (`find_opaque_types`'s
  `is_opaque`/implementation-source/by-value-exposure logic) has always
  lived in this module, not there. The docstring now explains why the
  ADR-061-correct placement (`diff_filtering.py`'s own no-growth debt pin;
  `policy/`'s import direction, unreachable from `diff_filtering.py`'s own
  `compare/`-legacy classification) isn't reachable within this PR's scope
  rather than misstating the current split (Codex review on PR #1041).
