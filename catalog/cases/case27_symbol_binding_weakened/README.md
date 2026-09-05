# Case 27: Symbol Binding Weakened (GLOBAL → WEAK)

**Category:** ELF / Symbol Quality | **Verdict:** 🟢 COMPATIBLE

## Verdict and consumer impact

`foo` is exported as a `GLOBAL` symbol in v1 and as a `WEAK` symbol in v2
(applying `__attribute__((weak))`). A `WEAK` symbol is still present in
`.dynsym` and still resolved normally by the dynamic linker whenever no
other definition overrides it, so any binary linked against v1's `foo`
continues to find and call the same function against v2 — no
recompilation needed. The only behavioral difference is symbol
interposition: a `WEAK` definition can be silently overridden by a
`GLOBAL` definition from another loaded object, where a `GLOBAL` one
cannot.

## Old/new diff

| old/lib.c | new/lib.c |
|-----------|-----------|
| `int foo(void) { return 42; }` | `__attribute__((weak)) int foo(void) { return 42; }` |

## abicheck command

```bash
gcc -shared -fPIC -g old/lib.c -Iold -o libfoo_v1.so
gcc -shared -fPIC -g new/lib.c -Inew -o libfoo_v2.so
abicheck compare libfoo_v1.so libfoo_v2.so
```

## Expected abicheck finding

```text
Verdict: COMPATIBLE (exit 0)

Quality Issues:
- symbol_binding_changed: Symbol binding changed: foo (global -> weak)
```

## Minimum evidence

`min_evidence: L0` — ELF's `.dynsym` table records each symbol's binding
attribute (`STB_GLOBAL`/`STB_WEAK`) directly; the exported-symbol table
alone is enough, no debug info or headers needed.

## Why abicheck catches it

The dynamic symbol table is authoritative L0 evidence — abicheck reads
each exported symbol's ELF binding field from both `.so` files and
reports a `GLOBAL`→`WEAK` (or the reverse) transition as
`symbol_binding_changed`, a quality finding rather than a break, since
resolution still succeeds either way absent interposition.

## Runtime failure demonstration

No observable effect on existing binaries when there is no competing
definition to interpose — `foo()` returns the identical value both before
and after the swap. The only place this could matter is if another loaded
`.so` or the executable itself also defines a `GLOBAL foo`, which would
then silently override the `WEAK` one; this demo has no such override.

```bash
# Build old library + app
gcc -shared -fPIC -g old/lib.c -Iold -o libfoo.so
gcc -g app.c -Iold -L. -lfoo -Wl,-rpath,. -o app
./app
# → foo() = 42

# Swap in new library (no recompile)
gcc -shared -fPIC -g new/lib.c -Inew -o libfoo.so
./app
# → foo() = 42   (identical)

# The difference is visible only in the ELF symbol table:
readelf --syms libfoo_v1.so | grep " foo"
# → FUNC    GLOBAL DEFAULT   10 foo
readelf --syms libfoo_v2.so | grep " foo"
# → FUNC    WEAK   DEFAULT   10 foo
```

## Safe redesign

Weakening a symbol's binding is not itself dangerous, but changing it
unintentionally can silently open the door to interposition. Document the
binding as part of the API contract when a symbol is deliberately made
overridable (e.g. to let consumers supply their own implementation), and
keep it strong otherwise.

**Real-world example:** glibc marks many internal helper symbols `WEAK` so
that applications or other libraries can override them (e.g. custom
`malloc` implementations interposing on `malloc`-family symbols).

## Cross-tool comparison

```bash
abidw --out-file v1.xml libfoo_v1.so
abidw --out-file v2.xml libfoo_v2.so
abidiff v1.xml v2.xml
```

## References

- [ELF symbol bindings](https://refspecs.linuxfoundation.org/elf/gabi4+/ch4.symtab.html)
- [libabigail `abidiff` manual](https://sourceware.org/libabigail/manual/abidiff.html)
