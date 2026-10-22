### Changed

- Replace `try/except/pass` and `try/except/continue` blocks flagged by CodeFactor in `classify.py` and `model/execution_cache.py` with `contextlib.suppress`; behavior is unchanged.
- Remove duplicated helpers flagged by CodeFactor across `compare/`, `storage/`, `workflows/`, `model/`, `buildsource/check_report.py` and the `diff_types_*` detectors; behavior is unchanged.
