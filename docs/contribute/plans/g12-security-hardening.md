# G12 — security-hardening drift surface + preset

**Registry:** `UC-WF-security-hardening` (`complete`)
**Effort:** M · **Risk:** low

## Problem

The "did this release silently weaken hardening?" usage model is now covered for
ELF. abicheck captures and diffs the checksec-style surface and ships a security
policy preset so hardening drift can be gated without hand-authoring YAML. This
plan records the implementation history; the original gaps were:

1. **Discoverability** — there is no built-in `security` severity preset or
   shipped policy, so gating requires hand-authoring YAML and knowing the kind
   slugs.
2. **Thin captured surface** — `elf` carries only `has_executable_stack` +
   `rpath`/`runpath`. Not captured: RELRO / BIND_NOW (full/partial/none), PIE,
   stack-canary, FORTIFY_SOURCE, writable+executable segments. The most common
   checksec-style regressions (full-RELRO → partial, PIE dropped) are invisible
   because the property was never recorded.

## Goal & acceptance criteria

- [x] ELF snapshot captures RELRO, BIND_NOW, PIE, stack-canary, FORTIFY, and
      W^X segment presence (a `checksec`-equivalent block).
- [x] New `RISK` `ChangeKind`s for the meaningful regressions (e.g.
      `relro_weakened`, `pie_disabled`) added per the root `CLAUDE.md` procedure.
- [x] A shipped `policies/security.yaml` and/or `--severity-preset security`
      makes hardening gating turnkey.
- [x] A release that weakens a hardening property fails under the security
      preset; an unchanged one passes.

## Design

1. Extend `abicheck/elf_metadata.py` to read the dynamic section / program
   headers / symbols for the checksec properties.
2. Add the diff rules in `abicheck/diff_platform.py` and the new kinds in
   `checker_policy.py` (keep them `RISK` by default, gateable to `break`).
3. Ship `policies/security.yaml` mapping the hardening kinds to `break`/`warn`.

## Files & surfaces

- `abicheck/elf_metadata.py`, `abicheck/diff_platform.py`,
  `abicheck/checker_policy.py`, a new `policies/security.yaml`, severity preset
  wiring in `abicheck/severity.py`.

## Tests

- Unit: two `.so`s differing only in RELRO/PIE → expected kind + gated verdict.
- Extend `tests/test_diff_platform_deep.py` coverage of the new properties.

## Out of scope

Non-ELF hardening (PE `/GS`, `/DYNAMICBASE`; macOS hardened runtime) — once the
ELF mechanism exists.
