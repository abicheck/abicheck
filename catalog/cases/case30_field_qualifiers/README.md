# Case 30: Field Qualifier Changes (const, volatile)

**Category:** Type Qualifiers | **Verdict:** 🔴 BREAKING (policy-escalated API break)

## Verdict and consumer impact

The binary layout of `struct SensorConfig` is unchanged — `const` and
`volatile` don't affect size, alignment, or offsets, so an already-built
consumer binary keeps linking and running against v2 unmodified. The
underlying compatibility *fact* is API_BREAK, not ABI_BREAK
(`ground_truth.json`: `abi_break: false`, `api_break: true`). abicheck's
default policy escalates the verdict to BREAKING anyway: `sample_rate`
becomes `const` (writing through the old, non-`const` assumption is
undefined behavior and will be rejected on recompilation) and `raw_value`
becomes `volatile` (a binary compiled without the volatile contract may
observe or rely on compiler-cached reads that are no longer valid). The
project treats this semantic-divergence risk as release-blocking by
default rather than recompile-only.

## Old/new diff

| v1.h | v2.h |
|------|------|
| `int   sample_rate;` | `const int    sample_rate;` |
| `int   raw_value;` | `volatile int raw_value;` |
| `int   cache_hits;` *(unchanged)* | `int          cache_hits;` |

## abicheck command

```bash
gcc -shared -fPIC -g v1.c -o libfoo_v1.so
gcc -shared -fPIC -g v2.c -o libfoo_v2.so
abicheck compare libfoo_v1.so libfoo_v2.so
```

## Expected abicheck finding

```text
Verdict: BREAKING (exit 4)

- type_field_type_changed: Field type changed: SensorConfig::sample_rate (int -> const int)
  > Field has different size or representation; old code misinterprets the data.
  Affected symbols: sensor_read
- type_field_type_changed: Field type changed: SensorConfig::raw_value (int -> volatile int)
  > Field has different size or representation; old code misinterprets the data.
  Affected symbols: sensor_read

Quality issues:
- field_became_const: Field became const: SensorConfig::sample_rate
- field_became_volatile: Field became volatile: SensorConfig::raw_value
```

## Minimum evidence

`min_evidence: L1` — DWARF wraps a qualified field's type DIE in
`DW_TAG_const_type`/`DW_TAG_volatile_type`, so the qualifier change is
visible directly in debug info; `-g` alone (no public headers) is enough,
matching the by-value cv-qualifier policy note in `ground_truth.json`.

## Why abicheck catches it

abicheck resolves each struct member's type DIE for both binaries and
compares the resolved type name/qualification. A member that gained a
`DW_TAG_const_type` or `DW_TAG_volatile_type` wrapper reports as a field
type change even though the underlying byte size and member offset are
identical — the layout-neutral, contract-only nature of the change is what
routes it to the quality-issue `field_became_const`/`field_became_volatile`
findings alongside the escalated breaking finding.

## Runtime failure demonstration

**Severity: MODERATE (semantic break, not crash)**

**Scenario:** compile app against v1 headers, swap in the v2 `.so` without
recompiling.

```bash
# Build old library + app
gcc -shared -fPIC -g v1.c -o libfoo.so
gcc -g app.c -I. -L. -lfoo -Wl,-rpath,. -o app
./app
# → sensor_read = 42
# → OK (semantic break only: field qualifiers changed, binary runs identically)

# Swap in new library (no recompile)
gcc -shared -fPIC -g v2.c -o libfoo.so
./app
# → sensor_read = 42
# → OK (semantic break only: field qualifiers changed, binary runs identically)
```

**Why MODERATE, not CRITICAL:** the struct's binary layout is unchanged, so
the already-compiled app keeps running with identical output — there's no
crash or corruption to observe here. The break only surfaces on
recompilation: code that writes to `sample_rate` becomes a compile error
against v2.h, and code relying on non-`volatile` caching of `raw_value` can
silently diverge from hardware-mapped reality once actually rebuilt against
the new header.

**Source break verification** (recompiling against v2 fails):

```bash
sed 's/#include "v1.h"/#include "v2.h"/' app.c > /tmp/app_v2_test.c
gcc -g /tmp/app_v2_test.c -I. -L. -lfoo -Wl,-rpath,. -o app_v2
# → error: assignment of read-only member 'sample_rate'
rm -f /tmp/app_v2_test.c
```

(This app doesn't write to `sample_rate`, so this step only demonstrates
the compile-time contract; a version that assigns `cfg.sample_rate = ...`
would fail to build against v2.h.)

## Safe redesign

Don't add `const` to a field of a public struct unless it was always
documented as read-only, and don't add `volatile` to an existing field —
both are contract changes that recompiled callers must account for. If a
field must become immutable, provide setter/getter functions and hide the
struct behind an opaque pointer instead of relying on the field-qualifier
system; introduce a new struct (or bump the major version) if `volatile`
semantics are genuinely required.

## Cross-tool comparison

```bash
abidw --out-file v1.xml libfoo_v1.so
abidw --out-file v2.xml libfoo_v2.so
abidiff v1.xml v2.xml
echo "exit: $?"
```

## References

- [C type qualifiers (`const`)](https://en.cppreference.com/w/c/language/const)
- [C type qualifiers (`volatile`)](https://en.cppreference.com/w/c/language/volatile)
- [C volatile semantics in systems code (WG14 N2148 discussion)](https://www.open-std.org/jtc1/sc22/wg14/www/docs/n2148.htm)
