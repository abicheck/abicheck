# Case 67: TLS Variable Size Changed

**Category:** Variable ABI | **Verdict:** 🔴 BREAKING

## Verdict and consumer impact

`ErrorCtx` is a thread-local (`__thread`) struct. v2 inserts a `severity`
field between `code` and `message`, growing the struct from 68 to 72 bytes
and shifting `message` from offset 4 to offset 8. Any consumer compiled
against v1 that reads `tls_error.message` directly still reads offset 4 —
which now holds the `severity` integer, not the string — silently
corrupting output with no crash. Recompilation is mandatory.

## Old/new diff

| v1.h | v2.h |
|------|------|
| `int code;` *(offset 0)* | `int code;` *(offset 0, unchanged)* |
| `char message[64];` *(offset 4)* | `int severity;` *(offset 4, NEW)* |
| *(sizeof = 68)* | `char message[64];` *(offset 8, shifted)* — *(sizeof = 72)* |

## abicheck command

```bash
gcc -shared -fPIC -g v1.c -o libfoo_v1.so
gcc -shared -fPIC -g v2.c -o libfoo_v2.so
abicheck compare libfoo_v1.so libfoo_v2.so
```

## Expected abicheck finding

```text
Verdict: BREAKING (exit 4)

- type_size_changed: Size changed: ErrorCtx (544 -> 576 bits)
  > Old code allocates or copies the type with the old size; heap/stack
    corruption, out-of-bounds access.
- struct_field_offset_changed: Field offset changed: ErrorCtx::message (+4 -> +8)
  > Field moved to different offset; old code accesses wrong memory.
- symbol_size_changed: Symbol size changed: tls_error (68 -> 72 bytes)
  > ELF symbol size changed; copy relocations or memcpy-based consumers get
    truncated/oversized data.
- tls_var_size_changed: TLS variable size changed: tls_error (68 -> 72 bytes)
  > Exported thread-local (TLS) variable size changed; consumers using copy
    relocations or direct TLS access will read/write out of bounds.

Additions:
- type_field_added_compatible: Field added: ErrorCtx::severity
```

## Minimum evidence

`min_evidence: L1` — DWARF debug info records the TLS variable's `.dynsym`
size plus the struct's member offsets for both versions; abicheck compares
them directly from debug info (`-g`), no public headers required.

## Why abicheck catches it

abicheck reads the TLS symbol's ELF size (`STT_TLS` entries in `.dynsym`)
and DWARF's `DW_TAG_structure_type` member-offset list for `ErrorCtx` in
both binaries; the offset shift for `message` and the size growth are both
visible directly in debug info, no header parsing needed.

## Runtime failure demonstration

**Severity: CRITICAL**

**Scenario:** compile app against v1, swap in v2 `.so` without recompile.

```bash
# Build old library + app
gcc -shared -fPIC -g v1.c -o liblogger.so
gcc -g app.c -L. -llogger -Wl,-rpath,. -o app
./app
# → error code = 404 (expected 404)
# → message = "not found" (expected "not found")

# Swap in new library (no recompile)
gcc -shared -fPIC -g v2.c -o liblogger.so
./app
# → error code = 404 (expected 404)
# → message = "" (expected "not found")
# → CORRUPTION: TLS struct layout changed — app reads v1 offset but library
#   wrote v2 layout!
```

**Why CRITICAL:** the app reads `tls_error.message` at v1's offset 4, but v2
placed the `severity` integer there instead; the real message, now at offset
8, is never read. No crash occurs — just silently wrong data, which is far
harder to diagnose than a hard failure.

## Safe redesign

Don't export TLS structs directly — provide accessor functions
(`logger_get_message()`) instead, and treat any exported TLS variable's
layout as frozen ABI (append-only, never insert fields).

**Real-world example:** glibc's `__thread int errno` has been frozen at 4
bytes since glibc 2.0 for exactly this reason. OpenSSL's per-thread error
queue used to be a public TLS struct; OpenSSL 3.0 moved to an opaque handle
to avoid this class of break when the context needed to grow.

## Cross-tool comparison

```bash
abidw --out-file v1.xml libfoo_v1.so
abidw --out-file v2.xml libfoo_v2.so
abidiff v1.xml v2.xml
```

## References

- [ELF Handling For Thread-Local Storage](https://www.akkadia.org/drepper/tls.pdf)
- [System V ABI — Thread-Local Storage](https://refspecs.linuxfoundation.org/elf/x86_64-abi-0.99.pdf)
