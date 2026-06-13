# Case 140: Empty Base Optimization Lost (base subobject moved)

**Category:** C++ Layout | **Verdict:** BREAKING

## What this case is about

```cpp
// v1                                    // v2
struct Tag {};                           struct Tag { long state; };  // <- gained a member
struct Payload { long value; };          struct Payload { long value; };
struct Widget : Tag, Payload {           struct Widget : Tag, Payload {
    long extra;                              long extra;
};                                       };
```

The only source edit is that `Tag` gained one data member. `Tag` was an **empty
class**, so under the Itanium C++ ABI the *Empty Base Optimization* (EBO) folded
it to offset 0 at zero cost, and the `Payload` base began at offset 0 as well.
Once `Tag` has a member it is no longer empty, EBO no longer applies, and the
`Payload` base **subobject moves from offset 0 to offset 8**.

```
Widget (v1):  [Payload::value @0][extra @8]            sizeof = 16   (Tag folded @0)
Widget (v2):  [Tag::state @0][Payload::value @8][extra @16]  sizeof = 24
```

This is the subtle case the proposal calls out: a change that looks like a
trivial "I just added a field to a helper base" silently relocates an *unrelated*
base subobject inside every derived object.

## What breaks at the binary level

- **Base-subobject offset shifts.** `Payload` moved 0 → 8 bytes. Any caller that
  upcasts `Widget*` to `Payload*` adjusts the pointer by the *compile-time*
  offset (0 under v1) and now points 8 bytes too low.
- **`sizeof(Widget)` grew** (16 → 24), so stack/heap allocations, arrays, and
  embedding inside other types are all mis-sized.
- **Every member after the moved base shifts**, so direct field reads through an
  old binary land on the wrong bytes.

The bundled `app.cpp` upcasts `Widget*` to its `Payload` base and reads
`Payload::value`. Compiled against v1 it reads offset 0 (correct: 42); run it
against the v2 library and the same offset now lands on `Tag::state` (0).

## What abicheck detects

- **`base_class_offset_changed`** — the `Payload` base subobject moved within
  `Widget` (`base_offsets["Payload"]` 0 → 64 bits). This is the headline finding
  and comes from the fine-grained class-layout descriptor (`diff_layout.py`).
- **`type_size_changed`** — `sizeof(Widget)` 128 → 192 bits.
- **`type_field_offset_changed`** — `extra` moved within `Widget`.

`base_class_offset_changed` is recovered from DWARF `DW_TAG_inheritance` offsets
(evidence tier **L1**) or, when headers are supplied, from the castxml record
layout (L2). It does **not** require the *declaration order* of the bases to
change — only their computed offsets.

**Overall verdict: BREAKING**

## How to reproduce

```bash
g++ -shared -fPIC -g v1.cpp -o libv1.so
g++ -shared -fPIC -g v2.cpp -o libv2.so

python3 -m abicheck.cli dump libv1.so -o /tmp/v1.json
python3 -m abicheck.cli dump libv2.so -o /tmp/v2.json
python3 -m abicheck.cli compare /tmp/v1.json /tmp/v2.json
# → BREAKING: base_class_offset_changed (Payload 0 → 64 bits) + type_size_changed
```

## Real Failure Demo

**Severity: BREAKING / OBJECT CORRUPTION**

```bash
g++ -shared -fPIC -g v1.cpp -o libwidget.so
g++ -g app.cpp -I. -L. -lwidget -Wl,-rpath,. -o app
./app
# Payload::value via base cast = 42 (expected 42)   ✓

g++ -shared -fPIC -g v2.cpp -o libwidget.so          # drop in the "compatible-looking" v2
./app
# Payload::value via base cast = 0  (expected 42)    ✗  CORRUPTION (base moved 0 → 8)
```

## Mitigation

- Do not expose concrete classes with public base classes across an ABI
  boundary; hide layout behind an opaque handle / pimpl.
- Treat "adding a field to an empty base/tag type" as an ABI-review event — EBO
  makes it a layout change, not a local edit.

## References

- [Itanium C++ ABI: empty bases & class layout](https://itanium-cxx-abi.github.io/cxx-abi/abi.html#class-types)
- [C++ standard-layout & EBO rules](https://en.cppreference.com/w/cpp/language/ebo)
- Related cases:
  [case60_base_class_position_changed](../case60_base_class_position_changed/README.md),
  [case37_base_class](../case37_base_class/README.md),
  [case142_vtable_slot_count_binary_only](../case142_vtable_slot_count_binary_only/README.md)
