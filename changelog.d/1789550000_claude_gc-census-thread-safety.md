### Fixed

- **A heap census no longer crashes sibling release workers.** Members of a
  parallel directory/package `compare` could end `ERROR` with CPython's
  `tupleobject.c: bad argument to internal function` when the fan-out ran
  under the graph benchmark (`scripts/bench_graph_materialization.py`). The
  benchmark's attach hook called `len(gc.get_objects())` inside a worker
  thread. That list takes a reference to every GC-tracked object, including a
  tuple another worker is still building with `tuple(<generator>)`, and the
  GIL can switch while the list is alive, so the builder's `_PyTuple_Resize`
  fails its refcount check. New `memory_trace.gc_object_count()` enumerates
  the heap only when this process has no other Python thread, and reports
  `null` otherwise. The benchmark and `scripts/benchmark_scaling.py` now use
  it (or its `gc_census_is_safe()` check), and a test rejects any other
  first-party `gc.get_objects()`/`gc.get_referrers()` call. abicheck's own
  release workers were never affected: without the hook, the same oneDAL
  comparison completed cleanly on every run.
