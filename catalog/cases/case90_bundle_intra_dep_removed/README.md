# Case 90: Bundle — Intra-Bundle Removed Symbol

**Category:** Bundle / cross-library | **Verdict:** 🔴 BREAKING
(per-library: `libalgo.so` COMPATIBLE, `libcore.so` BREAKING)

## Verdict and consumer impact

`libcore.so` removes `core_mul()`. `libalgo.so` is unchanged — it still
imports `core_mul` (`DT_NEEDED libcore.so.1`, undefined `core_mul` in its
`.dynsym`). The new bundle no longer exports `core_mul` from anywhere, so
`dlopen("libalgo.so")` (or any process that loads it) fails at load time
with `undefined symbol: core_mul`. A consumer who only re-links against the
new `libcore.so` and never touches `libalgo.so` still breaks, because the
break lives in the *relationship* between the two libraries, not in either
one's own public surface change.

## Old/new diff

| Library | v1 | v2 |
|---|---|---|
| `libcore.so` | `int core_add(int,int)`, `int core_mul(int,int)` | `int core_add(int,int)` — `core_mul` *(removed)* |
| `libalgo.so` | calls `core_add`, `core_mul` | unchanged — still calls `core_mul` |

## abicheck command

```bash
cmake -S examples -B /tmp/abicheck-examples-build -DCMAKE_BUILD_TYPE=Debug
cmake --build /tmp/abicheck-examples-build \
    --target case90_bundle_intra_dep_removed_old_libcore \
              case90_bundle_intra_dep_removed_old_libalgo \
              case90_bundle_intra_dep_removed_new_libcore \
              case90_bundle_intra_dep_removed_new_libalgo
abicheck compare \
    /tmp/abicheck-examples-build/case90_bundle_intra_dep_removed/old \
    /tmp/abicheck-examples-build/case90_bundle_intra_dep_removed/new \
    --format json
```

## Expected abicheck finding

```text
Verdict: BREAKING (exit 4)

Per-library:
- libalgo.so -> COMPATIBLE (no findings — the file is unchanged)
- libcore.so -> BREAKING
  - func_removed: Public function removed: core_mul

Bundle (cross-library) findings:
- bundle_intra_dep_removed: libalgo.so imports core_mul, but no library
  in the new bundle exports it.
  > Runtime load of libalgo.so will fail with undefined symbol.
```

## Minimum evidence

`min_evidence: L0` — both halves of this finding are binary-only facts:
`core_mul` disappearing from `libcore.so`'s exported `.dynsym` is a plain
symbol-table diff (the same evidence case01's `func_removed` uses), and
`libalgo.so`'s undefined `core_mul` import plus its `DT_NEEDED
libcore.so.1` entry are read straight from its dynamic section. No debug
info or headers are required — stripping `-g` from both builds still
produces the removal (as `func_removed_elf_only` instead of `func_removed`)
and the same `bundle_intra_dep_removed` bundle finding.

## Why abicheck catches it

A pairwise `compare` of `libalgo.so` old vs. new alone reports
`COMPATIBLE` — the file's own exported surface and imports are unchanged.
The break only exists between libraries: `libalgo.so` imports a symbol that
the sibling providing it just dropped. `abicheck compare` on two
directories builds a bundle snapshot of the whole release and cross-checks
every library's *undefined* imports against every other library's
*exported* symbols in the new release; an import with no remaining
provider anywhere in the bundle is `bundle_intra_dep_removed`. Per-library
`compare` has no visibility into that relationship — it only ever sees one
file at a time.

## Runtime failure demonstration

**Severity: BREAKING (cross-library load failure)**

There's no single `app.c` here — the failure is between two library
artifacts, not between an app and one library. The real failure mode:

```text
$ LD_LIBRARY_PATH=new dlopen("libalgo.so", RTLD_NOW)
./libalgo.so: undefined symbol: core_mul
```

`libalgo.so`'s own binary never changed and its own ABI check passes
cleanly — the break is entirely a consequence of what `libcore.so` stopped
providing. Any process that loads both new libraries together, or any
symbol resolution that reaches `algo_square()` (the function that calls
`core_mul`), fails at that point.

## Safe redesign

Never remove a symbol one sibling library still imports without either
removing that sibling too or providing a compatibility shim. Treat the
bundle's internal `DT_NEEDED` graph as part of the public contract — a
release gate should refuse to publish a bundle where any member's imports
resolve to nothing.

**Real-world example:** oneDAL's `libonedal_thread.so` imports symbols
from `libonedal_core.so`. If a refactor moves or deletes an internal core
symbol that `thread` still needs, per-library diffing says "thread is
fine, core changed" — exactly the false-negative this bundle layer exists
to close.

## Cross-tool comparison

`abidiff`/`abi-compliance-checker` compare one library pair at a time. Run
against `libalgo.so` old vs. new alone, either would correctly report "no
ABI change" — the file really is unchanged — which is exactly why this
class of break (nothing in the changed file, everything in what a sibling
stopped exporting) needs bundle/cohort-aware tooling rather than a
stronger per-file diff.
