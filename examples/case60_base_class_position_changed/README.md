# Case 60: Base Class Position Changed (Multiple Inheritance Reorder)

**Category:** C++ Layout | **Verdict:** BREAKING

## What this case is about

v1: `struct Widget : public Drawable, public Clickable`
v2: `struct Widget : public Clickable, public Drawable`

The base class declaration order is swapped. In C++ multiple inheritance,
base subobjects are laid out in declaration order, so swapping them changes
every offset in the class.

## What breaks at binary level

- **Subobject offsets swap**: Drawable subobject was at offset 0, now Clickable
  is at offset 0 and Drawable is further into the object.
- **Vtable pointer adjustment breaks**: Casting `Widget*` to `Drawable*` or
  `Clickable*` uses compile-time offsets that are now wrong.
- **Cross-cast corrupts**: `static_cast<Clickable*>(widget)` compiled against
  v1 adjusts by the wrong offset when v2 is loaded.

## What ABICheck detects

- **`BASE_CLASS_POSITION_CHANGED`**: The base class list order changed,
  detected via DWARF `DW_TAG_inheritance` entries with different offsets.

**Overall verdict: BREAKING**

## How to reproduce

```bash
g++ -shared -fPIC -g v1.cpp -o libv1.so
g++ -shared -fPIC -g v2.cpp -o libv2.so

python3 -m abicheck.cli dump libv1.so -o /tmp/v1.json
python3 -m abicheck.cli dump libv2.so -o /tmp/v2.json
python3 -m abicheck.cli compare /tmp/v1.json /tmp/v2.json
# → BREAKING: BASE_CLASS_POSITION_CHANGED
```

## Real-world examples

- GUI frameworks with multiple interface inheritance (Qt, GTK+) must freeze
  base class order once published.
- COM-style interfaces on Linux also depend on base class order for vtable layout.

## References

- [Itanium C++ ABI: class layout](https://itanium-cxx-abi.github.io/cxx-abi/abi.html#class-types)

## Real Failure Demo

**Severity: BREAKING / OBJECT CORRUPTION**

```bash
cmake -S examples -B /tmp/abicheck-examples-build -DCMAKE_BUILD_TYPE=Debug
cmake --build /tmp/abicheck-examples-build --target case61_base_class_position_changed_app case61_base_class_position_changed_v2

tmp=$(mktemp -d)
cp /tmp/abicheck-examples-build/case61_base_class_position_changed/app_v1 "$tmp/"
cp /tmp/abicheck-examples-build/case61_base_class_position_changed/libv2.so "$tmp/libv1.so"
(cd "$tmp" && LD_LIBRARY_PATH=. ./app_v1)
# id = <garbage>; CORRUPTION: base-class order changed, subobject offsets mismatch
```
