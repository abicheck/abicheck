# Case 173: VTT Slot Count Changed (detected from a stripped binary)

**Category:** C++ Layout | **Verdict:** BREAKING

## What this case is about

```cpp
// v1                                   // v2
struct Base { ... };                    struct Base { ... };
struct Left : virtual Base { ... };     struct Left : virtual Base { ... };
struct Right : virtual Base { ... };    struct Right : virtual Base { ... };
                                         struct Mixin : virtual Base { ... };  // <- new
struct Diamond : Left, Right {};        struct Diamond : Left, Right, Mixin {};
```

`Diamond` is a classic virtual-inheritance diamond: `Left` and `Right` each
declare `Base` as a *virtual* base, so `Diamond` contains exactly one shared
`Base` subobject. Constructing that object correctly requires a **VTT**
("virtual table table" — `_ZTT7Diamond`): an array of *construction*
sub-vtables that gives each base class's own constructor a temporarily valid
view of the not-yet-finished object, before `Diamond`'s constructor finishes
wiring up the final virtual-base offsets.

v2 adds a third virtual-inheritance leg, `Mixin`. `Diamond`'s construction
now needs **one more** construction sub-vtable — the VTT grows.

## Why this case exists: a construction-time signal, not a dispatch one

Adding a virtual-inheritance leg is a big structural change, and it fires
several findings at once on a stripped binary:

- `vtable_slot_count_changed` (case142) — the main vtable grew (Mixin
  contributes a secondary group with its own method).
- `rtti_inheritance_changed` — the RTTI base-class shape grew.
- `vtable_thunk_offset_changed` (case172) — the destructor's thunk offsets
  shifted.
- **`vtt_slot_count_changed`** — the one this case exists to demonstrate.

The first three all describe **how a `Diamond*` is used after construction**
(dispatch, typeinfo, this-adjustment). `vtt_slot_count_changed` describes
something none of them do: **how a `Diamond` is built in the first place** —
it is the construction-time binary evidence that the virtual-inheritance
shape changed. The object-layout consequence of that same shape change (a
consumer's compiled-in `sizeof(Diamond)` disagreeing with the library's
actual size — see the Real Failure Demo below) comes from `Diamond` actually
growing once `Mixin` is added, not from the VTT itself; the VTT's size is the
signal that lets abicheck see the construction-scaffolding change even on a
stripped binary that carries none of the type information needed to compute
`sizeof(Diamond)` directly.

## What abicheck detects

- **`vtt_slot_count_changed`** — `_ZTT7Diamond`'s size grew from 56 to 80
  bytes (one more construction sub-vtable). **Evidence tier L0** — read from
  the `_ZTT` symbol's `st_size` alone; no DWARF, no headers.
- (companions, already demonstrated elsewhere): `vtable_slot_count_changed`,
  `rtti_inheritance_changed`, `vtable_thunk_offset_changed`.

**Overall verdict: BREAKING**

## How to reproduce (stripped, binary-only)

```bash
g++ -shared -fPIC -g v1.cpp -o libv1.so
g++ -shared -fPIC -g v2.cpp -o libv2.so
strip --strip-debug libv1.so libv2.so

python3 -m abicheck.cli dump libv1.so -o /tmp/v1.json
python3 -m abicheck.cli dump libv2.so -o /tmp/v2.json
python3 -m abicheck.cli compare /tmp/v1.json /tmp/v2.json
# → BREAKING: vtt_slot_count_changed (_ZTT7Diamond: 56 -> 80 bytes)
```

## Real Failure Demo

**Severity: BREAKING / SILENT LAYOUT MISMATCH**

```bash
g++ -shared -fPIC -g v1.cpp -o libv1.so
g++ -g app.cpp -I. -L. -lv1 -Wl,-rpath,. -o app
./app
# app compiled with sizeof(Diamond) = 16
# loaded library reports sizeof(Diamond) = 16
# layouts agree

g++ -shared -fPIC -g v2.cpp -o libv2.so
cp libv2.so libv1.so
./app
# app compiled with sizeof(Diamond) = 16
# loaded library reports sizeof(Diamond) = 24
# MISMATCH: the app's compile-time Diamond layout disagrees with the
# library's actual layout.
```

This demo is deterministic by construction — no crash required to observe
it: it directly compares the app's compile-time `sizeof(Diamond)` (baked in
from `v1.h`) against the value the *loaded* library actually uses. Any real
consumer that embeds, copies, or by-value-returns a `Diamond` — not just one
that calls a virtual method through it — inherits this same mismatch.

## Mitigation

- Treat adding a virtual base to a class in a published hierarchy as an ABI
  break, not a compatible addition — it changes both the object's runtime
  layout and its construction scaffolding.
- Prefer non-virtual composition, or introduce new capabilities via a
  separate, independently-constructed interface rather than widening an
  existing virtual-inheritance diamond.

## References

- [Itanium C++ ABI: construction vtables (VTT)](https://itanium-cxx-abi.github.io/cxx-abi/abi.html#vtable-ctor)
- Related cases:
  [case142_vtable_slot_count_binary_only](../case142_vtable_slot_count_binary_only/README.md),
  [case172_vtable_thunk_offset_changed](../case172_vtable_thunk_offset_changed/README.md),
  [case174_secondary_vtable_group_changed](../case174_secondary_vtable_group_changed/README.md)
