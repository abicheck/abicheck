# Case 11: Global Variable Type Change

**Category:** Type Layout | **Verdict:** 🔴 BREAKING

## Verdict and consumer impact

Any binary that accesses `lib_version` as a 4-byte `int` (v1's type) now
reads only half of the 8-byte `long` v2 defines. On little-endian x86 the
low word happens to be correct for small values, masking the bug — until
`lib_version` exceeds `INT_MAX`, at which point old binaries read garbage.

## Old/new diff

| v1.c | v2.c |
|------|------|
| `int  lib_version = 1;` | `long lib_version = 5000000000L;` |

## abicheck command

```bash
gcc -shared -fPIC -g v1.c -o libfoo_v1.so
gcc -shared -fPIC -g v2.c -o libfoo_v2.so
abicheck compare libfoo_v1.so libfoo_v2.so
```

## Expected abicheck finding

```text
Verdict: BREAKING (exit 4)

- var_type_changed: Variable type changed: lib_version (int -> long int)
  > Old binaries read/write the variable with wrong size or layout; data
    corruption or segfault.
- symbol_size_changed: Symbol size changed: lib_version (4 -> 8 bytes)
  > ELF symbol size changed; copy relocations or memcpy-based consumers
    get truncated/oversized data.
```

## Minimum evidence

`min_evidence: L1` — DWARF's variable entry (`DW_AT_type` on `lib_version`)
records the type for both versions, and the ELF symbol table separately
records the size change; `-g` alone is enough to detect both, no public
headers required.

## Why abicheck catches it

DWARF records each global variable's type; abicheck diffs the two
versions' variable-type entries directly from debug info, and
cross-checks against the ELF symbol table's recorded size for the same
symbol — both signal the same underlying change independently.

## Runtime failure demonstration

**Severity: CRITICAL**

**Scenario:** app reads `lib_version` as `int` (v1's type); v2 declares it
`long 5000000000`.

```bash
# Build v1 + app
gcc -shared -fPIC -g v1.c -o libfoo.so
gcc -g app.c -I. -L. -lfoo -Wl,-rpath,. -o app
./app
# → lib_version = 1 (as int)

# Swap in v2 (no recompile)
gcc -shared -fPIC -g v2.c -o libfoo.so
./app
# → ./app: Symbol `lib_version' has different size in shared object, consider re-linking
# → lib_version = 705032704 (as int)
# → WRONG RESULT: global variable type/value contract changed
```

**Why CRITICAL:** the app accesses only the lower 4 bytes of a now-8-byte
symbol. The dynamic linker itself warns about the size mismatch, and on
little-endian x86 the read of the low 32 bits produces garbage for values
above `INT_MAX`.

## Safe redesign

Use a fixed-width type from the start (`int32_t`, `int64_t`, or
`uint32_t`). If a global's type must change, introduce a new symbol with a
new name and deprecate the old one.

**Real-world example:** the `errno` global in glibc is deliberately typed
as `int` and will never change; glibc uses `__thread int errno` internally
but the public type is ABI-frozen.

## Cross-tool comparison

```bash
abidw --out-file v1.xml libfoo_v1.so
abidw --out-file v2.xml libfoo_v2.so
abidiff v1.xml v2.xml
echo "exit: $?"   # → 4
```

> **Note on abidiff 2.4.0:** reports `size of symbol changed from 4 to 8`
> and `type of variable changed: int -> long int`, exit **4**.

## References

- [ELF symbols](https://refspecs.linuxfoundation.org/elf/gabi4+/ch4.symtab.html)
- [libabigail `abidiff` manual](https://sourceware.org/libabigail/manual/abidiff.html)
