# Case 73: Typedef Underlying Type Changed

**Category:** Type ABI | **Verdict:** 🔴 BREAKING

## Verdict and consumer impact

The typedef `handle_t` changes its underlying type from `int` (4 bytes) to
`void*` (8 bytes on x86-64). Every function using `handle_t` — parameters
and the return value — inherits the break from this single typedef change:
`handle_open()`'s return value grows from 4 to 8 bytes, and the `h`
parameters of `handle_read()`/`handle_close()` do too. A binary compiled
against v1 truncates the returned pointer to 32 bits and passes that
truncated value back into the library, which dereferences a corrupted
pointer. Recompilation is mandatory.

## Old/new diff

| v1.h | v2.h |
|------|------|
| `typedef int handle_t;` | `typedef void *handle_t;` |

## abicheck command

```bash
gcc -shared -fPIC -g v1.c -o libfoo_v1.so
gcc -shared -fPIC -g v2.c -o libfoo_v2.so
abicheck compare libfoo_v1.so libfoo_v2.so
```

## Expected abicheck finding

```text
Verdict: BREAKING (exit 4)

- typedef_base_changed: Typedef base type changed: handle_t (int -> void *)
  > Underlying type changed; old code using the typedef operates on wrong
    representation.
  2 derived change(s) collapsed
  Affected symbols: handle_close, handle_open, handle_read
- param_pointer_level_changed: Parameter pointer level changed: handle_close
  param h (depth 0 -> 1)
- param_pointer_level_changed: Parameter pointer level changed: handle_read
  param h (depth 0 -> 1)
- return_pointer_level_changed: Return pointer level changed: handle_open
  (depth 0 -> 1)

Deployment risk:
- imported_symbol_added: New imported symbol: free@GLIBC_2.2.5, malloc@GLIBC_2.2.5
```

## Minimum evidence

`min_evidence: L1` — DWARF's typedef DIE for `handle_t` records its
underlying type for both versions; abicheck compares that plus every
using-function's parameter/return DWARF types directly from debug info
(`-g`), no public headers required.

## Why abicheck catches it

abicheck resolves `handle_t`'s DWARF typedef target (`int` vs `void *`) and
reports the root `typedef_base_changed` finding, then collapses the
per-function pointer-level changes it implies (`handle_open`'s return,
`handle_read`/`handle_close`'s `h` parameter) under it — the underlying
type comparison needs only debug info, no header parsing.

## Runtime failure demonstration

**Severity: CRITICAL**

**Scenario:** compile app against v1, swap in v2 `.so` without recompile.

```bash
# Build v1 and app
gcc -shared -fPIC -g v1.c -o libhandle.so
gcc -g app.c -L. -lhandle -Wl,-rpath,. -o app
./app
# → handle = 1
# → read 4 bytes
# → Done.

# Swap in v2 (handle_t is now void*, no recompile)
gcc -shared -fPIC -g v2.c -o libhandle.so
./app
# → Segmentation fault (core dumped)
```

**Why CRITICAL:** the app was compiled to treat `handle_t` as a 4-byte
`int`, so it truncates v2's 8-byte pointer return value. Passing that
truncated value into `handle_read()`/`handle_close()` — now `void*`
parameters — makes the library dereference or `free()` a corrupted
pointer, producing the segfault observed above.

## Safe redesign

Design handles as opaque pointers from the start, or use a fixed-width
integer that is already the maximum size ever needed:

```c
/* Option 1: opaque pointer from the start */
typedef struct handle_impl *handle_t;

/* Option 2: use a large enough integer from day one */
#include <stdint.h>
typedef uintptr_t handle_t;  /* always 8 bytes on 64-bit */
```

If the change is unavoidable, bump the SONAME and provide a migration path.

**Real-world example:** this is a common pattern when handles evolve —
POSIX `pid_t` has varied width across platforms, Windows `HANDLE` is
`void*` but was historically `int` in some SDK versions, and database
client libraries sometimes widen handle types to support larger
connection pools.

## Cross-tool comparison

```bash
abidw --out-file v1.xml libfoo_v1.so
abidw --out-file v2.xml libfoo_v2.so
abidiff v1.xml v2.xml
```

## References

- [ABICC: Typedef base type change detection](https://lvc.github.io/abi-compliance-checker/)
- [System V AMD64 ABI: parameter passing for integer types](https://refspecs.linuxfoundation.org/elf/x86_64-abi-0.99.pdf)
