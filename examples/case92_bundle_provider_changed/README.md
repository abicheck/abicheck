# Case 92: Bundle — symbol provider migration

**Category:** Bundle / cross-library | **Verdict:** ⚠️ COMPATIBLE_WITH_RISK
(per-library: BREAKING for libcore, COMPATIBLE for libutil — both
subsumed by the bundle-level provider-move finding)

## What changed
`shared_util` moved from `libcore.so` to `libutil.so` between releases.
The bundle still exports the symbol exactly once, so the absolute symbol
set is unchanged. The set of *providers* changed:

| Symbol        | v1 provider | v2 provider |
|---------------|-------------|-------------|
| `core_add`    | libcore.so  | libcore.so  |
| `shared_util` | libcore.so  | libutil.so  |
| `util_double_add` | libutil.so | libutil.so |

## Why this is risk, not a hard break
- Downstream binaries linked with `-lcore -lutil` get both; the loader
  resolves `shared_util` from whichever DSO is searched first (the new
  bundle still has it).
- Downstream binaries linked with only `-lcore` had `DT_NEEDED libcore.so`
  in v1. In v2 they still load `libcore.so`, but `shared_util` is no
  longer in it. They will fail at runtime *unless* something else in
  their dependency chain pulls in `libutil.so`.

So the bundle is internally consistent but downstream contracts may
break depending on how each consumer was linked.

## Why per-library compare misclassifies
- `compare libcore_v1 libcore_v2` reports `func_removed: shared_util` → BREAKING.
- `compare libutil_v1 libutil_v2` reports `func_added: shared_util` → COMPATIBLE.

Worst-of aggregation reports BREAKING for the release — too pessimistic
in many real cases. The bundle layer recognises the move and downgrades
to `COMPATIBLE_WITH_RISK`.

## What the bundle layer detects
```text
## 🔗 Bundle (Cross-Library) Findings
- bundle_provider_changed — shared_util (provider: libutil.so)
  - Symbol shared_util moved from libcore.so to libutil.so within the bundle.
    Downstream consumers with DT_NEEDED on libcore.so only resolve
    transitively if their dependency chain reaches libutil.so.
```

## Real-world analogue
oneDAL reorganises its internal libraries between major releases
(e.g. detail symbols moving from `libonedal_core` to a new
`libonedal_parameters`). The exported symbol set is preserved at the
bundle level but DT_NEEDED contracts change.
