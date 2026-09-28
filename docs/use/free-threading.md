---
doc_type: how-to
level: intermediate
audience:
  - library-maintainer
  - ci-owner
canonical_for:
  - concurrency-and-free-threading
depends_on:
  - abicheck/process_resources.py
  - abicheck/workflows/release_jobs.py
  - abicheck/buildsource/include_graph_workers.py
lifecycle: active
generated: false
---

# Faster release comparisons on free-threaded Python

A directory or package `compare` (a *release*: many libraries compared at
once) spends almost all of its time in Python: decoding header ASTs, building
the ABI model, and running detectors. On a regular CPython build the global
interpreter lock (GIL) lets only one thread run Python at a time, so extra
threads cannot make that work faster. A **free-threaded** CPython build
(`python3.15t`, [PEP 703](https://peps.python.org/pep-0703/)) has no GIL, and
abicheck uses the extra cores automatically.

## What you gain

Measured on a 12-library C++ release at header depth, 4-core host, header-AST
cache warm, identical reports in every run:

| Interpreter | Libraries compared at once | Wall time |
|---|---|---|
| CPython 3.13 (GIL) | 2 (default) | 157–177 s |
| CPython 3.15t (free-threaded) | 1 | 114 s |
| CPython 3.15t (free-threaded) | 4 (default on 4 cores) | 51 s |

- **Throughput scales with cores.** Under the GIL a run uses about one core
  (CPU ≈ 108%) however many threads it starts. Free-threaded, the same run
  compared four libraries at once and finished 3.4× faster than on 3.13.
- **Each comparison is faster too.** The old and new side of every library
  are resolved in parallel; without a GIL they really overlap, which is why
  even one library at a time beat 3.13 (114 s vs ~160 s).
- **Same results.** Reports are byte-identical between the GIL and
  free-threaded interpreters, and between one and many parallel libraries.
  Output order never depends on which thread finishes first.
- **No new tuning.** abicheck detects the interpreter
  (`sys._is_gil_enabled()`): under the GIL it runs 2 libraries at once (more
  only costs memory); free-threaded it runs one per CPU, still bounded by the
  memory admission gate.

Single-library `compare` and `dump` gain less, since only the old/new sides,
the per-TU header parses, and (under the default castxml frontend) the header
graph's own clang parse run in parallel there. That last overlap helps on
every interpreter when the header cache is cold: 11% faster on 3.13, 15% on
3.15t, for one library.

## Trying it

Free-threaded CPython 3.15 (a release candidate until 3.15.0 is final) is
available from the python.org installers (the "free-threaded" option),
`uv python install 3.15t`, and conda-forge (`python=3.15=*_cp315t`, from the
`conda-forge/label/python_rc` channel while it is a release candidate). Install abicheck into it as usual:

```bash
python3.15t -m pip install abicheck
python3.15t -m abicheck compare old/ new/ -H old=old/include -H new=new/include
```

Check that the GIL really is off (a C extension without free-threading
support turns it back on at import, with a warning):

```bash
python3.15t -c "import abicheck, sys; print('GIL enabled:', sys._is_gil_enabled())"
```

All of abicheck's dependencies ship free-threaded builds; this is verified by
the `free-threading (py3.15t)` CI job, which fails if importing any abicheck
module re-enables the GIL.

In the GitHub Action, set the interpreter with the existing input:

```yaml
- uses: abicheck/abicheck@v0.6.0
  with:
    python-version: '3.15t'
```

## Controlling threads and memory

All controls are environment variables.

| Variable | Controls | Default |
|---|---|---|
| `ABICHECK_MEMBER_JOBS` | Libraries compared at once in a release | 2 with the GIL, CPU count free-threaded |
| `ABICHECK_MAX_THREADS` | Total worker threads across every abicheck pool | unlimited |
| `ABICHECK_RELEASE_JOB_MEM_GIB` | Memory budget per library (can lower the above) | depth-dependent |
| `ABICHECK_INCLUDE_MAP_JOBS` | Concurrent `clang -M` include probes (and the probe pool's size) | CPU count, memory-capped |
| `ABICHECK_PARALLEL_EXTRACTION=0` | Resolve a comparison's two sides one after the other | parallel |

Each library in flight holds its own working set, so free-threaded runs trade
memory for speed: peak memory grows with the number of libraries compared at
once. On a memory-constrained runner, lower `ABICHECK_MEMBER_JOBS` or set
`ABICHECK_RELEASE_JOB_MEM_GIB`. `ABICHECK_MAX_THREADS` caps the total without
risk: a pool that finds the budget spent runs its work in the calling thread,
so a low cap is slower but never deadlocks and never changes a result.
Full reference: [Environment variables](../reference/environment.md).

## Caveats

- Python 3.15 is new. Free-threaded builds are officially supported, but
  single-threaded code runs somewhat slower on them than on a GIL build; the
  win comes from parallelism, so it is largest for releases with several
  libraries and a host with several cores.
- The speedup is capped by memory as much as by cores: with *N* libraries in
  flight, expect roughly *N* times one library's working set.
