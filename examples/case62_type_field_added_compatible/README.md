# Case 62: Type Field Added (Compatible — Opaque Struct)

**Category:** Type Layout | **Verdict:** COMPATIBLE

## What this case is about

v1 defines `Session` as an opaque struct with `name` and `timeout` fields.
v2 adds a `priority` field at the end. Because callers only use `Session*`
(never allocate, embed, or sizeof the struct), the change is **ABI-compatible**.

This is the **correct design pattern** for extensible C APIs: opaque handles +
accessor functions allow adding fields without breaking existing consumers.

## Real Failure Demo

**Severity: COMPATIBLE - NO FAILURE EXPECTED**

The struct is opaque to callers, so v2 can grow the private allocation without changing caller layout.

```bash
cmake -S examples -B /tmp/abicheck-examples-build -DCMAKE_BUILD_TYPE=Debug
cmake --build /tmp/abicheck-examples-build --target case62_type_field_added_compatible_app case62_type_field_added_compatible_v2

tmp=$(mktemp -d)
cp /tmp/abicheck-examples-build/case62_type_field_added_compatible/app_v1 "$tmp/"
cp /tmp/abicheck-examples-build/case62_type_field_added_compatible/libv2.so "$tmp/libv1.so"
(cd "$tmp" && LD_LIBRARY_PATH=. ./app_v1)
# name = test / timeout = 30
```

## Why this is compatible

- **Callers never see the layout**: `Session` is forward-declared in the header.
  All allocation is done by `session_open()` inside the library.
- **Existing field offsets unchanged**: `name` and `timeout` are at the same
  offsets. Only a new field is appended.
- **Existing functions unchanged**: `session_get_name()` and `session_get_timeout()`
  work identically.

## Contrast with case07 (breaking)

Case 07 adds a field to a **non-opaque** struct that callers `sizeof` and
embed — that's breaking. This case demonstrates the safe pattern.

## What abicheck detects

- **`FUNC_ADDED`**: `session_get_priority()` is a new symbol.

`Session` is forward-declared only in the public header — its definition
never appears in header-based diffing at all, so no type-level finding
fires for it (not `TYPE_FIELD_ADDED`, not `TYPE_FIELD_ADDED_COMPATIBLE`).
That's the point of the pattern: the struct's layout isn't part of the
diffed public surface to begin with, so there's nothing to classify as
"added" or "compatible" — it's simply invisible to the checker, which is
exactly what makes it safe to change. (For an example where `TYPE_FIELD_ADDED_COMPATIBLE`
actually fires on a *visible* struct's appended field, see case94 — there
it's paired with a breaking `TYPE_SIZE_CHANGED` on the same struct, since
the catalog does not yet have a case isolating a clean, standalone
COMPATIBLE verdict driven by `TYPE_FIELD_ADDED_COMPATIBLE` alone.)

**Overall verdict: COMPATIBLE**

## How to reproduce

```bash
gcc -shared -fPIC -g bad.c  -include bad.h  -o libbad.so
gcc -shared -fPIC -g good.c -include good.h -o libgood.so

python3 -m abicheck.cli dump libbad.so  -o /tmp/v1.json
python3 -m abicheck.cli dump libgood.so -o /tmp/v2.json
python3 -m abicheck.cli compare /tmp/v1.json /tmp/v2.json
# → COMPATIBLE: FUNC_ADDED
```

## Design pattern

```c
/* PUBLIC HEADER — opaque pointer */
typedef struct Widget Widget;
Widget* widget_new(void);
void widget_free(Widget *w);

/* PRIVATE IMPLEMENTATION — can grow freely */
struct Widget {
    int x, y;
    int new_field;  /* ← safe to add */
};
```

## Real-world examples

- **OpenSSL**: All major types (`SSL`, `EVP_MD_CTX`, etc.) are opaque since 1.1.0
- **libcurl**: `CURL *` handle is fully opaque
- **SQLite**: `sqlite3 *` is opaque

## References

- [How to Write Shared Libraries — Opaque Types](https://www.akkadia.org/drepper/dsohowto.pdf)


Implementation note: v1's private `Session` definition keeps a reserved
`_reserved0` slot; v2 renames it to `priority` at the same offset and size.
This keeps `sizeof(Session)` identical between versions — deliberately, so
the case isolates *only* the opacity argument (the struct is invisible to
header-based diffing either way) rather than also depending on
`_filter_opaque_size_changes`' pointer-only-usage suppression of a growing
`sizeof`. Since `Session` is never in the public header, none of this is
visible to abicheck regardless — the private struct could have changed
size too and the verdict would be identical.
