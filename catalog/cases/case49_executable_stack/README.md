# Case 49: Executable Stack (GNU_STACK RWX)

**Category:** ELF / Security | **Verdict:** 🟢 COMPATIBLE (bad practice)

## Verdict and consumer impact

Both libraries export identical symbols with identical signatures and
identical DWARF type layouts — the functional ABI is unchanged. The
difference is in the `PT_GNU_STACK` program header: v1 (linked with
`-Wl,-z,execstack`) has flags `RWE` (read-write-execute), while v2 (linked
with `-Wl,-z,noexecstack`) has `RW`. An executable stack disables NX
(No-eXecute) protection for the *entire process* that loads the library,
making stack-based buffer overflows in that process trivially exploitable
— a security regression, not an ABI break. No recompilation is required
either way; the concern is deployment security posture, not compatibility.

## Old/new diff

| old/lib.c (linked `-Wl,-z,execstack`) | new/lib.c (linked `-Wl,-z,noexecstack`) |
|-----------|-----------|
| identical `compute()`/`transform()` source | identical `compute()`/`transform()` source |
| `GNU_STACK` segment: `RWE` | `GNU_STACK` segment: `RW` |

## abicheck command

```bash
gcc -shared -fPIC -g old/lib.c -o libstack_v1.so -Wl,-z,execstack
gcc -shared -fPIC -g new/lib.c -o libstack_v2.so -Wl,-z,noexecstack
abicheck compare libstack_v1.so libstack_v2.so
```

## Expected abicheck finding

```text
Verdict: COMPATIBLE (exit 0)

Quality Issues:
- executable_stack_removed: Executable stack removed: library now uses a
  non-executable stack — NX protection restored (good practice)
```

## Minimum evidence

`min_evidence: L0` — the `PT_GNU_STACK` program header's flags are read
directly from the ELF program header table; no debug info or headers
needed.

## Why abicheck catches it

abicheck reads each binary's `PT_GNU_STACK` segment flags from the ELF
program headers and compares the executable bit between old and new; a
library that drops the executable-stack flag (or gains one) is reported
as a quality finding — `executable_stack_removed`/`executable_stack_added`
— rather than a break, since the exported symbols and their types are
unaffected.

## Runtime failure demonstration

No observable effect on this app — `compute()`/`transform()` return
identical results in both versions, since neither function relies on
stack execution. The risk `PT_GNU_STACK` RWE exposes is latent: it widens
the blast radius of any *other* stack-corrupting bug in the process, it
doesn't itself cause one.

```bash
# Build old library (executable stack) + app
gcc -shared -fPIC -g old/lib.c -o libstack.so -Wl,-z,execstack
gcc -g app.c -L. -lstack -Wl,-rpath,. -o app
./app
# → compute(7) = 50
# → transform(3, 4) = 11

# Swap in new library (non-executable stack, no recompile)
gcc -shared -fPIC -g new/lib.c -o libstack.so -Wl,-z,noexecstack
./app
# → compute(7) = 50
# → transform(3, 4) = 11   (identical)

# The difference is visible only in the ELF program headers:
readelf -l libstack_v1.so | grep -A1 GNU_STACK
# → GNU_STACK ... RWE
readelf -l libstack_v2.so | grep -A1 GNU_STACK
# → GNU_STACK ... RW
```

## Safe redesign

Ensure every object linked into the library — especially hand-written or
generated assembly — carries a `.note.GNU-stack` section requesting a
non-executable stack, since the linker takes the *union* of all input
objects' stack permissions:

```asm
.section .note.GNU-stack,"",@progbits
```

or force it at link time regardless of inputs:

```bash
gcc -shared -fPIC lib.c asm.S -o libfoo.so -Wl,-z,noexecstack
```

**Real-world example:** Fedora and Debian both enforce
`-Wl,-z,noexecstack` via lintian/rpmlint packaging checks, rejecting
packages that ship an executable stack.

## Cross-tool comparison

`abidw`/`abidiff` operate on symbol and type ABI and have no dedicated
notion of `PT_GNU_STACK` security metadata, so there is no equivalent
finding to compare here — this is an abicheck-specific ELF hardening
check.

## References

- [Hardening ELF binaries — execstack](https://wiki.gentoo.org/wiki/Hardened/GNU_stack_quickstart)
- [Red Hat: Controlling execstack](https://access.redhat.com/solutions/2936741)
