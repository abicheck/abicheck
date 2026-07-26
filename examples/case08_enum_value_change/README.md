# Case 08: Enum Value Change

**Category:** Type Layout | **Verdict:** 🔴 BREAKING

## Verdict and consumer impact

Any code compiled against v1 that compares against or stores the numeric
value `1` for `GREEN` now silently means `YELLOW` under v2. Serialized data
(files, network packets, saved config) using the old integer values is
misinterpreted after the swap — there is no crash, just wrong behavior.

## Old/new diff

| v1.h | v2.h |
|------|------|
| `{ RED=0, GREEN=1, BLUE=2 }` | `{ RED=0, YELLOW=1, GREEN=2, BLUE=3 }` |

## abicheck command

```bash
gcc -shared -fPIC -g v1.c -o libfoo_v1.so
gcc -shared -fPIC -g v2.c -o libfoo_v2.so
abicheck compare libfoo_v1.so libfoo_v2.so
```

## Expected abicheck finding

```text
Verdict: BREAKING (exit 4)

- enum_member_value_changed: Enum member value changed: Color::GREEN (1 -> 2)
  > Old binaries use stale numeric values; logic comparisons and switch
    statements silently break.
- enum_member_value_changed: Enum member value changed: Color::BLUE (2 -> 3)
  > Old binaries use stale numeric values; logic comparisons and switch
    statements silently break.

Additions:
- enum_member_added: Enum member added: Color::YELLOW (1)
  > New enumerator may shift subsequent values in non-fixed enums; switch
    defaults may miss the new case.
```

## Minimum evidence

`min_evidence: L1` — DWARF's enumerator entries (`DW_TAG_enumerator`) record
each member's name and integer value for both versions, so `-g` alone is
enough to detect the renumbering; no public headers required.

## Why abicheck catches it

DWARF preserves each enum member's value alongside its name; abicheck
diffs the two versions' enumerator lists by name and flags any member whose
associated integer value changed, independent of any textual reordering in
source.

## Runtime failure demonstration

**Severity: CRITICAL**

**Scenario:** app compiled with v1 (`GREEN=1`) calls `get_signal()`, which
returns `GREEN`. With v2, `GREEN` shifted to `2` — the app's compiled-in
comparison against `1` no longer matches.

```bash
# Build v1 + app
gcc -shared -fPIC -g v1.c -o libfoo.so
gcc -g app.c -I. -L. -lfoo -Wl,-rpath,. -o app
./app
# → GREEN (correct)

# Swap in v2 (no recompile)
gcc -shared -fPIC -g v2.c -o libfoo.so
./app
# → WRONG RESULT: expected GREEN, got 2
```

**Why CRITICAL:** the integer value `1` now means `YELLOW` in v2, but the
compiled app still checks `if (c == GREEN)` against the old constant `1`.
Any stored values, protocol messages, or switch statements using the old
numeric constants silently route to the wrong branch.

## Safe redesign

Only append new enum values at the end — never insert them in the middle.
Document the enum as append-only, and never renumber existing values.

**Real-world example:** Protocol Buffers enforces append-only enum values
for exactly this reason; inserting values in the middle is a common source
of subtle bugs in versioned wire formats.

## Cross-tool comparison

```bash
abidw --out-file v1.xml libfoo_v1.so
abidw --out-file v2.xml libfoo_v2.so
abidiff v1.xml v2.xml
echo "exit: $?"   # → 4
```

> **Note on abidiff 2.4.0:** reports `1 enumerator insertion:
> 'Color::YELLOW' value '1'` plus enumerator changes for `GREEN` and
> `BLUE`, exit **4**. Semantically breaking because code compiled against
> v1 uses hardcoded integer values (e.g. `if (c == 1)` for `GREEN`) that
> now mean `YELLOW`.

## References

- [C enum rules](https://en.cppreference.com/w/c/language/enum)
- [libabigail `abidiff` manual](https://sourceware.org/libabigail/manual/abidiff.html)
