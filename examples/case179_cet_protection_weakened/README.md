# Case 179: CET Protection Weakened

**Category:** Security Hardening (ELF/Loader) | **Verdict:** COMPATIBLE_WITH_RISK

## What this case is about

```c
/* v1 and v2 implement identical logic. Only the compiler flag differs. */
int dispatch(int which, int a, int b) {
    binop_fn table[2] = { add, sub };
    return table[which & 1](a, b);   /* indirect call through a pointer */
}
```

```text
v1: gcc -shared -fPIC -fcf-protection=full v1.c -o libv1.so
v2: gcc -shared -fPIC -fcf-protection=none v2.c -o libv2.so
```

The **source is unchanged**. `-fcf-protection` controls whether the compiler
emits Intel CET (Control-flow Enforcement Technology) instrumentation:
`ENDBR64` "landing pad" instructions at every valid indirect-branch target
(**IBT**) and shadow-stack bookkeeping for `call`/`ret` (**SHSTK**). v1 is
built with full CET instrumentation; v2 drops it entirely.

## Why this case matters: the same code, less protected

`dispatch()`'s indirect call through `table[which & 1]` is exactly what IBT
defends: if an attacker can corrupt that function pointer (a classic
buffer-overflow / use-after-free primitive), a CET-enabled binary refuses to
jump anywhere that is not a declared `ENDBR64` landing pad — collapsing a
large class of jump-oriented-programming (JOP) gadget chains. v2 still
*works* identically for legitimate input (the demo below prints the same
results either way) — this is not a functional regression, and abicheck
correctly does not call it `BREAKING`. But the security posture regressed
silently: nothing about the exported API, symbol table, or types changed at
all.

## What abicheck detects

- **`cet_protection_weakened`** — the `.note.gnu.property` section's x86
  feature bits dropped both `IBT` and `SHSTK` (`IBT, SHSTK` → `(none)`).
  **Evidence tier L0** — read directly from the ELF property note; no DWARF,
  no headers, no build metadata. (The opposite direction —
  `cet_protection_improved` — fires when a rebuild *adds* CET
  instrumentation that was previously absent.)

**Overall verdict: COMPATIBLE_WITH_RISK** — functionally identical, silently
less hardened. Under a security-focused policy profile this can be
escalated to a hard gate; under the default profile it is a deployment risk
worth a human look.

## How to reproduce

```bash
gcc -shared -fPIC -fcf-protection=full v1.c -o libv1.so
gcc -shared -fPIC -fcf-protection=none v2.c -o libv2.so
readelf -n libv1.so | grep -A2 propert   # x86 feature: IBT, SHSTK
readelf -n libv2.so | grep -A2 propert   # (no .note.gnu.property section)

python3 -m abicheck.cli dump libv1.so -o /tmp/v1.json
python3 -m abicheck.cli dump libv2.so -o /tmp/v2.json
python3 -m abicheck.cli compare /tmp/v1.json /tmp/v2.json
# → COMPATIBLE_WITH_RISK: cet_protection_weakened (IBT, SHSTK -> (none))
```

## Real Failure Demo

There is deliberately no crash to demonstrate: `dispatch()` behaves
identically under both builds for legitimate callers.

```bash
gcc app.c v1.c -o app
./app
# dispatch(0, 5, 3) = 8 (add, expected 8)
# dispatch(1, 5, 3) = 2 (sub, expected 2)
```

The risk this case encodes is exploitability under attack, not correctness
under normal use — the kind of change a functional test suite will never
catch, and that only reading the binary's own hardening metadata reveals.

## Mitigation

- Build security-sensitive libraries with `-fcf-protection=full` (or the
  distro toolchain default that already enables it) and treat a regression
  as a release blocker, not a style choice.
- If a specific translation unit genuinely cannot support CET (rare —
  usually hand-written assembly without `ENDBR64` landing pads), scope
  `-fcf-protection=none` to that file only, not the whole library.

## References

- [Intel CET: Control-flow Enforcement Technology](https://www.intel.com/content/www/us/en/developer/articles/technical/technical-look-control-flow-enforcement-technology.html)
- [GCC: `-fcf-protection`](https://gcc.gnu.org/onlinedocs/gcc/Instrumentation-Options.html)
- Related cases: [case135_stack_canary_removed](../case135_stack_canary_removed/README.md),
  [case134_relro_weakened](../case134_relro_weakened/README.md)
