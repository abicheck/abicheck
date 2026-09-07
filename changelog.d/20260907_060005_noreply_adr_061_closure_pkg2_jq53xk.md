### Changed

- **`check_architecture.py` now resolves dynamic `importlib.import_module`
  bridges as real import edges** — ADR-061 closure package 2 (gap A):
  a literal first-party `importlib.import_module("...")` call (including a
  module-level alias like `_importlib = importlib`) is treated as an import
  edge by the `dependency-direction` check, closing the gap where such a
  call stayed invisible to the checker's `ast.Import`/`ast.ImportFrom` walk.
  `workflows/render.py` retired (`service.py` now imports `service_render`
  directly); `cli_dump_helpers.py` now statically re-exports from
  `header_conditionals.py`. The two remaining real cross-layer edges this
  re-measurement found (`service.py -> service_render`,
  `cli_dump_helpers.py -> header_conditionals.py`) are recorded as reviewed
  exceptions in `architecture/debt.yaml`'s new `dependency_direction_exceptions`
  list rather than left dynamic and unlisted.
