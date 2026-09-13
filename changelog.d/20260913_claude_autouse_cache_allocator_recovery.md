### Fixed

- `tests/conftest.py`'s autouse snapshot-cache fixture now recreates pytest's
  basetemp before allocating, so a transient deletion anywhere in the temp tree
  no longer fails every remaining test in the worker. The bucket's own
  existence check covered the bucket disappearing but not its parent, so once a
  worker's basetemp was gone every subsequent test errored with a
  `FileNotFoundError` naming a different freshly-generated bucket — two Linux
  unit lanes reported 20,867 and 29,164 such errors from one root cause. Bug
  class: `test_infra.autouse_allocator_cannot_recover`.
