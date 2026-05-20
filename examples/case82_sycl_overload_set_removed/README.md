# Case 82: SYCL overload set removed (DPC++ build withdrawn)

**Category:** Overload-family ABI | **Verdict:** BREAKING

## What breaks

In v1 the library exports every algorithm entry point twice — once with a
CPU signature and once accepting `sycl::queue&` as the first parameter:

```cpp
result_t compute(const descriptor&, const table&);
result_t compute(sycl::queue&, const descriptor&, const table&);   // SYCL
```

In v2 the DPC++ build is dropped. The CPU overloads are unchanged; the
`sycl::queue&` overloads disappear — typically 30–80 symbols across an
algorithm catalog like oneDAL's.

Mechanically this is N×`func_removed`. Reporting it that way buries the
signal under a wall of independent removals, making the suppression UX a
mess (one rule per algorithm) and obscuring the deployment-level question
("did the SYCL surface go away?").

## Why this is its own ChangeKind

A grouped finding — `SYCL_OVERLOAD_SET_REMOVED` — names the deployment
event in one place: "the DPC++ overload family was removed (N affected
entry points: compute, train, infer, finalize, …)". Per-symbol
`func_removed` findings for the affected entries are suppressed as
children.

## How abicheck detects it

After the per-symbol removal pass, the new detector
(`abicheck/diff_sycl_overloads.py`) groups removed symbols by their
demangled *unqualified* name and parameter list-minus-first-arg.
When ≥ K removed siblings share an unqualified name with a surviving
non-SYCL overload AND the removed one's first parameter type contains
``sycl::queue``, a single `SYCL_OVERLOAD_SET_REMOVED` finding is emitted
listing the affected entry-point family.

## Real-world reference

`cpp/oneapi/dal/algo/*/compute.hpp`, `train.hpp`, `infer.hpp` each ship a
CPU overload and a `sycl::queue&` overload guarded by `ONEDAL_DATA_PARALLEL`.
Switching off DPC++ at build time withdraws every queue-taking overload in
one go.
