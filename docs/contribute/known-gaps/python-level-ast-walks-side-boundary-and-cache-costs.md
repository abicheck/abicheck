---
doc_type: contributor
audience:
  - contributor
level: advanced
lifecycle: active
generated: false
---

# Python-level AST walks, the side boundary and the snapshot cache dominate large runs (2026-10-11)

Index: [Known gaps](../known-gaps.md)

**Status (2026-10-11 at `b2e478513`): OPEN.** Measured on oneDAL (45/125 header roots), a 2021.x DWARF dump and a 28-member release; the cheap levers landed (see below), the structural ones did not.

## Landed

- `materialize_locations` iterates siblings in a C-level `for` loop and only
  touches its stack on descent (~15 % on a synthetic tree; the walk was
  23.5 s self / 46 M `isinstance` of 435 s profiled on oneDAL).
- `process_resources.gc_relaxed` raises the gen-0 threshold around the DWARF
  CU walk on GIL builds (44 → 37.5 s on 3.12, 41 → 35.4 s on 3.15 measured
  with a process-wide threshold; identical output, +20-90 MB).
- Free-threaded interpreters resolve two live binaries as two threads, not
  two forked children (`side_isolation.concurrent_threads_apply`).
- `ABICHECK_MEMORY_TRACE` marks `dump.cache.lookup`/`dump.cache.store`;
  `ABICHECK_SNAPSHOT_CACHE_WRITE=0` skips populating the snapshot cache.

## Still open

1. **The clang JSON tree is walked at least eight times**, each paying
   Python per-node dispatch: `dict.get` alone is 225 M calls (expr.walk
   25.5 M, `qualified_name_segments_walk._flag_fields` 24.5 M, templates.walk
   15.4 M, `_flag_plan` 14.4 M, `dumper_clang._walk` 11.1 M,
   `type_graph._node_file` and `call_graph._node_file` 7.8 M each). Fusing
   them (one visitor, per-pass callbacks) is the largest remaining CPU lever;
   `json.load` itself is only 8 s of 435 s. `_node_file` is computed twice.
2. **Side-boundary pickling**: ~6 s `reduction.dumps` + ~2.5 s
   `_pickle.loads` per run on the fork path; `storage/json_compact._compact`
   is the top abicheck self-frame there (4 %).
3. **`GIL_MEMBER_PARALLELISM = 2`**: warm, a 28-member release ran j=1 in
   29:45 / 3.99 GiB against j=2's 30:48 / 9.44 GiB (identical findings). The
   constant exists for the *cold* overlap of one member's clang children with
   another's Python; the cold j=1-vs-2 A/B has not been run. Lower it (or
   make it depend on a warm cache) only on that evidence.
4. **On python3.15t a warm 28-member release is slower than cold**
   (9:57 vs 9:01, 33.7 vs 28.0 GiB, 2 925 vs 2 717 CPU s). Patching out the
   cache population saved ~30 % wall / ~37 % RSS. Attribute with the new
   `dump.cache.*` marks before changing the cache.
5. **`Fact` construction**: `Fact.present` already serves shared constants
   and enum members; the earlier "3.1x `__post_init__`" micro-win could not
   be located on `Fact` (it has no `__post_init__`) — re-profile to find
   which owning dataclass it named.

## Measurement method that worked

- Default-path cProfile only shows the parent blocked in `posix.read`:
  profile the fork path with py-spy, attribute with cProfile under
  `ABICHECK_SIDE_PROCESSES=0`.
- RSS summed across forked children double-counts copy-on-write pages;
  compare PSS (`/proc/<pid>/smaps_rollup`) against a one-process run.
- `tracemalloc` multiplies wall time ~3x and RSS ~2x: memory only.
- Peak RSS (`/usr/bin/time -v`) plus a matched `ABICHECK_MEMBER_JOBS` A/B
  separates interpreter overhead from concurrency.
