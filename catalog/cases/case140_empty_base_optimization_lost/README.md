# Case 140: Empty Base Optimization Lost (base subobject moved)

**Category:** C++ Layout | **Verdict:** 🔴 BREAKING

## Verdict and consumer impact

Any binary that upcasts a `Widget*` to its `Payload` base — or otherwise
relies on `Widget`'s layout — is broken without recompilation. The upcast
offset is baked in at compile time: under v1 it's 0, under v2 it's 8. A
prebuilt caller keeps using the old (now wrong) offset and silently reads
the wrong bytes; `sizeof(Widget)` also grows, so every stack/heap
allocation, array, and embedding of `Widget` is mis-sized too.

## Old/new diff

```cpp
// v1                                    // v2
struct Tag {};                           struct Tag { long state; };  // <- gained a member
struct Payload { long value; };          struct Payload { long value; };
struct Widget : Tag, Payload {           struct Widget : Tag, Payload {
    long extra;                              long extra;
};                                       };
```

The only source edit is that `Tag` gained one data member. `Tag` was an
**empty class**, so under the Itanium C++ ABI the *Empty Base Optimization*
(EBO) folded it to offset 0 at zero cost, and the `Payload` base began at
offset 0 too. Once `Tag` has a member it is no longer empty, EBO no longer
applies, and the `Payload` base subobject **moves from offset 0 to offset
8**:

```
Widget (v1):  [Payload::value @0][extra @8]                  sizeof = 16
Widget (v2):  [Tag::state @0][Payload::value @8][extra @16]   sizeof = 24
```

## abicheck command

```bash
g++ -shared -fPIC -g v1.cpp -o libfoo_v1.so
g++ -shared -fPIC -g v2.cpp -o libfoo_v2.so
abicheck compare libfoo_v1.so libfoo_v2.so
```

## Expected abicheck finding

```text
Verdict: BREAKING (exit 4)

- type_size_changed: Size changed: Tag (8 -> 64 bits)
- type_size_changed: Size changed: Widget (128 -> 192 bits)
  > Affected symbols: make_widget, widget_payload_value
- type_field_offset_changed: Field offset changed: Widget::extra (64 -> 128 bits)
  > Old code reads/writes fields at stale offsets; silent data corruption.
- base_class_offset_changed: Base class 'Payload' moved within 'Widget'
  (0 -> 64 bits). The this-pointer adjustment for that base and the offset
  of every field after it shift; existing binaries read the wrong
  addresses.

Additions:
- type_field_added_compatible: Field added: Tag::state
```

## Minimum evidence

`min_evidence: L1` — DWARF's `DW_TAG_inheritance` entries record each base
class's computed offset within the derived type for both versions, so `-g`
alone (no public headers) is enough; with headers supplied the same fact is
also visible via the castxml record layout (L2).

## Why abicheck catches it

abicheck reads each base's `DW_TAG_inheritance` offset (or, when headers
are available, the castxml/clang AST record layout) from DWARF and compares
`base_offsets["Payload"]` directly between the two snapshots —
`base_class_offset_changed` fires on the computed offset shift itself, not
on the declaration order of the bases changing.

## Runtime failure demonstration

**Severity: BREAKING / OBJECT CORRUPTION**

**Scenario:** app upcasts `Widget*` to `Payload*` and reads `Payload::value`
through that pointer — the compile-time offset is baked into the upcast.

```bash
# Build old library + app
g++ -shared -fPIC -g v1.cpp -o libwidget.so
g++ -g app.cpp -I. -L. -lwidget -Wl,-rpath,. -o app
./app
# → Payload::value via base cast = 42 (expected 42)
# → Payload::value via accessor  = 42 (expected 42)

# Swap in new library (no recompile)
g++ -shared -fPIC -g v2.cpp -o libwidget.so
./app
# → Payload::value via base cast = 0 (expected 42)   ✗  CORRUPTION
# → Payload::value via accessor  = 42 (expected 42)  ✓  (library-side, always correct)
# → CORRUPTION: base-subobject offset shifted (EBO lost)
```

**Why BREAKING:** the app's upcast uses the v1 offset (0) baked in at
compile time; under v2 that offset lands on `Tag::state` instead of
`Payload::value`, while the library's own accessor (which recomputes the
offset at v2's compile time) still returns the right answer — a textbook
silent-corruption signature.

## Safe redesign

Do not expose concrete classes with public base classes across an ABI
boundary; hide layout behind an opaque handle / pimpl. Treat "adding a
field to an empty base/tag type" as an ABI-review event — EBO makes it a
layout change, not a local edit.

**Real-world example:** libc++'s `unique_ptr`/`optional` and similar
empty-base-heavy templates are a known EBO trap — adding any state to a
policy/tag type used as a base silently grows and reshuffles every
instantiation.

## Cross-tool comparison

```bash
abidw --out-file v1.xml libfoo_v1.so
abidw --out-file v2.xml libfoo_v2.so
abidiff v1.xml v2.xml
```

`abidiff` also models base-class layout via its DWARF DIE walker and would
flag the size and offset changes; it doesn't have a dedicated "EBO lost"
label, so the underlying cause (an empty base gaining a member) is less
immediately named than abicheck's `base_class_offset_changed`.

## References

- [Itanium C++ ABI: empty bases & class layout](https://itanium-cxx-abi.github.io/cxx-abi/abi.html#class-types)
- [C++ standard-layout & EBO rules](https://en.cppreference.com/w/cpp/language/ebo)
- Related cases:
  [case60_base_class_position_changed](../case60_base_class_position_changed/README.md),
  [case37_base_class](../case37_base_class/README.md),
  [case142_vtable_slot_count_binary_only](../case142_vtable_slot_count_binary_only/README.md)
