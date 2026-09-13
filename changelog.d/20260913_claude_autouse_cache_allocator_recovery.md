### Fixed

- `tests/conftest.py`'s autouse snapshot-cache fixture now recreates pytest's
  basetemp before allocating, so a transient deletion anywhere in the temp tree
  no longer fails every remaining test in the worker. The bucket's own
  existence check covered the bucket disappearing but not its parent, so once a
  worker's basetemp was gone every subsequent test errored with a
  `FileNotFoundError` naming a different freshly-generated bucket — two Linux
  unit lanes reported 20,867 and 29,164 such errors from one root cause. Bug
  class: `test_infra.autouse_allocator_cannot_recover`.
  The recovery recreates each level with pytest's own guarantees -- mode `0o700`,
  a refusal on a symlinked or foreign-owned level, and the same loose-mode fixup
  -- since these paths are predictable and sit on a temp root shared between
  users, so a `parents=True` recreate under the process umask would leave the
  hierarchy traversable and accept a path planted in the deletion window.
  Those checks apply only within pytest's own `pytest-of-<user>` root: asserting
  ownership or repairing modes on ancestors above it would error every test on a
  runner that does not own the shared temp directory, and as root would strip that
  directory's world-writable mode machine-wide.
