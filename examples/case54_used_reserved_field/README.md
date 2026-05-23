# Case 54: Used Reserved Field

**Category:** Type Layout | **Verdict:** COMPATIBLE

## What this case is about

v1 has a struct `Config` with `__reserved1` and `__reserved2` placeholder fields.
v2 renames them to `priority` and `max_retries` at the same offsets with the same
types. The struct size and layout are unchanged.

This is the **correct way** to evolve a struct: reserve padding fields upfront,
then activate them in later versions without breaking ABI.

## What abicheck detects

- **`USED_RESERVED_FIELD`**: abicheck has a dedicated detector (`_diff_reserved_fields`)
  that recognizes patterns like `__reserved`, `_reserved`, `__pad`, `_unused` being
  renamed to meaningful names at the same offset. This is classified as COMPATIBLE.

**Overall verdict: COMPATIBLE** (layout unchanged, reserved slots activated as intended).

## How to reproduce

```bash
gcc -shared -fPIC -g bad.c  -include bad.h  -o libbad.so
gcc -shared -fPIC -g good.c -include good.h -o libgood.so

python3 -m abicheck.cli dump libbad.so  -o /tmp/v1.json
python3 -m abicheck.cli dump libgood.so -o /tmp/v2.json
python3 -m abicheck.cli compare /tmp/v1.json /tmp/v2.json
# → COMPATIBLE + USED_RESERVED_FIELD note
```

## Design pattern

```c
/* v1: reserve slots for future use */
typedef struct {
    int version;
    int __reserved1;  /* ← will become priority */
    int __reserved2;  /* ← will become max_retries */
    int flags;
} Config;

/* v2: activate reserved slots */
typedef struct {
    int version;
    int priority;       /* was __reserved1 */
    int max_retries;    /* was __reserved2 */
    int flags;
} Config;
```

## Real-world examples

- Linux kernel's `struct stat` uses `__unused` / `__st_ino` fields
- glibc's `pthread_attr_t` has reserved space for future extensions
- Wayland protocol structs use `__padding` fields

## References

- [Preserving ABI with reserved fields](https://www.akkadia.org/drepper/dsohowto.pdf)

## Real Failure Demo

**Severity: BAD PRACTICE / ABI RISK**

The small app still prints the old value, but v1 consumes a reserved field that v2 expects to own. The failure is latent: future library code may reinterpret that field and corrupt caller state.

```bash
cmake -S examples -B /tmp/abicheck-examples-build -DCMAKE_BUILD_TYPE=Debug
cmake --build /tmp/abicheck-examples-build --target case55_used_reserved_field_app case55_used_reserved_field_v2

tmp=$(mktemp -d)
cp /tmp/abicheck-examples-build/case55_used_reserved_field/app_v1 "$tmp/"
cp /tmp/abicheck-examples-build/case55_used_reserved_field/libv2.so "$tmp/libv1.so"
(cd "$tmp" && LD_LIBRARY_PATH=. ./app_v1)
# flags = 0
```
