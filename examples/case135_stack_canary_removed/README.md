# Case 135: Stack Canary Removed

**Category:** ELF / Security | **Verdict:** COMPATIBLE_WITH_RISK

## What this case is about

Both libraries export the same functions with the same signatures. v1 is
compiled with **`-fstack-protector-all`**, so every function (including
`process()`, which has an on-stack buffer) gets a stack-smashing guard — a
canary written on entry and checked before return via a reference to
`__stack_chk_fail`. v2 is compiled with **`-fno-stack-protector`**, removing
all canaries and the `__stack_chk_fail` dependency.

Stack canaries detect contiguous stack buffer overflows before a corrupted
return address is used. Dropping them is a security regression even though the
functional ABI (symbols, types, layout) is unchanged.

## What abicheck detects

- **`STACK_CANARY_REMOVED`**: the stack-protector guard references present in v1
  are gone in v2. Classified as a deployment/security risk, not an ABI break.

(The compare may also note `symbol_version_required_removed` — a side effect of
dropping the `__stack_chk_fail@GLIBC` dependency.)

**Overall verdict: COMPATIBLE_WITH_RISK.**

## How to reproduce

```bash
gcc -shared -fPIC -g -O2 v1.c -o libv1.so -fstack-protector-all
gcc -shared -fPIC -g -O2 v2.c -o libv2.so -fno-stack-protector

# v1 references __stack_chk_fail; v2 does not
nm -D libv1.so | grep __stack_chk_fail
nm -D libv2.so | grep __stack_chk_fail || echo "no canary in v2"

python3 -m abicheck.cli dump libv1.so -o v1.json
python3 -m abicheck.cli dump libv2.so -o v2.json
python3 -m abicheck.cli compare v1.json v2.json
# → COMPATIBLE_WITH_RISK + STACK_CANARY_REMOVED
```

## How to fix

Build release shared objects with at least `-fstack-protector-strong`. Distro
hardening policies require it.

## Real Failure Demo

**Severity: SECURITY / BAD PRACTICE**

`process()` still runs and produces the same output, but a buffer overflow that
v1 would have caught at return time now goes undetected in v2.
