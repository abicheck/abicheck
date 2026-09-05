# Case 43: Base Class Member Added

**Category:** Type Layout | **Verdict:** 🔴 BREAKING

## Verdict and consumer impact

`Base` gains a new data member (`extra_field`). Because `Derived` inherits
from `Base`, every field declared in `Derived` shifts to a higher offset —
`Derived::value` moves from byte offset 12 to byte offset 16, and
`sizeof(Derived)` grows from 16 to 24. Any binary compiled against v1 that
allocates or accesses a `Derived` reads/writes the wrong memory once linked
against v2. Recompilation is mandatory.

## Old/new diff

| v1.hpp | v2.hpp |
|--------|--------|
| `class Base { public: int base_id; virtual void describe(); };` | `class Base { public: int base_id; int extra_field; virtual void describe(); };` |
| `class Derived : public Base { public: int value; void process(); };` | *(unchanged — but `value`'s offset shifts)* |

## abicheck command

```bash
g++ -shared -fPIC -g -femit-class-debug-always v1.cpp -o libfoo_v1.so
g++ -shared -fPIC -g -femit-class-debug-always v2.cpp -o libfoo_v2.so
abicheck compare libfoo_v1.so libfoo_v2.so
```

`-femit-class-debug-always` matters here for the same reason as case37:
`Derived` declares no virtual method of its own (its virtuals are all
inherited from `Base`), so GCC has no class-local key function to anchor a
complete class DIE to and emits only a declaration stub for `Derived` by
default — dropping the member-offset information abicheck needs to see the
shift in `Derived::value`. The flag forces GCC to always emit the complete
class layout in DWARF. Without it, only `Base`'s own field addition is
visible (`Base` has an out-of-line virtual method, `describe()`, so it
already gets full DWARF either way) — `Derived`'s size/offset shift is
missed.

## Expected abicheck finding

```text
Verdict: BREAKING (exit 4)

- type_field_added: Field added: Base::extra_field
  > New field shifts subsequent fields; old code reads wrong offsets
    for all fields after insertion point.
- type_size_changed: Size changed: Derived (128 -> 192 bits)
  > Old code allocates or copies the type with the old size; heap/stack
    corruption, out-of-bounds access.
- type_field_offset_changed: Field offset changed: Derived::value (96 -> 128 bits)
  > Old code reads/writes fields at stale offsets; silent data corruption.
```

## Minimum evidence

`min_evidence: L1` — DWARF's per-member offsets (`DW_AT_data_member_location`)
for both `Base` and `Derived`, plus each type's `DW_AT_byte_size`, are
enough to detect the field addition and the resulting size/offset shift in
the derived class; no public headers required. The `-femit-class-debug-always`
caveat above is about getting complete DWARF out of GCC for a
no-key-function derived class, not about needing a higher evidence tier.

## Why abicheck catches it

DWARF records each struct/class member's byte offset directly
(`DW_AT_data_member_location`); abicheck compares `Base`'s member list
(catching the added `extra_field`) and independently compares `Derived`'s
total size and each of its own members' offsets, catching the downstream
shift even though `Derived`'s own source didn't change.

## Runtime failure demonstration

**Severity: CRITICAL**

**Scenario:** app allocates a `Derived` with v1's layout (`value` at offset
12), sets `base_id` and calls `process()` (which writes `value` from
`base_id`), then the library is swapped for v2 without recompiling the app.

```bash
# Build old library + app
g++ -shared -fPIC -g -femit-class-debug-always v1.cpp -o libfoo.so
g++ -g app.cpp -I. -L. -lfoo -Wl,-rpath,. -o app
./app
# → value = 42
# → expected = 42
# → exit 0

# Swap in new library (no recompile)
g++ -shared -fPIC -g -femit-class-debug-always v2.cpp -o libfoo.so
./app
# → value = 0
# → expected = 42
# → CORRUPTION: base-class layout changed, Derived::value offset mismatch
# → exit 1
```

**Why CRITICAL:** v2's `Derived::process()` writes `value` at offset 16 (its
correct offset under v2's layout), but the app's `Derived d` object was
allocated with v1's 16-byte layout — the write lands past what the app
treats as `value`'s slot, so the app's read of `d.value` sees stale/zeroed
memory instead of the computed result. No crash, just silently wrong data.

## Safe redesign

Never add a data member to a base class that has public derived classes —
every derived class's layout shifts. Put new base-class state behind a
private `Impl*` (PIMPL) pointer instead, add the field to the specific
derived class that needs it (if only one does), or bump the SONAME.

**Real-world example:** this is the same failure mode that motivated
d-pointer/PIMPL adoption in Qt and KDE — adding *any* base-class member,
even a private one, breaks every derived class's ABI unless the base is
already opaque.

## Cross-tool comparison

`abidiff` also catches this (`Added_Base_Class_Data_Member`):

```bash
abidw --out-file v1.xml libv1.so
abidw --out-file v2.xml libv2.so
abidiff v1.xml v2.xml
echo "exit: $?"   # → 4 (ABI change: base class data member added)
```

> **Note on abidiff 2.4.0:** exits **4** (not 12) because this is a layout
> change, not a symbol removal — the change is still semantically breaking.
