# Case 127: Exported Data Object Size Changed

**Category:** Symbol / Data Layout | **Verdict:** 🔴 BREAKING

## Verdict and consumer impact

The library exports a global data object, `config_table`. In v1 it is
`int[16]` (64 bytes); in v2 it grows to `int[32]` (128 bytes) — the exported
symbol's `st_size` changes. When an executable references an exported data
object, the static linker emits a copy relocation sized for the *old*
definition: the executable reserves 64 bytes in its own BSS, and the dynamic
loader copies the library's initial value into that fixed-size slot at
startup. After upgrading to v2, any library code that indexes
`config_table[16..31]` reads or writes past the consumer's 64-byte copy —
silent out-of-bounds memory access, without recompilation.

## Old/new diff

| v1.h | v2.h |
|------|------|
| `#define CONFIG_SLOTS 16` | `#define CONFIG_SLOTS 32` |
| `extern int config_table[CONFIG_SLOTS];` (64 bytes) | `extern int config_table[CONFIG_SLOTS];` (128 bytes) |

## abicheck command

```bash
gcc -shared -fPIC -g v1.c -o libcfg_v1.so
gcc -shared -fPIC -g v2.c -o libcfg_v2.so
abicheck compare libcfg_v1.so libcfg_v2.so
```

## Expected abicheck finding

```text
Verdict: BREAKING (exit 4)

- symbol_size_changed: Symbol size changed: config_table (64 -> 128 bytes)
  > ELF symbol size changed; copy relocations or memcpy-based consumers
    get truncated/oversized data.
```

## Minimum evidence

`min_evidence: L0` — the object's size lives directly in the ELF symbol
table's `st_size` field; no debug info or headers are required to detect it.

## Why abicheck catches it

abicheck diffs the exported-symbol tables of both binaries directly,
including each data symbol's `st_size` — the same field the static linker
itself reads to size a copy relocation. A changed size is reported
regardless of whether DWARF or headers are available.

## Runtime failure demonstration

**Severity: CRITICAL (for a consumer carrying a copy relocation)**

**Scenario:** the consumer is a non-PIE executable (`-fno-pie -no-pie`), so
GCC and Clang alike resolve its reference to `config_table` through an
`R_X86_64_COPY` relocation: the executable reserves its own 64-byte copy and
every reference — the library's included — binds to it. v2 grows the object
to 128 bytes; its `config_reset()` writes all 32 slots.

```bash
gcc -shared -fPIC -g v1.c -o libv1.so
gcc -g -fno-pie -no-pie app.c -I. -L. -lv1 -Wl,-rpath,'$ORIGIN' -o app
readelf -r app | grep COPY      # R_X86_64_COPY config_table
./app            # config_table[15] = 115                          exit 0
gcc -shared -fPIC -g v2.c -o libv1.so     # swap in v2, no recompile
./app            # (loader: "Symbol `config_table' has different size ...")
                 # CORRUPTION: library wrote past the executable's copy, exit 1
```

The witness snapshots the 64 bytes that follow the executable's copy (other
objects or padding the executable owns) before calling `config_reset()` and
compares afterwards, so it asserts the corruption rather than printing the
loader's warning.

**The hazard is conditional on the consumer.** A PIE consumer built by Clang
uses `R_X86_64_GLOB_DAT` instead, binds to the library's own 128-byte object
and is not corrupted (GCC's PIE default on this target still emits a copy
relocation). The finding stays `BREAKING` because a copy-relocated consumer
is the common case for exported data objects and is not visible from the
library; the witness pins that condition explicitly.

## Safe redesign

Do not change the size of an exported data object within a SONAME; bump the
SONAME (major version) if the layout must change. Better: never export
mutable global arrays at all — expose accessor functions
(`config_get`/`config_set`) and keep the storage private, so its size is an
implementation detail no consumer copy-relocates.

**Real-world example:** this is the exact class of regression the glibc
testsuite guards with its static-variable-size checks, and that Apple's
dynamic-library guidelines classify as a *major* change ("changing a
symbol's size").

## Cross-tool comparison

`abidiff` also reads `st_size` from the ELF symbol table the same way
abicheck does, so it would catch this pure L0 size change too — this is the
kind of break every ABI-diffing tool is built to catch, not one that
distinguishes them.
