# Case 142: Vtable Slot Count Changed (detected from a stripped binary)

**Category:** C++ Layout | **Verdict:** 🔴 BREAKING

## Verdict and consumer impact

A new virtual function is inserted **between** two existing ones. Under the
Itanium C++ ABI a class's vtable is an ordered array of slots; inserting a
virtual in the middle shifts every later slot down by one. A consumer
compiled against v1 dispatches `perimeter()` through a *fixed slot index*
— after the insertion that index now points at `rotate()` instead. Every
prebuilt binary that calls a virtual method declared after the insertion
point silently misdispatches, with no crash to signal it.

## Old/new diff

```cpp
// v1                              // v2
struct Shape {                     struct Shape {
    virtual int area();                virtual int area();
                                       virtual int rotate();   // <- inserted in the middle
    virtual int perimeter();           virtual int perimeter();
    virtual ~Shape();                  virtual ~Shape();
};                                 };
```

## abicheck command

```bash
g++ -shared -fPIC -g v1.cpp -o libfoo_v1.so
g++ -shared -fPIC -g v2.cpp -o libfoo_v2.so
strip --strip-debug libfoo_v1.so libfoo_v2.so   # remove all DWARF
abicheck compare libfoo_v1.so libfoo_v2.so
```

## Expected abicheck finding

```text
Verdict: BREAKING (exit 4)

- vtable_slot_count_changed: Vtable for 'Shape' changed size: 48 -> 56
  bytes (+1 pointer-sized word). Virtual functions were net added or removed, or
  the inheritance shape changed — the symbol size cannot distinguish them;
  existing binaries dispatch through fixed vtable offsets and may call the
  wrong slot. Detected from the ELF symbol size without
  debug info.

Additions:
- func_added: New public function: Shape::rotate()
```

## Minimum evidence

`min_evidence: L0` — no DWARF, no headers. This case exists specifically to
show binary-only detection: the point isn't that abicheck catches a vtable
change (many cases do that from DWARF/headers), it's that it catches this
one from the **ELF symbol table alone**, on a fully stripped `.so`.

## Why abicheck catches it

The Itanium ABI emits a `_ZTV5Shape` ("vtable for Shape") object whose
layout is `[offset-to-top, typeinfo*, slot0, slot1, …]`. Adding one virtual
grows that object by exactly one pointer (8 bytes on LP64). abicheck's
binary-only layout detector (`diff_elf_layout.py`) reads the `st_size` of
the `_ZTV` symbol on both sides and infers the slot-count delta directly
from that size change — closing the blind spot a pure symbol-*name* diff
would have, since no mangled name needs to change for this break to occur.
When debug info or headers *are* present, the same change is additionally
reported as `type_vtable_changed`/`virtual_method_added` (L1/L2) with
higher confidence; the L0 signal is what makes it detectable on a
production binary with no debug info shipped.

## Runtime failure demonstration

**Severity: BREAKING / SILENT MISDISPATCH**

**Scenario:** app calls `perimeter()` through a `Shape*`. The compiler
emits a virtual call through a *fixed vtable slot index* (slot 1 under
v1). Insert `rotate()` ahead of `perimeter()` in v2 and slot 1 now holds
`rotate()`, so the same binary silently misdispatches.

```bash
# Build old library + app
g++ -shared -fPIC -g v1.cpp -o libshape.so
g++ -g app.cpp -I. -L. -lshape -Wl,-rpath,. -o app
./app
# → perimeter() = 20 (expected 20)

# Swap in new library (no recompile)
g++ -shared -fPIC -g v2.cpp -o libshape.so
./app
# → perimeter() = 99 (expected 20)
# → MISDISPATCH: vtable slot order changed
```

**Why BREAKING:** `perimeter()`'s call site was compiled to invoke whatever
sits at slot 1 of `Shape`'s vtable; under v2 that slot holds `rotate()`
instead, and the app's binary has no way to know without recompiling
against the v2 header.

## Safe redesign

Never insert or reorder virtual functions in a published polymorphic
class; append-only, and even then only with per-ABI review. Prefer
non-virtual extension points or explicitly versioned interfaces.

## Cross-tool comparison

`abidiff` works from DWARF (via `abidw`) and would flag this from debug
info the same way most struct/vtable cases in this catalog do — but it has
no equivalent binary-only (stripped, no-DWARF) detection path; run against
these stripped `.so`s, `abidw` has no type information to extract at all.
abicheck's ELF-only `_ZTV` size heuristic is what makes this specific
production scenario (a stripped release binary) detectable in the first
place.

## References

- [Itanium C++ ABI: vtable layout](https://itanium-cxx-abi.github.io/cxx-abi/abi.html#vtable)
- Related cases:
  [case09_cpp_vtable](../case09_cpp_vtable/README.md),
  [case68_virtual_method_added](../case68_virtual_method_added/README.md),
  [case140_empty_base_optimization_lost](../case140_empty_base_optimization_lost/README.md)
