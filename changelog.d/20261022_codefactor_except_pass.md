### Changed

- Replace `try/except/pass` and `try/except/continue` blocks flagged by CodeFactor in `classify.py` and `model/execution_cache.py` with `contextlib.suppress` and an explicit failed-future check; behavior is unchanged.
