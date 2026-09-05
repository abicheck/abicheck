# Case 134: RELRO Weakened

**Category:** ELF / Security | **Verdict:** 🟡 COMPATIBLE_WITH_RISK

## Verdict and consumer impact

Both libraries export identical symbols with identical signatures — every
existing consumer keeps working exactly as before, no recompilation needed.
The difference is link hardening: v1 is linked with **full RELRO**
(`-Wl,-z,relro -Wl,-z,now`), giving it a `PT_GNU_RELRO` program header and
an eagerly-bound, read-only GOT after startup. v2 drops it
(`-Wl,-z,norelro`), so the `GNU_RELRO` segment disappears and the GOT stays
writable for the process lifetime — a security regression (widens the
attack surface for a GOT-overwrite exploit) even though the functional ABI
is untouched.

## Old/new diff

| v1.c | v2.c |
|------|------|
| `int compute(int x) { return x * x + 1; }`<br>`int transform(int x, int y) { return x + y * 2; }`<br>(linked `-Wl,-z,relro -Wl,-z,now`) | *(identical source)*<br>(linked `-Wl,-z,norelro`) |

## abicheck command

```bash
gcc -shared -fPIC -g v1.c -o libfoo_v1.so -Wl,-z,relro -Wl,-z,now
gcc -shared -fPIC -g v2.c -o libfoo_v2.so -Wl,-z,norelro
abicheck compare libfoo_v1.so libfoo_v2.so
```

## Expected abicheck finding

```text
Verdict: COMPATIBLE_WITH_RISK (exit 0)

- relro_weakened: RELRO weakened: full → none
```

## Minimum evidence

`min_evidence: L0` — the `PT_GNU_RELRO` program header (or its absence) is
visible directly in the ELF program-header table; no debug info or headers
needed.

## Why abicheck catches it

abicheck reads each binary's ELF program headers and classifies the RELRO
level (`none`/`partial`/`full`) from the presence and flags of
`PT_GNU_RELRO` plus the `DT_BIND_NOW`/`DT_FLAGS` dynamic tags; a downgrade
between the two sides is reported directly from that L0 evidence.

## Runtime failure demonstration

**No observable effect on existing binaries.** `compute()`/`transform()`
produce identical output before and after the swap — this is a deployment
hardening regression, not a functional break:

```bash
gcc -shared -fPIC -g v1.c -o libfoo.so -Wl,-z,relro -Wl,-z,now
gcc -g app.c -L. -lfoo -Wl,-rpath,. -o app
./app
# → compute(7) = 50
# → transform(3, 4) = 11

# Swap in new library (no recompile)
gcc -shared -fPIC -g v2.c -o libfoo.so -Wl,-z,norelro
./app
# → compute(7) = 50          (unchanged)
# → transform(3, 4) = 11     (unchanged)
```

The regression only matters if an attacker can already write to the
process's memory — full RELRO removes the GOT as a target; `norelro`
leaves it writable and exploitable for the process's whole lifetime.

## Safe redesign

Keep full RELRO in release builds: `-Wl,-z,relro -Wl,-z,now`. Distribution
hardening policies (Debian, Fedora) flag partial/absent RELRO on shared
objects and can reject the package outright.

## Cross-tool comparison

`abidiff` compares ABI XML dumps (symbols and types) and has no concept of
link-time hardening flags, so it reports no difference at all between these
two libraries — RELRO weakening is invisible to it. `checksec`-style tools
catch the hardening regression but don't do ABI diffing, so they'd miss a
genuine symbol/type break in the same release. abicheck reports both from
one pass over the binary.
