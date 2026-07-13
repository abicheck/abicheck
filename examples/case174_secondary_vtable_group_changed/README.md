# Case 174: Secondary Vtable Group Changed

**Category:** C++ Layout (DWARF/L1) | **Verdict:** BREAKING

## What this case is about

```cpp
// v1                                    // v2
struct Base1 {                           struct Base1 { ... };   // unchanged
    virtual int f1();
    virtual ~Base1();
};
struct Base2 {                           struct Base2 {
    int helper();     // non-virtual         virtual int helper();  // <- now virtual
};                                            virtual ~Base2();
                                          };
struct Derived : Base1, Base2 {};        struct Derived : Base1, Base2 {};  // BYTE-IDENTICAL
```

`Derived`'s own declaration — its base list, `struct Derived : Base1, Base2
{}` — is **byte-for-byte identical** between v1 and v2. Only `Base2`'s own
declaration changes: it gains a virtual method. Under the Itanium C++ ABI,
`Base1` is `Derived`'s *primary* base (first polymorphic direct base — it
shares `Derived`'s primary vtable); every other polymorphic base contributes
its own *secondary* vtable group. In v1, `Base2` is not polymorphic, so it
contributes nothing. In v2, `Base2` is polymorphic, so `Derived` needs a new
secondary vtable group for it — **even though nothing about `Derived` itself
changed.**

## Why this case exists: a cross-type effect a per-type diff cannot see

A naive checker that diffs `Derived`'s own declaration against itself finds
**no difference** — same bases, same members, same everything. The only way
to know `Derived`'s dispatch surface changed is to also know that `Base2`,
somewhere else entirely, became polymorphic. `secondary_vtable_group_changed`
is abicheck's DWARF-based (L1) reconstruction of exactly this: it recomputes
each class's *ordered list of secondary vtable groups* from the current
snapshot's base/vtable metadata on both sides, and reports when that list
changes for a class whose own base declaration list did not move (a moved
base is `base_class_position_changed`'s job, not this one).

This case also demonstrates the change's full blast radius: `Base2` gaining
a vtable pointer is a real, large structural event, so several other
findings co-occur (`type_size_changed`/`type_field_added`/`type_vtable_changed`
on `Base2` itself, `vtable_slot_count_changed`/`base_class_offset_changed`/
`vtable_thunk_set_changed` on `Derived`). None of those name the specific
fact `secondary_vtable_group_changed` does: which vtable group `Derived`
now needs to satisfy dispatch through a `Base2*` — information a
plugin/reflection framework that walks vtable groups by position needs
directly, and that a per-type diff of `Derived` alone would never surface.

## What abicheck detects

- **`secondary_vtable_group_changed`** — `Derived`'s secondary vtable groups
  went from `(none)` to `Base2`. **Evidence tier L1** — reconstructed from
  DWARF base/vtable metadata (`bases`, `virtual_bases`, `vtable` on
  `RecordType`); requires debug info on both sides (unlike cases 172/173,
  this is not visible on a stripped binary — the base's polymorphism has to
  be known, not just inferred from a symbol's size).

**Overall verdict: BREAKING**

## How to reproduce

```bash
g++ -shared -fPIC -g v1.cpp -o libv1.so
g++ -shared -fPIC -g v2.cpp -o libv2.so

python3 -m abicheck.cli dump libv1.so -o /tmp/v1.json
python3 -m abicheck.cli dump libv2.so -o /tmp/v2.json
python3 -m abicheck.cli compare /tmp/v1.json /tmp/v2.json
# → BREAKING: secondary_vtable_group_changed (Derived: (none) -> Base2)
#   (plus the type_size_changed/vtable_slot_count_changed/... companions
#   described above)
```

## Real Failure Demo

**Severity: BREAKING / SILENT LAYOUT MISMATCH**

```bash
g++ -shared -fPIC -g v1.cpp -o libv1.so
g++ -g app.cpp -I. -L. -lv1 -Wl,-rpath,. -o app
./app
# app compiled with sizeof(Derived) = 8
# loaded library reports sizeof(Derived) = 8
# layouts agree

g++ -shared -fPIC -g v2.cpp -o libv2.so
cp libv2.so libv1.so
./app
# app compiled with sizeof(Derived) = 8
# loaded library reports sizeof(Derived) = 16
# MISMATCH: Base2 gained a vtable pointer underneath an unchanged Derived
# declaration -- Derived's own diff is a no-op, yet its layout changed.
```

## Mitigation

- Adding a virtual method to a base class is a break for *every* class that
  derives from it, not just a local change to that base — audit derived
  classes, not just the base being edited.
- If a base might need virtual dispatch later, declare at least one virtual
  method (even a virtual destructor) from the start, so its polymorphism
  status is part of the class's original, versioned contract.

## References

- [Itanium C++ ABI: primary and secondary vtable groups](https://itanium-cxx-abi.github.io/cxx-abi/abi.html#vtable-general)
- Related cases:
  [case172_vtable_thunk_offset_changed](../case172_vtable_thunk_offset_changed/README.md),
  [case173_vtt_slot_count_changed](../case173_vtt_slot_count_changed/README.md),
  [case60_base_class_position_changed](../case60_base_class_position_changed/README.md)
