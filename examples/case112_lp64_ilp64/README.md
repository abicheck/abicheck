# Case 112: LP64 → ILP64 integer-model switch (oneMKL MKL_INT 32→64)

**Category:** Binary ABI break / numerical-library ABI hazard | **Verdict:** 🔴 BREAKING

## What changed

`v1` is the **LP64** interface: `MKL_INT` is `int` (32-bit). `v2` is the
**ILP64** interface: `MKL_INT` is `long` (64-bit). Every public entry point
that takes a dimension, stride, or count — and the functions that return one —
flips its integer width at the same time. The function *names* are unchanged
(they are `extern "C"`), so a consumer linked against the LP64 build resolves
the ILP64 symbols at load time but passes/reads integers with the wrong width:
array indices and lengths are silently truncated or sign-extended.

This is the highest-value numerical-library ABI hazard: for example oneMKL
ships both interfaces and they are not interchangeable.

## How abicheck catches it

`integer_model_changed` fires because a large fraction of public integer
parameters/returns flip width together and the `MKL_INT` integer typedef
changes its underlying size (`int` → `long`), which is the signature of an
LP64↔ILP64 switch. Individual `func_params_changed` / `func_return_changed`
findings are still reported per symbol; the grouped diagnostic names the root
cause.

## Files
- `v1.h` / `v2.h` — the LP64 and ILP64 header versions
- `v1.c` / `v2.c` — the two library builds
- `app.c` — consumer built against the LP64 interface
