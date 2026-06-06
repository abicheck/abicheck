# G13 — cross-architecture comparison guardrail

**Registry:** `UC-PLAT-arch-guard` (`planned`)
**Effort:** S · **Risk:** low

## Problem

Comparing an x86-64 build against an aarch64 build of the *same* library version
returns a falsely reassuring `COMPATIBLE_WITH_RISK` verdict at 100%
binary-compatibility. The ELF snapshot never captures `e_machine` (nor
`EI_CLASS` 32/64, nor endianness), so a cross-architecture diff is not even
recognised as such — the only incidental trace is a `toolchain_flag_drift`
note scraped from `DW_AT_producer`. This is a real "false green" footgun for
multi-arch CI matrices. PE and Mach-O already carry a machine field — ELF is
the outlier.

## Goal & acceptance criteria

- [ ] ELF snapshot captures `e_machine`, `EI_CLASS`, and endianness.
- [ ] `compare` / `compare-release` treat a machine mismatch as a hard guard:
      either refuse with a clear error, or emit a top-level
      `ARCHITECTURE_MISMATCH` finding that dominates the verdict (never a
      compatible/low-risk result).
- [ ] Same-arch comparisons are unaffected.

## Design

1. Add the machine fields to `abicheck/elf_metadata.py` and the ELF snapshot
   model (PE/Mach-O already expose an equivalent).
2. In the diff entry point, compare machine/class/endianness first; on mismatch
   short-circuit to the guard outcome (new `ARCHITECTURE_MISMATCH` kind in
   `checker_policy.py`, classified to dominate — or a refusing `ValidationError`,
   decided in the plan's first step).

## Files & surfaces

- `abicheck/elf_metadata.py`, `abicheck/model.py` (ELF block),
  `abicheck/checker.py` / `abicheck/diff_platform.py` (guard),
  `abicheck/checker_policy.py` (kind).

## Tests

- `tests/test_arch_mismatch_guard.py`: x86-64 vs aarch64 snapshot → guard
  outcome; x86-64 vs x86-64 → normal path.

## Out of scope

Deliberate multi-arch *fat* binaries (Mach-O universal) — those expose multiple
slices and are a separate concern.
