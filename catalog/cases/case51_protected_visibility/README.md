# Case 51: Protected Visibility (DEFAULT to PROTECTED)

**Category:** Risk | **Verdict:** 🟡 COMPATIBLE_WITH_RISK

## Verdict and consumer impact

Existing binaries that call `hook_point()` keep working unmodified — the
symbol is still exported and resolves normally for external callers. What
changes is **interposition**: with `STV_PROTECTED`, the library's own
internal calls to `hook_point()` always bind to its local definition. A
consumer that installs its own `hook_point()` (an `LD_PRELOAD` shim, a
profiler, a mock, or simply an executable that defines the symbol) silently
stops intercepting calls made *from within* the library — the old app below
sees `compute(5) = 501` with v1 and `11` with v2, both exiting 0.

Whether that is a break depends on whether interposition is part of the
library's promise, which the binary does not say. abicheck therefore reports
a **conditional risk** (`func_visibility_protected_changed`,
`COMPATIBLE_WITH_RISK`, exit 0) rather than an unconditional `COMPATIBLE`;
block it if the hook is a supported extension point.

## Old/new diff

| old/lib.c | new/lib.c |
|-----------|-----------|
| `int hook_point(int x) { return x * 2; }` (DEFAULT visibility) | `__attribute__((visibility("protected")))`<br>`int hook_point(int x) { return x * 2; }` |

## abicheck command

```bash
gcc -shared -fPIC -g old/lib.c -Iold -o libfoo_v1.so
gcc -shared -fPIC -g new/lib.c -Inew -o libfoo_v2.so
abicheck compare libfoo_v1.so libfoo_v2.so
```

## Expected abicheck finding

```text
Verdict: COMPATIBLE_WITH_RISK (exit 0)

- func_visibility_protected_changed: ELF symbol visibility changed: hook_point (default → protected)
- symbol_elf_visibility_changed: ELF visibility changed: hook_point (default -> protected)
```

## Minimum evidence

`min_evidence: L0` — ELF symbol visibility (`STV_DEFAULT` vs `STV_PROTECTED`)
is recorded directly in `.dynsym`; no debug info or headers needed to see the
change. (`-g` above is only there so DWARF-aware confidence is reported.)

## Why abicheck catches it

abicheck reads each symbol's `st_other` visibility byte from the ELF dynamic
symbol table and diffs it between versions — a pure L0 ELF fact, independent
of DWARF or headers.

## Runtime failure demonstration

**Severity: silent behavior change for consumers that interpose the hook.**

The app defines its own `hook_point(x) = 100 * x`. Because the library
references `hook_point`, the linker exports the executable's definition and
the dynamic loader searches the executable first — the same lookup order an
`LD_PRELOAD` shim uses. The consumer's contract is that the library's
`compute()` goes through the installed hook.

```bash
gcc -shared -fPIC -g -fsemantic-interposition old/lib.c -Iold -o libv1.so
gcc -g app.c -I. -L. -lv1 -Wl,-rpath,'$ORIGIN' -o app
./app            # compute(5) = 501 (the installed hook is used)   exit 0
gcc -shared -fPIC -g -fsemantic-interposition new/lib.c -Inew -o libv1.so
./app            # compute(5) = 11  INTERPOSITION LOST              exit 1
```

Both libraries are built with `-fsemantic-interposition`: it is GCC's
default, but Clang (13+) assumes no interposition at `-O1` and above and may
inline or directly call a default-visibility function from inside the
library — then even v1 would never call the installed hook, and the
fixture's premise ("v1 is interposable") would not hold under Clang Release
builds. The original smoke never installed a hook and so showed no change.

## Safe redesign

If interposition of an internal helper is a supported extension point,
document it explicitly and keep the symbol `STV_DEFAULT`. If protected
visibility is intentional (performance, hardening), call it out in release
notes so profiling/mocking tooling that depends on interposition isn't
surprised.

**Real-world example:** GCC's `-fno-semantic-interposition` makes the same
trade-off at compiler-flag granularity — assuming interposed implementations
are semantically equivalent so intra-TU calls can bind directly, trading
interposability for codegen freedom.

## References

- [ELF symbol visibility](https://refspecs.linuxfoundation.org/elf/gabi4+/ch4.symtab.html)
- [GCC `-fno-semantic-interposition`](https://gcc.gnu.org/onlinedocs/gcc/Code-Gen-Options.html)
- [GNU ld `-Bsymbolic`](https://sourceware.org/binutils/docs/ld/Options.html)
