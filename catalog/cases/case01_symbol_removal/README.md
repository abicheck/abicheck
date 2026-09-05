# Case 01: Symbol Removal

**Category:** Symbol API | **Verdict:** 🔴 BREAKING

## Verdict and consumer impact

Any downstream binary that dynamically links against `helper()` will fail at
runtime with `undefined symbol` after upgrading to v2. Even if *you* no
longer use `helper()`, removing it from the public `.so` is an ABI contract
violation — recompilation cannot fix a binary that's already deployed.

## Old/new diff

| v1.c | v2.c |
|------|------|
| `int compute(int x) { return x * 2; }` | `int compute(int x) { return x * 2; }` |
| `int helper(int x)  { return x + 1; }` | *(removed)* |

## abicheck command

```bash
gcc -shared -fPIC -g v1.c -o libfoo_v1.so
gcc -shared -fPIC -g v2.c -o libfoo_v2.so
abicheck compare libfoo_v1.so libfoo_v2.so
```

## Expected abicheck finding

```text
Verdict: BREAKING (exit 4)

- func_removed: Public function removed: helper
  > Old binaries call a symbol that no longer exists; dynamic linker
    will refuse to load or crash at call site.
```

## Minimum evidence

`min_evidence: L0` — the exported-symbol table alone is enough: `helper` is
present in v1's `.dynsym` and absent from v2's. No debug info or headers
needed; `-g` above is only there so the *Runtime failure demonstration*
below can build a matching app.

## Why abicheck catches it

The dynamic symbol table is authoritative L0 evidence — abicheck diffs the
exported-symbol sets directly, no debug info or headers required.

## Runtime failure demonstration

**Severity: CRITICAL**

**Scenario:** compile app against v1, swap in v2 `.so` without recompile.

```bash
# Build old library + app
gcc -shared -fPIC -g v1.c -o libfoo.so
gcc -g app.c -L. -lfoo -Wl,-rpath,. -o app
./app
# → compute(5) = 10
# → helper(5)  = 6

# Swap in new library (no recompile)
gcc -shared -fPIC -g v2.c -o libfoo.so
./app
# → ./app: symbol lookup error: ./app: undefined symbol: helper
```

**Why CRITICAL:** `helper` is removed from the dynamic symbol table in v2; the runtime
linker cannot resolve the symbol and the process is killed immediately on startup.

## Safe redesign

Never remove a public symbol in a minor/patch release. Deprecate with
`__attribute__((deprecated("use compute() instead")))` and only remove on the next
**SONAME bump** (major version).

**Real-world example:** common in C libraries during "API cleanup"
refactors — OpenSSL 1.1.0 removed several low-level functions that were
technically public, forcing all downstream packages to patch at once.

## Cross-tool comparison

`abidiff` also catches this (it's a pure symbol removal, the case every ABI
diff tool is built around):

```bash
abidw --out-file v1.xml libfoo_v1.so
abidw --out-file v2.xml libfoo_v2.so
abidiff v1.xml v2.xml
echo "exit: $?"   # → 12 (= 4 | 8: ABI change detected + breaking change)
```

## References

- [ELF symbol table + dynamic linking behavior](https://refspecs.linuxfoundation.org/elf/gabi4+/ch4.symtab.html)
- [libabigail `abidiff` manual](https://sourceware.org/libabigail/manual/abidiff.html)
