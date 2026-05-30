# Case 115: _BitInt(N) width change (C23 64 → 128)

**Category:** Binary ABI break / C23 | **Verdict:** 🔴 BREAKING

## What changed

`v1` uses C23 `_BitInt(64)` for an accumulator's storage, parameter, and
return type. `v2` widens it to `_BitInt(128)`. The bit width N is part of the
type: it determines the storage size of the `Accumulator` struct and the
calling-convention treatment of the by-value parameter and return.

A consumer built against v1 passes/reads a 64-bit value where the v2 library
expects 128 bits, so arguments, the struct field, and the returned value are
all miscompiled.

(128 bits is the maximum `_BitInt` width clang supports, so both versions stay
buildable on every supported toolchain.)

## How abicheck catches it

`bit_int_width_changed` fires for each public slot (parameter, return, or
field) whose `_BitInt(N)` width changes — or that migrates to/from `_BitInt`.

## Files
- `v1.h` / `v2.h` — `_BitInt(64)` vs `_BitInt(128)` declarations
- `v1.c` / `v2.c` — the two library builds
- `app.c` — consumer built against the 64-bit interface
