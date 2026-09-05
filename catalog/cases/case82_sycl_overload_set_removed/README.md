# Case 82: SYCL Overload Set Removed (DPC++ Build Withdrawn)

**Category:** Overload-Family ABI | **Verdict:** 🔴 BREAKING

## Verdict and consumer impact

In v1 the library exports every algorithm entry point twice — a CPU
signature and a `sycl::queue&`-taking SYCL signature:
`compute(const descriptor&, const table&)` and
`compute(sycl::queue&, const descriptor&, const table&)`. v2 drops the
DPC++ build entirely: the CPU overloads survive unchanged, but all four
`sycl::queue&` overloads (`compute`, `train`, `infer`, `finalize`)
disappear. Any consumer built against the SYCL surface (`mylib::compute(q,
d, t)`) fails to resolve its symbol at load time. Mirrors oneDAL's
`ONEDAL_DATA_PARALLEL`-guarded dual-overload pattern.

## Old/new diff

| v1.h | v2.h |
|------|------|
| `result_t compute(const descriptor&, const table&);` *(CPU)* | same, unchanged |
| `result_t compute(sycl::queue&, const descriptor&, const table&);` *(SYCL)* | *(removed — and likewise for `train`, `infer`, `finalize`)* |

## abicheck command

```bash
g++ -std=c++17 -shared -fPIC -g v1.cpp -o libfoo_v1.so
g++ -std=c++17 -shared -fPIC -g v2.cpp -o libfoo_v2.so
abicheck compare libfoo_v1.so libfoo_v2.so
```

## Expected abicheck finding

```text
Verdict: BREAKING (exit 4)

- type_removed: Type removed: sycl::queue
  > Old code references a type that no longer exists; compilation or
    link failure.
- func_removed_elf_only: Elf_only function removed:
  mylib::compute(sycl::queue&, mylib::descriptor const&, mylib::table const&)
- func_removed_elf_only: Elf_only function removed:
  mylib::train(sycl::queue&, mylib::descriptor const&, mylib::table const&)
- func_removed_elf_only: Elf_only function removed:
  mylib::infer(sycl::queue&, mylib::descriptor const&, mylib::table const&)
- func_removed_elf_only: Elf_only function removed:
  mylib::finalize(sycl::queue&, mylib::descriptor const&, mylib::table const&)
```

## Minimum evidence

`min_evidence: L0` — the exported-symbol table alone is enough: all four
`sycl::queue&`-taking mangled symbols are present in v1's `.dynsym` and
absent from v2's. Adding public headers (`-H` + `--ast-frontend clang`)
lets the dedicated overload-family detector collapse these four
`func_removed_elf_only` findings (plus the incidental `sycl::queue` type
removal) into a single grouped `sycl_overload_set_removed` finding naming
all four affected entry points at once — useful for suppression UX, but
not required to reach the BREAKING verdict at the L0 floor.

## Why abicheck catches it

The four SYCL-overload mangled symbols are simply absent from v2's dynamic
symbol table — pure L0 evidence, no debug info or headers required. With
header evidence, `diff_cpp_patterns.py`'s
`detect_sycl_overload_set_removal` additionally groups removed symbols by
demangled unqualified name and parameter-list-minus-first-arg: when several
removed siblings share a name with a surviving non-SYCL overload and the
removed overload's first parameter type contains `sycl::queue`, it emits
one `sycl_overload_set_removed` finding for the whole family instead of N
independent removals — naming the deployment-level event ("the DPC++
overload family was withdrawn") in one place.

> **Known gap on macOS:** integration testing has observed this case
> reporting NO_CHANGE on macOS as of a known commit; Linux (gcc and clang)
> is the reliable lane for this detector. Registered as a `known_gap` in
> `examples/ground_truth.json`.

## Runtime failure demonstration

**Severity: CRITICAL**

**Scenario:** compile app against v1, swap in v2 `.so` without recompile —
the app calls the SYCL overload directly.

```bash
# Build old library + app
g++ -std=c++17 -shared -fPIC -g v1.cpp -o libfoo.so
g++ -std=c++17 -g app.cpp -L. -lfoo -Wl,-rpath,. -o app
./app
# → cpu=0 gpu=1

# Swap in new library (no recompile)
g++ -std=c++17 -shared -fPIC -g v2.cpp -o libfoo.so
./app
# → app: symbol lookup error: app: undefined symbol:
#   _ZN5mylib7computeERN4sycl5queueERKNS_10descriptorERKNS_5tableE
```

**Why CRITICAL:** `mylib::compute(sycl::queue&, ...)` is resolved lazily by
the dynamic linker at first call; the CPU-only `compute(d, t)` call
succeeds, but the SYCL call crashes the process the moment it's reached —
exactly the load-time failure mode DPC++-build-trims produce in practice.

## Safe redesign

Ship a deprecation cycle instead of silently dropping the DPC++ build:
keep exporting the `sycl::queue&` overloads (even as thin wrappers that
report "GPU support not built into this package" at runtime) for at least
one release, and communicate the DPC++ withdrawal in release notes before
removing the symbols outright.

**Real-world example:** `cpp/oneapi/dal/algo/*/compute.hpp`, `train.hpp`,
`infer.hpp` each ship a CPU overload and a `sycl::queue&` overload guarded
by `ONEDAL_DATA_PARALLEL`. Switching DPC++ off at build time withdraws
every queue-taking overload across the algorithm catalog in one go —
typically 30-80 symbols.
