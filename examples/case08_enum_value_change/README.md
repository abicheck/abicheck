# Case 08: Enum Value Change

**Category:** Type Layout | **Verdict:** 🔴 BREAKING

> **Note on abidiff 2.4.0:** Returns exit **4**. Semantically breaking because
> code compiled against v1 uses hardcoded integer values (e.g., `if (c == 1)` for
> GREEN) that now mean YELLOW.

## What breaks
Any switch statement or comparison against `GREEN` (value 1) now hits the `YELLOW`
branch instead. Serialized data (files, network packets) using the old integer values
becomes misinterpreted.

## Why abidiff catches it
Reports `1 enumerator insertion: 'Color::YELLOW' value '1'` and two enumerator changes
for GREEN and BLUE.

## Code diff

| v1.c | v2.c |
|------|------|
| `{ RED=0, GREEN=1, BLUE=2 }` | `{ RED=0, YELLOW=1, GREEN=2, BLUE=3 }` |

## Reproduce manually
```bash
gcc -shared -fPIC -g v1.c -o libfoo_v1.so
gcc -shared -fPIC -g v2.c -o libfoo_v2.so
abidw --out-file v1.xml libfoo_v1.so
abidw --out-file v2.xml libfoo_v2.so
abidiff v1.xml v2.xml
echo "exit: $?"   # → 4
```

## How to fix
Only append new enum values at the end (never insert). Mark the enum as "reserved
slots allowed" in documentation. Never renumber existing values.

## Real-world example
Protocol Buffers (protobuf) enforces append-only enum values for exactly this reason.
Inserting values in the middle is a common source of subtle bugs in versioned protocols.

## Real Failure Demo

**Severity: CRITICAL**

**Scenario:** app compiled with v1 (GREEN=1) calls `get_signal()` which returns GREEN. With v2 GREEN shifted to 2 — app checks against wrong value.

```bash
# Build v1 + app
gcc -shared -fPIC -g v1.c -o libfoo.so
gcc -g app.c -I. -L. -lfoo -Wl,-rpath,. -o app
./app
# → GREEN (correct)

# Swap in v2 (no recompile)
gcc -shared -fPIC -g v2.c -o libfoo.so
./app
# → ERROR: expected GREEN=1, got 2
```

**Why CRITICAL:** The integer value `1` now means YELLOW in v2 but the compiled app
still checks `if c == 1` for GREEN. Any stored values, protocol messages, or switch
statements using the old numeric constants silently route to the wrong branch.

## References

- [C enum rules](https://en.cppreference.com/w/c/language/enum)
- [libabigail `abidiff` manual](https://sourceware.org/libabigail/manual/abidiff.html)
