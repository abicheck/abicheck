# Case 55: Type Kind Changed (struct → union)

**Category:** Type Layout | **Verdict:** 🔴 BREAKING

## Verdict and consumer impact

v1 defines `Data` as a `struct` with two fields `x` and `y` laid out
sequentially (`sizeof` = 8). v2 changes `Data` to a `union`, where `x` and
`y` overlap at offset 0 (`sizeof` = 4). This is a fundamental ABI break:
memory layout, size, and field semantics all change at once. Any consumer
that allocates `Data` on the stack, embeds it in another struct, or puts it
in an array uses the old 8-byte size and reads `y` from the old offset 4 —
which now aliases `x`.

## Old/new diff

| bad.h (v1) | good.h (v2) |
|------------|-------------|
| `typedef struct { int x; int y; } Data;` (8 bytes, sequential) | `typedef union { int x; int y; } Data;` (4 bytes, overlapping) |

## abicheck command

```bash
gcc -shared -fPIC -g bad.c  -include bad.h  -o libfoo_v1.so
gcc -shared -fPIC -g good.c -include good.h -o libfoo_v2.so
abicheck compare libfoo_v1.so libfoo_v2.so
```

## Expected abicheck finding

```text
Verdict: BREAKING (exit 4)

- type_size_changed: Size changed: Data (64 -> 32 bits)
  > Old code allocates or copies the type with the old size;
    heap/stack corruption, out-of-bounds access.
  Affected symbols: data_init, data_sum
- type_field_offset_changed: Field offset changed: Data::y (32 -> 0 bits)
  > Old code reads/writes fields at stale offsets; silent data corruption.
  Affected symbols: data_init, data_sum
- type_kind_changed: Aggregate kind changed: Data (struct -> union)
```

## Minimum evidence

`min_evidence: L1` — DWARF distinguishes `DW_TAG_structure_type` from
`DW_TAG_union_type` and carries each field's offset, so `-g` debug info
alone (no public headers) is enough to detect the kind change, the size
change, and the offset change together.

## Why abicheck catches it

abicheck's DWARF-based type differ reads each type's DWARF tag directly;
`Data` going from `DW_TAG_structure_type` to `DW_TAG_union_type` between the
two snapshots is an unambiguous kind change, and the differ additionally
reports the consequential size and field-offset changes that follow from a
struct's sequential layout becoming a union's overlapping one.

## Runtime failure demonstration

**Severity: BREAKING**

**Scenario:** app initializes `Data{x:10, y:20}` against v1's struct layout,
then calls `data_sum()` from v2, which now reads `Data` as a union.

```bash
# Build old library + app
gcc -shared -fPIC -g bad.c -include bad.h -o libfoo.so
gcc -g app.c -L. -lfoo -Wl,-rpath,. -o app
./app
# → sum = 30

# Swap in new library (no recompile)
gcc -shared -fPIC -g good.c -include good.h -o libfoo.so
./app
# → sum = 10
# → WRONG RESULT: type kind changed (struct -> union)
echo "exit: $?"   # → 1
```

**Why BREAKING:** in v1, `x=10` and `y=20` occupy separate 4-byte slots and
`data_sum` returns `x + y = 30`. In v2's union layout, `data_init` only
writes `x` (the two fields alias the same 4 bytes), so `data_sum` returns
`x = 10` instead — silently wrong output rather than a crash, which is the
more dangerous failure mode.

## Safe redesign

Never change an already-published aggregate's kind (struct ↔ union). If the
semantics genuinely need to change, introduce a new type name (`DataV2`) and
a matching new set of functions, so the old `Data`/functions keep working
for existing consumers.

**Real-world example:** kernel and driver ABIs (Linux `struct sockaddr` vs.
its per-family variants) are careful to keep struct/union kind stable across
versions specifically because this class of change is undetectable from
function signatures alone and produces silent data corruption rather than a
load-time failure.

## Cross-tool comparison

```bash
abidw --out-file v1.xml libfoo_v1.so
abidw --out-file v2.xml libfoo_v2.so
abidiff v1.xml v2.xml
```

## References

- [DWARF structure vs union tags](https://dwarfstd.org/doc/DWARF5.pdf)
