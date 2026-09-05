# Case 54: Used Reserved Field

**Category:** Quality | **Verdict:** 🟢 COMPATIBLE

## Verdict and consumer impact

v1's `Config` struct carries `__reserved1`/`__reserved2` placeholder fields.
v2 renames them to `priority`/`max_retries` at the **same offsets, same
types** — the struct size and layout are byte-identical. Existing binaries
that allocate, copy, or read `Config` keep working unmodified; this is the
textbook-correct way to evolve a struct (reserve padding fields upfront,
then activate them without an ABI break).

## Old/new diff

| old/lib.h | new/lib.h |
|-----------|-----------|
| `int __reserved1;` | `int priority;       /* was __reserved1 */` |
| `int __reserved2;` | `int max_retries;    /* was __reserved2 */` |

## abicheck command

```bash
gcc -shared -fPIC -g -include old/lib.h old/lib.c -o libfoo_v1.so
gcc -shared -fPIC -g -include new/lib.h new/lib.c -o libfoo_v2.so
abicheck compare libfoo_v1.so libfoo_v2.so
```

## Expected abicheck finding

```text
Verdict: COMPATIBLE (exit 0)

- used_reserved_field: Reserved field put into use: Config::__reserved1 -> priority
- used_reserved_field: Reserved field put into use: Config::__reserved2 -> max_retries
```

## Minimum evidence

`min_evidence: L1` — DWARF's `DW_TAG_structure_type` member list (name +
offset) for both versions is enough for the dedicated reserved-field
detector to match `__reserved1`/`__reserved2` renamed to new names at
unchanged offsets; no public headers required. (castxml is the documented
default AST backend for header-level evidence; clang, via `--ast-frontend
clang`, is a supported alternative when castxml isn't available — neither is
needed at this evidence tier.)

## Why abicheck catches it

abicheck's reserved-field detector recognizes naming patterns like
`__reserved`, `_reserved`, `__pad`, `_unused` and checks, from DWARF member
offsets, whether the renamed field lands at the exact same offset with the
same size as the reserved slot it replaces — if so, it's classified
COMPATIBLE rather than as a generic field rename/type change.

## Runtime failure demonstration

No observable effect on existing binaries — layout is unchanged.

```bash
# Build old library + app (app.c uses the v1 struct shape, with __reserved fields)
gcc -shared -fPIC -g -include old/lib.h old/lib.c -o libfoo.so
gcc -g app.c -L. -lfoo -Wl,-rpath,. -o app
./app
# → flags = 0

# Swap in new library (no recompile)
gcc -shared -fPIC -g -include new/lib.h new/lib.c -o libfoo.so
./app
# → flags = 0   ← identical
```

`priority`/`max_retries` simply occupy the bytes the old binary already
treats as reserved padding; nothing reads or writes past what v1 expects.

## Safe redesign

This case *is* the safe redesign pattern — reserve fields in the first
public release, then activate them later at the same offset instead of
growing the struct:

```c
/* v1: reserve slots for future use */
typedef struct {
    int version;
    int __reserved1;  /* -> becomes priority */
    int __reserved2;  /* -> becomes max_retries */
    int flags;
} Config;
```

**Real-world examples:** the Linux kernel's `struct stat` carries
`__unused`/padding fields for exactly this purpose; glibc's `pthread_attr_t`
reserves space for future extensions; Wayland protocol structs use
`__padding` fields the same way.

## References

- [Preserving ABI with reserved fields](https://www.akkadia.org/drepper/dsohowto.pdf)
