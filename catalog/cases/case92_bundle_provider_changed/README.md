# Case 92: Bundle — Symbol Provider Migration

**Category:** Bundle / cross-library | **Verdict:** ⚠️ COMPATIBLE_WITH_RISK
(bundle-level finding) — combined `abicheck compare` verdict is 🔴 BREAKING
(exit 4), the worst-of the per-library `BREAKING` result for `libcore.so`.

## Verdict and consumer impact

`shared_util` moves from `libcore.so` to `libutil.so` between releases. The
bundle's total exported-symbol set is unchanged — some library in the
release still exports `shared_util` — but *which* library provides it
changed. A consumer whose binary has `DT_NEEDED libcore.so` only (it linked
`-lcore` and never touched `-lutil`) loses the symbol at runtime unless
something else in its dependency chain happens to pull in `libutil.so`. A
consumer that links both, or already depends on `libutil.so` transitively,
is unaffected. abicheck's bundle layer recognizes the migration and reports
`bundle_provider_changed` / `COMPATIBLE_WITH_RISK` instead of a flat
removal — but `libcore.so` compared on its own is still `BREAKING`, and the
combined `abicheck compare` verdict (worst-of across libraries and bundle)
reflects that.

## Old/new diff

| Library | v1 exports | v2 exports |
|---|---|---|
| `libcore.so` | `core_add`, `shared_util` | `core_add` |
| `libutil.so` | `util_double_add` | `util_double_add`, `shared_util` |

## abicheck command

```bash
g++ -shared -fPIC -g old/libcore.cpp -o old/libcore.so
g++ -shared -fPIC -g old/libutil.cpp -o old/libutil.so
g++ -shared -fPIC -g new/libcore.cpp -o new/libcore.so
g++ -shared -fPIC -g new/libutil.cpp -o new/libutil.so
abicheck compare old/ new/ --format markdown
```

## Expected abicheck finding

```text
Verdict: BREAKING (exit 4)
Bundle:  COMPATIBLE_WITH_RISK (1 cross-library finding)

libcore.so -> BREAKING
- func_removed: Public function removed: shared_util

libutil.so -> COMPATIBLE
- (func_added: shared_util)

## 🔗 Bundle (Cross-Library) Findings
- bundle_provider_changed: shared_util (provider: libutil.so)
  > Symbol shared_util moved from libcore.so to libutil.so within the
    bundle. Downstream consumers with DT_NEEDED on libcore.so only resolve
    transitively if their dependency chain reaches libutil.so.
```

## Minimum evidence

`min_evidence: L0` — each library's exported-symbol table (`.dynsym`) is
enough for both the per-library `func_removed`/`func_added` pair and the
bundle-level `bundle_provider_changed` correlation. No debug info or
headers needed; `-g` above is only there so the *Runtime failure
demonstration* below can build a matching app.

## Why abicheck catches it

The bundle layer (ADR-023) builds a symbol→provider map across every
library in a release from each `.dynsym`, then diffs that map between
releases: a symbol whose provider library changed (rather than
disappearing from the release entirely) is `bundle_provider_changed` —
distinct from the per-library `func_removed`/`func_added` pair a single-pair
`compare` would report on `libcore.so`/`libutil.so` in isolation, with no
way to correlate the two.

## Runtime failure demonstration

**Severity: RISK (linkage-dependent)**

**Scenario:** an app links only `-lcore` (the pre-migration provider) and is
not recompiled against the new release.

```bash
# Build old libcore.so + app linked only against -lcore
g++ -shared -fPIC -g old/libcore.cpp -o libcore.so
gcc app.c -L. -lcore -Wl,-rpath,. -o app
./app
# → core_add(2,3) = 5
# → shared_util(5) = 10

# Swap in new libcore.so (shared_util moved out, app not recompiled)
g++ -shared -fPIC -g new/libcore.cpp -o libcore.so
./app
# → ./app: symbol lookup error: ./app: undefined symbol: shared_util
```

**Why RISK, not always BREAKING:** the same app relinked against both
`-lcore -lutil` (or one that already picks up `libutil.so` transitively)
keeps working unmodified — the symbol still exists somewhere in the
release, just from a different `.so`. That link-configuration dependency is
exactly what downgrades this from a flat "symbol removed" BREAKING to the
bundle layer's `COMPATIBLE_WITH_RISK`.

## Safe redesign

Don't move a public symbol between libraries within one release without
keeping a forwarding re-export in the old location (or documenting the
move loudly enough that every narrowly-linked consumer gets recompiled). If
consolidation across libraries is unavoidable, ship a transition release
where the symbol is present in *both* the old and new provider before
removing it from the old one.

**Real-world example:** oneDAL reorganizes its internal libraries between
major releases (e.g. detail symbols moving from `libonedal_core` to a new
`libonedal_parameters`). The exported symbol set is preserved at the bundle
level but `DT_NEEDED` contracts of narrowly-linked consumers change.

## Cross-tool comparison

`abidiff` and `abi-compliance-checker` compare one library pair at a time —
neither has a bundle-aware, cross-library notion of "symbol moved to a
sibling library in the same release." Run per-library, either would report
exactly the per-library split shown above (`func_removed: shared_util` for
`libcore.so`, `func_added: shared_util` for `libutil.so`) with no way to
correlate the two into a single provider-migration finding, and no basis
for downgrading the verdict from a flat removal.
