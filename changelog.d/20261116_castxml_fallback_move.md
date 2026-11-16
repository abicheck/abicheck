### Changed

- **castxml fallback decision moved to its header owner** — `_castxml_fallback_reason` now lives in `abicheck/extract/headers/castxml_fallback.py` instead of `abicheck/dumper.py`. Pure move; the decision and its messages are unchanged. Callers import it from the new module; the `abicheck.dumper` path is retired.
