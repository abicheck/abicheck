# Case 57: Enum Underlying Size Changed

**Category:** Type Layout | **Verdict:** BREAKING

## What this case is about

v1 defines `Color` enum with values 0-2 (fits in `int`, 4 bytes on LP64).
v2 adds a sentinel value `0x100000000LL` that exceeds `INT_MAX`, forcing the
compiler to use a 64-bit underlying type.

This changes `sizeof(Color)` from 4 to 8, which breaks any struct containing
the enum.

## What breaks at binary level

- **Enum size doubles**: 4 → 8 bytes. All structs containing `Color` change layout.
- **Struct `Pixel` grows**: `alpha` moves from offset 4 to offset 8.
- **Function ABI changes**: `Color` is now passed in a 64-bit register/slot.
- **Arrays break**: `Color arr[N]` has different stride.

## What abicheck detects

- **`ENUM_UNDERLYING_SIZE_CHANGED`**: Detected via DWARF `DW_AT_byte_size` on
  the enumeration type.
- **`TYPE_SIZE_CHANGED`**: Structs containing the enum also change size.

**Overall verdict: BREAKING**

## How to reproduce

```bash
gcc -shared -fPIC -g bad.c  -include bad.h  -o libbad.so
gcc -shared -fPIC -g good.c -include good.h -o libgood.so

python3 -m abicheck.cli dump libbad.so  -o /tmp/v1.json
python3 -m abicheck.cli dump libgood.so -o /tmp/v2.json
python3 -m abicheck.cli compare /tmp/v1.json /tmp/v2.json
# → BREAKING: ENUM_UNDERLYING_SIZE_CHANGED
```

## Real-world examples

- Adding a large sentinel to a public enum is a common mistake in C libraries.
- C++11's `enum class Color : uint64_t` makes the underlying type explicit,
  but changing it between releases still breaks ABI.

## References

- [C11 6.7.2.2: Enumeration specifiers](https://www.open-std.org/jtc1/sc22/wg14/www/docs/n1570.pdf)

## Real Failure Demo

**Severity: BREAKING / WRONG RESULT**

```bash
cmake -S examples -B /tmp/abicheck-examples-build -DCMAKE_BUILD_TYPE=Debug
cmake --build /tmp/abicheck-examples-build --target case58_enum_underlying_size_changed_app case58_enum_underlying_size_changed_v2

tmp=$(mktemp -d)
cp /tmp/abicheck-examples-build/case58_enum_underlying_size_changed/app_v1 "$tmp/"
cp /tmp/abicheck-examples-build/case58_enum_underlying_size_changed/libv2.so "$tmp/libv1.so"
(cd "$tmp" && LD_LIBRARY_PATH=. ./app_v1)
# color = 2; alpha = 0 / WRONG RESULT: enum underlying size/layout changed
```
