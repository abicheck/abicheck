# Case 42: Type Alignment Changed (standalone alignas)

**Category:** Type Layout / DWARF | **Verdict:** BREAKING

## What this case is about

v1 defines `CacheBlock` with `aligned(8)`. v2 increases alignment to
`aligned(64)` for cache-line optimization. The fields and their types are
**identical** — only the alignment attribute changes.

This is a clean, isolated alignment change (unlike case41 which bundles
alignment with type removal and enum changes).

## What breaks at binary level

- **sizeof may change**: Compilers pad structs to a multiple of their alignment.
  `aligned(64)` makes `sizeof(CacheBlock)` = 64 (padded to alignment boundary).
  With `aligned(8)` it's also 64 here, but the ABI contract about where the
  struct can live in memory changes.
- **Stack allocation misaligned**: Old binaries allocate `CacheBlock` with 8-byte
  alignment. The v2 library may use SIMD instructions (e.g., `vmovdqa`) that
  require 64-byte alignment → SIGBUS / SIGSEGV.
- **Array stride changes**: If sizeof changes, `&blocks[i]` computes wrong offsets.
- **malloc alignment**: `malloc` typically returns 16-byte aligned memory.
  64-byte aligned structs need `aligned_alloc(64, sizeof(CacheBlock))`.

## What abicheck detects

- **`TYPE_ALIGNMENT_CHANGED`**: Detected via DWARF `DW_AT_alignment` or
  inferred from `DW_AT_byte_size` changes caused by alignment padding.

**Overall verdict: BREAKING**

## How to reproduce

```bash
gcc -shared -fPIC -g bad.c  -include bad.h  -o libbad.so
gcc -shared -fPIC -g good.c -include good.h -o libgood.so

python3 -m abicheck.cli dump libbad.so  -o /tmp/v1.json
python3 -m abicheck.cli dump libgood.so -o /tmp/v2.json
python3 -m abicheck.cli compare /tmp/v1.json /tmp/v2.json
# → BREAKING: TYPE_ALIGNMENT_CHANGED
```

## How to fix

Use opaque types so callers never allocate or embed the struct directly:

```c
/* header */
typedef struct CacheBlock CacheBlock;
CacheBlock* block_alloc(void);  /* library controls alignment */

/* implementation */
struct CacheBlock __attribute__((aligned(64))) { ... };
CacheBlock* block_alloc(void) {
    return aligned_alloc(64, sizeof(CacheBlock));
}
```

## Real-world examples

- DPDK packet buffers require cache-line alignment (64 bytes).
- Intel TBB / oneTBB uses `alignas(64)` for scalable allocator metadata.
- Changing alignment in a public struct after release broke ABI in several
  multimedia libraries (FFmpeg, GStreamer).

## References

- [C11 alignas / _Alignas](https://en.cppreference.com/w/c/language/_Alignas)
- [GCC __attribute__((aligned))](https://gcc.gnu.org/onlinedocs/gcc/Common-Type-Attributes.html)

## Real Failure Demo

**Severity: BAD PRACTICE / ABI BREAK**

```bash
cmake -S examples -B /tmp/abicheck-examples-build -DCMAKE_BUILD_TYPE=Debug
cmake --build /tmp/abicheck-examples-build --target case43_type_alignment_changed_app case43_type_alignment_changed_v2

tmp=$(mktemp -d)
cp /tmp/abicheck-examples-build/case43_type_alignment_changed/app_v1 "$tmp/"
cp /tmp/abicheck-examples-build/case43_type_alignment_changed/libv2.so "$tmp/libv1.so"
(cd "$tmp" && LD_LIBRARY_PATH=. ./app_v1)
# block_process = 0 (expected 4) / WRONG RESULT: alignment change caused array stride/layout mismatch
```
