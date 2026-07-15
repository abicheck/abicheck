# Case 30 — Field Qualifier Changes (const, volatile)

**Category:** Type Qualifiers | **Verdict:** 🔴 BREAKING (policy-escalated API break)

The underlying compatibility **fact** is `API_BREAK`, exactly like case95 and
case109: `abi_break: false`, `api_break: true` in `ground_truth.json` — the
binary layout of `struct SensorConfig` is unchanged, so an already-built
consumer binary keeps linking and running against v2 unmodified. `expected`
is escalated to `BREAKING` as a **policy** decision (see `policy_note` in
`ground_truth.json`): the project's default policy conservatively routes
field-qualifier changes through the same contract detector as other
field-type changes, treating the semantic-divergence risk (a `const` write
becoming a silent bug, a missing `volatile` risking stale-cache reads) as
release-blocking rather than recompile-only. This is a deliberate policy
choice, not a claim that the binary itself is incompatible.

## Compatibility classification

- **Binary ABI impact:** None — layout-compatible (no size/offset change); an existing binary keeps linking and running against v2.
- **Source compatibility impact:** API_BREAK (`const` write errors, `volatile` contract changes) — the underlying compatibility fact.
- **Runtime behavior impact:** Semantic divergence (stale reads / UB writes) without linker errors, if an old binary's assumptions about a now-`const`/`volatile` field are wrong.
- **Policy severity:** **BREAKING** in `ground_truth.json` — a policy escalation of the API_BREAK fact, not a second independent verdict.

## What changes

| Field | v1 | v2 | Effect |
|---|---|---|---|
| `sample_rate` | `int sample_rate` | `const int sample_rate` | Writing becomes UB |
| `raw_value` | `int raw_value` | `volatile int raw_value` | Compiler must not cache reads |
| `cache_hits` | `int cache_hits` | `int cache_hits` | Unchanged |

## Why this is an API break despite binary compatibility

The binary layout of `struct SensorConfig` is **unchanged** — `const` and `volatile`
do not affect size, alignment, or field offsets. An existing binary will link and
run against the v2 library without error. This is not an ABI break.

However, the **API contract** has changed:

1. **`const int sample_rate`:** Code compiled against v1 freely writes to `sample_rate`.
   The v2 header declares this field `const`, meaning the library now considers it
   immutable after initialization. Writing to a `const`-qualified field through a
   non-`const` pointer is undefined behavior in C. Compilers recompiling against v2
   will reject the write at compile time.

2. **`volatile int raw_value`:** Code compiled against v1 may have the compiler optimize
   away redundant reads of `raw_value`. The v2 header marks it `volatile`, indicating
   it may change asynchronously (e.g., hardware-mapped). Binaries compiled without
   `volatile` may return stale cached values.

## Code diff

```diff
 struct SensorConfig {
-    int   sample_rate;
-    int   raw_value;
+    const int    sample_rate;
+    volatile int raw_value;
     int   cache_hits;
 };
```

## Real Failure Demo

**Severity: MODERATE (semantic break, not crash)**

**Scenario:** Compile app against v1 headers, swap in v2 `.so`.

```bash
# Build v1 library + app
gcc -shared -fPIC -g v1.c -o libfoo.so
gcc -g app.c -I. -L. -lfoo -Wl,-rpath,. -o app
./app
# → Field qualifier change demo (compiled against v1.h):
# →
# → Initial state:
# →   sample_rate = 1000
# →   raw_value   = 42
# →   cache_hits  = 0
# →
# → sensor_read(&cfg) = 42
# →
# → After setting sample_rate = 2000:
# →   sample_rate = 2000
# →
# → raw_value read twice: r1=99 r2=99 (should be equal)
# → ...
# → sensor_read(&cfg) after modifications = 99

# Swap in v2 (no recompile)
gcc -shared -fPIC -g v2.c -o libfoo.so
./app
# → Output is identical — binary layout unchanged.
# → But the semantic contract is now violated: the app writes
# → to sample_rate which v2 declares const.
```

**Source break verification** (recompilation against v2 will warn/error):

```bash
# Create a temporary source that includes v2.h instead of v1.h
sed 's/#include "v1.h"/#include "v2.h"/' app.c > /tmp/app_v2_test.c
gcc -g /tmp/app_v2_test.c -I. -L. -lfoo -Wl,-rpath,. -o app_v2 2>&1
# → error: assignment of read-only member 'sample_rate'
#   (because sample_rate is const in v2.h)
rm -f /tmp/app_v2_test.c
```

## Reproduce with abicheck

```bash
gcc -shared -fPIC -g v1.c -o libfoo_v1.so
gcc -shared -fPIC -g v2.c -o libfoo_v2.so
abidw --out-file v1.xml libfoo_v1.so
abidw --out-file v2.xml libfoo_v2.so
abidiff v1.xml v2.xml
echo "exit: $?"
```

## How to fix

- Do not add `const` to fields of public structs unless the field was always
  documented as read-only.
- If a field must become immutable, provide setter/getter functions instead of
  direct field access, and hide the struct behind an opaque pointer.
- Adding `volatile` should be done only in a new struct or with a major version bump.

## References

- [C type qualifiers (`const`)](https://en.cppreference.com/w/c/language/const)
- [C type qualifiers (`volatile`)](https://en.cppreference.com/w/c/language/volatile)
- [C volatile semantics in systems code (WG14 N2148 discussion)](https://www.open-std.org/jtc1/sc22/wg14/www/docs/n2148.htm)
