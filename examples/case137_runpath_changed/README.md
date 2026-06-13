# Case 137: DT_RUNPATH Changed

**Category:** ELF / Linker metadata | **Verdict:** COMPATIBLE

## What this case is about

Both libraries export identical symbols. v2 is linked with an embedded
`-rpath` (`-Wl,-rpath,/opt/vendor/lib -Wl,--enable-new-dtags`), which the
linker records as a **`DT_RUNPATH`** dynamic entry. v1 has none. The functional
ABI is unchanged, but the runtime library search path the dynamic loader uses
to resolve *this* library's dependencies has changed.

A baked-in `RUNPATH` changes where dependencies are found at load time. That can
silently alter which build of a transitive dependency is picked up, so it is
worth surfacing — without treating it as an ABI break.

## What abicheck detects

- **`RUNPATH_CHANGED`**: `DT_RUNPATH` differs between the two libraries.
  Classified as a compatible quality/metadata change.

**Overall verdict: COMPATIBLE.**

## How to reproduce

```bash
gcc -shared -fPIC -g v1.c -o libv1.so
gcc -shared -fPIC -g v2.c -o libv2.so -Wl,-rpath,/opt/vendor/lib -Wl,--enable-new-dtags

readelf -dW libv1.so | grep RUNPATH || echo "v1: none"
readelf -dW libv2.so | grep RUNPATH         # /opt/vendor/lib

python3 -m abicheck.cli dump libv1.so -o v1.json
python3 -m abicheck.cli dump libv2.so -o v2.json
python3 -m abicheck.cli compare v1.json v2.json
# → COMPATIBLE + RUNPATH_CHANGED
```

## How to fix

Avoid baking absolute `RUNPATH`/`RPATH` into distributed shared objects; prefer
`$ORIGIN`-relative paths or loader configuration. This case documents the
detection, not a required fix.

## Real Failure Demo

**Severity: INFORMATIONAL**

The library loads and runs the same, but its dependency search path now points
at `/opt/vendor/lib`, which can change which transitive dependency is resolved
on a different host.
