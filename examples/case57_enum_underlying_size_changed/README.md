# Case 57: Enum Underlying Size Changed

**Category:** Type Layout | **Verdict:** 🔴 BREAKING

## Verdict and consumer impact

v1's `Color` enum only holds values 0–2, so the compiler picks a 4-byte
(`int`) underlying type. v2 adds a sentinel `_COLOR_FORCE_64BIT =
0x100000000LL` that exceeds `INT_MAX`, forcing the underlying type to widen
to 8 bytes. Every struct embedding `Color` — here `Pixel` — changes size and
member offsets as a result. A binary compiled against v1 that reads `Pixel`
fields directly gets a stale offset and silently wrong data, not a crash.
Recompilation against v2 is mandatory.

## Old/new diff

| bad.h (v1) | good.h (v2) |
|------------|-------------|
| `Color` values 0–2 → underlying type `int` (4 bytes) | adds `_COLOR_FORCE_64BIT = 0x100000000LL` → underlying type widens to `long` (8 bytes) |
| `Pixel { Color color; int alpha; }` = 8 bytes, `alpha` at offset 4 | same fields, now 16 bytes, `alpha` at offset 8 |

## abicheck command

```bash
gcc -shared -fPIC -g -include bad.h  bad.c  -o libfoo_v1.so
gcc -shared -fPIC -g -include good.h good.c -o libfoo_v2.so
abicheck compare libfoo_v1.so libfoo_v2.so
```

## Expected abicheck finding

```text
Verdict: BREAKING (exit 4)

- type_size_changed: Size changed: Pixel (64 -> 128 bits)
  > Old code allocates or copies the type with the old size;
    heap/stack corruption, out-of-bounds access.
  Affected symbols: pixel_create, pixel_destroy, pixel_get_color
- type_field_offset_changed: Field offset changed: Pixel::alpha (32 -> 64 bits)
  > Old code reads/writes fields at stale offsets; silent data corruption.
- enum_underlying_size_changed: Enum underlying type size changed: Color (4 -> 8 bytes)
  > Enum underlying type changed (e.g. int->long); affects ABI of functions
    passing enums by value.

Additions:
- enum_member_added: Enum member added: Color::_COLOR_FORCE_64BIT
```

## Minimum evidence

`min_evidence: L1` — DWARF's enumeration-type record carries
`DW_AT_byte_size` for `Color` itself, and the containing `Pixel` struct's
`DW_AT_byte_size`/member offsets shift accordingly; no public headers
required to see the size change propagate.

## Why abicheck catches it

DWARF records each enum type's underlying byte size directly (independent
of which enumerator values exist); abicheck compares that size across
versions and also walks every struct that embeds the enum to report the
derived field-offset and size changes those structs inherit.

## Runtime failure demonstration

**Severity: CRITICAL**

**Scenario:** app compiled against v1's 8-byte `Pixel` (4-byte `Color` +
4-byte `alpha` at offset 4), library swapped for v2's 16-byte `Pixel`
(8-byte `Color` + `alpha` moved to offset 8) without recompiling.

```bash
# Build old library + app
gcc -shared -fPIC -g -include bad.h bad.c -o libfoo.so
gcc -g app.c -L. -lfoo -Wl,-rpath,. -o app
./app
# → color = 2
# → alpha = 255

# Swap in new library (no recompile)
gcc -shared -fPIC -g -include good.h good.c -o libfoo.so
./app
# → color = 2
# → alpha = 0
# → WRONG RESULT: enum underlying size/layout changed
```

**Why CRITICAL:** the app's own `Pixel` layout (compiled from `bad.h`)
still reads `alpha` at offset 4, but v2's `pixel_create` allocates a
16-byte `Pixel` and writes `alpha` at offset 8. Offset 4 in the new layout
falls inside the widened `Color` field's padding, so the app silently
reads zero instead of 255.

## Safe redesign

Never add an enumerator value that forces the underlying type to widen —
treat a public enum's value range as fixed once released. If a wider range
is genuinely needed, pin the underlying type explicitly from the start
(`enum Color : int` in C++, or a plain `int32_t` constant table in C) so a
future large value can't silently change ABI, and give the new range a
new type/symbol name instead.

**Real-world example:** adding a large sentinel or bitmask value to a
public C enum is a recurring mistake; C++11's `enum class Color :
uint64_t` makes the underlying type explicit at declaration time, but
changing that annotation between releases is exactly this same break.

## Cross-tool comparison

```bash
abidw --out-file v1.xml libfoo_v1.so
abidw --out-file v2.xml libfoo_v2.so
abidiff v1.xml v2.xml
```

## References

- [C11 6.7.2.2: Enumeration specifiers](https://www.open-std.org/jtc1/sc22/wg14/www/docs/n1570.pdf)
- [libabigail `abidiff` manual](https://sourceware.org/libabigail/manual/abidiff.html)
