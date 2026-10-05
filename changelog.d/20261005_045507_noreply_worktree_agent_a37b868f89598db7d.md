### Fixed

- Importing `abicheck.name_classification` (or `finding_identity`,
  `compare.functions`, ...) as the first abicheck module in a fresh
  interpreter no longer raises `ImportError`: `abicheck/__init__.py` now
  initializes the `model` package first, closing the
  `name_classification -> model.execution_cache -> model/__init__ ->
  name_classification` cycle introduced with the cache wrapper (#1453).
