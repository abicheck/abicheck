# Case 136: Executable Stack Removed (the fix direction)

**Category:** ELF / Security | **Verdict:** COMPATIBLE

## What this case is about

This is the **fix** counterpart to [case49](../case49_executable_stack)
(which goes the bad direction). v1 has an executable stack: `PT_GNU_STACK` with
flags `RWE` (`-Wl,-z,execstack`). v2 corrects it to `RW`
(`-Wl,-z,noexecstack`), restoring NX (No-eXecute) protection. The exported ABI
surface is identical in both.

Tracking the *removal* of an executable stack matters because abicheck should
report it as a positive, compatible change (a hardening improvement), not flag
it as a regression — the symmetric complement of detecting the regression.

## What abicheck detects

- **`EXECUTABLE_STACK_REMOVED`**: v1's `GNU_STACK` is executable (`RWE`); v2's is
  not (`RW`). Classified as a compatible quality/security improvement.

**Overall verdict: COMPATIBLE.**

## How to reproduce

```bash
gcc -shared -fPIC -g v1.c -o libv1.so -Wl,-z,execstack
gcc -shared -fPIC -g v2.c -o libv2.so -Wl,-z,noexecstack

readelf -lW libv1.so | grep GNU_STACK   # RWE
readelf -lW libv2.so | grep GNU_STACK   # RW

python3 -m abicheck.cli dump libv1.so -o v1.json
python3 -m abicheck.cli dump libv2.so -o v2.json
python3 -m abicheck.cli compare v1.json v2.json
# → COMPATIBLE + EXECUTABLE_STACK_REMOVED
```

## How to fix

Nothing to fix — v2 is the corrected artifact. Ensure all assembly inputs carry
`.section .note.GNU-stack,"",@progbits` so the linker never unions in an
executable stack.

## Real Failure Demo

**Severity: SECURITY IMPROVEMENT (informational)**

Both versions run identically; v2 simply restores NX protection on the process
stack. abicheck reports the change as compatible, distinguishing a hardening
*improvement* from a regression.
