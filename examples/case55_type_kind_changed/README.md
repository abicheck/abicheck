# Case 55: Type Kind Changed (struct → union)

**Category:** Type Layout | **Verdict:** BREAKING

## What this case is about

v1 defines `Data` as a `struct` with two fields `x` and `y` laid out
sequentially (sizeof = 8). v2 changes `Data` to a `union` where `x` and `y`
overlap at offset 0 (sizeof = 4).

This is a **fundamental ABI break**: the memory layout, size, and semantics
all change when a struct becomes a union.

## What breaks at binary level

- **sizeof changes**: `sizeof(Data)` shrinks from 8 to 4. Consumers that
  allocate `Data` on the stack or in arrays use the old size.
- **Field offsets change**: `y` moves from offset 4 to offset 0 (overlapping `x`).
- **Semantic break**: Writing to `y` no longer preserves `x` — they share storage.

## What abicheck detects

- **`TYPE_KIND_CHANGED`**: abicheck detects that `Data` changed from struct to union
  via DWARF `DW_TAG_structure_type` → `DW_TAG_union_type`.

**Overall verdict: BREAKING**

## How to reproduce

```bash
gcc -shared -fPIC -g bad.c  -include bad.h  -o libbad.so
gcc -shared -fPIC -g good.c -include good.h -o libgood.so

# Verify size difference
pahole libbad.so  -C Data  # → struct, size 8
pahole libgood.so -C Data  # → union, size 4

python3 -m abicheck.cli dump libbad.so  -o /tmp/v1.json
python3 -m abicheck.cli dump libgood.so -o /tmp/v2.json
python3 -m abicheck.cli compare /tmp/v1.json /tmp/v2.json
# → BREAKING: TYPE_KIND_CHANGED
```

## References

- [DWARF structure vs union tags](https://dwarfstd.org/doc/DWARF5.pdf)

## Real Failure Demo

**Severity: BREAKING / WRONG RESULT**

```bash
cmake -S examples -B /tmp/abicheck-examples-build -DCMAKE_BUILD_TYPE=Debug
cmake --build /tmp/abicheck-examples-build --target case56_type_kind_changed_app case56_type_kind_changed_v2

tmp=$(mktemp -d)
cp /tmp/abicheck-examples-build/case56_type_kind_changed/app_v1 "$tmp/"
cp /tmp/abicheck-examples-build/case56_type_kind_changed/libv2.so "$tmp/libv1.so"
(cd "$tmp" && LD_LIBRARY_PATH=. ./app_v1)
# sum = 10 / WRONG RESULT: type kind changed (struct -> union)
```
