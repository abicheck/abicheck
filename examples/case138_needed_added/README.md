# Case 138: DT_NEEDED Added

**Category:** ELF / Linker metadata | **Verdict:** COMPATIBLE

## What this case is about

Both libraries export identical symbols. v2 is linked with
`-Wl,--no-as-needed -lm`, which records a **`DT_NEEDED`** entry for
`libm.so.6` even though no math symbol is used. v1 has no such dependency. The
exported ABI surface is unchanged, but the library now drags an additional
shared object into every process that loads it.

A new `DT_NEEDED` is compatible for consumers of *this* library's API, but it
changes the runtime dependency closure — relevant for packaging, container
image size, and deployments where the new dependency might be absent.

## What abicheck detects

- **`NEEDED_ADDED`**: a `DT_NEEDED` entry present in v2 but not v1. Classified as
  a compatible quality/metadata change.

(The compare may also note `symbol_version_required_added_compat` from the new
dependency's version requirements.)

**Overall verdict: COMPATIBLE.**

## How to reproduce

```bash
gcc -shared -fPIC -g v1.c -o libv1.so
gcc -shared -fPIC -g v2.c -o libv2.so -Wl,--no-as-needed -lm

readelf -dW libv1.so | grep NEEDED
readelf -dW libv2.so | grep NEEDED   # adds libm.so.6

python3 -m abicheck.cli dump libv1.so -o v1.json
python3 -m abicheck.cli dump libv2.so -o v2.json
python3 -m abicheck.cli compare v1.json v2.json
# → COMPATIBLE + NEEDED_ADDED
```

## How to fix

Link with `--as-needed` (the default on most toolchains) so only genuinely-used
libraries become `DT_NEEDED`. This case documents the detection, not a required
fix.

## Real Failure Demo

**Severity: INFORMATIONAL**

The library loads and runs the same where `libm.so.6` is present, but the v2
artifact fails to load on a system that lacks the newly-added dependency.
