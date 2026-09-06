# Multi-library project: did removing a function break a sibling library too?

You ship more than one shared library from the same build, and they depend
on each other — `libalgo.so` calls into `libcore.so`. Comparing each library
in isolation misses a real class of break: a symbol one library still
*imports* can vanish from the library that used to *export* it, even though
the importing library's own code and headers never changed. This is the
project-wide, cross-library view `abicheck compare` gives you for free when
you point it at two directories instead of two files — no manifest, no
extra flags.

## The project

`libcore.so` exports two functions; `libalgo.so` links against it and calls
one of them:

```c
// core.h
#pragma once
int core_add(int a, int b);
int core_mul(int a, int b);
```

```c
// algo.c
#include "core.h"
int algo_compute(int a, int b) { return core_add(a, core_mul(a, b)); }
```

Between `core_v1.c` and `core_v2.c`, `core_mul` is removed from `libcore.so`.
`algo.c` itself is unchanged and rebuilds cleanly against the new headers —
but the *binary* `libalgo.so` was built against the old `libcore.so` and
still references `core_mul` by name. Nothing about `libalgo.so` in isolation
looks different.

## Build both releases

```bash
cd examples/workflows/compare-project

gcc -shared -fPIC -g core_v1.c -o old/libcore.so
gcc -shared -fPIC -g algo.c -o old/libalgo.so -L old -lcore
gcc -shared -fPIC -g core_v2.c -o new/libcore.so
gcc -shared -fPIC -g algo.c -o new/libalgo.so -L new -lcore
```

## Compare the two release directories

```bash
abicheck compare old new
```

`old/` and `new/` are each a small "project": abicheck discovers every
library in each directory, diffs the ones that match by name, and — because
both are actual directories rather than single files — also builds the
dependency graph between them from each library's `DT_NEEDED` entries and
dynamic symbol table. That graph is what catches this break: `libalgo.so`'s
own diff is clean, but the bundle-level view sees that it still imports a
symbol nothing in the new release exports any more.

```
| **Verdict** | ❌ `BREAKING` |
| **Bundle** | ❌ `BREAKING` (2 cross-library findings) |
```

```
| Library | Verdict | Breaking | Source | Risk | Additions |
|---|---|---|---|---|---|
| `libalgo.so` | ✅ `NO_CHANGE` | 0 | 0 | 0 | 0 |
| `libcore.so` | ❌ `BREAKING` | 1 | 0 | 0 | 0 |
```

```
## 🔗 Bundle (Cross-Library) Findings

- **bundle_intra_dep_removed** — `core_mul` (consumer: `libalgo.so`)
  - libalgo.so imports core_mul, but no library in the new bundle exports it. Runtime load of libalgo.so will fail with undefined symbol.
```

`libalgo.so` alone reports `NO_CHANGE` — its own exports and code are
identical between releases. The bundle finding is the only place this break
is visible: it names the missing symbol (`core_mul`), which library still
imports it (`libalgo.so`), and what will actually happen at runtime
(undefined-symbol failure on load). The exit code is `4` (ABI break),
because `libcore.so`'s own `func_removed` finding already crosses that
threshold on its own — the bundle finding is additional evidence of impact,
not what raises the verdict here.

You'll also see a second bundle finding, `bundle_intra_dep_signature_unverified`,
for `core_add` — because this walkthrough passes no headers, abicheck can't
confirm `core_add`'s signature is unchanged on both sides (only that the
symbol name and mangling survived), so it reports that gap explicitly
instead of assuming compatibility.

See [Multi-binary and bundle comparison](../../../docs/use/multi-binary.md)
for the full model (how libraries are discovered and matched, what the
dependency graph covers, and how to layer a project manifest on top).
