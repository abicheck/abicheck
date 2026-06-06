# G12 — security-hardening drift surface + preset

**Registry:** `UC-WF-security-hardening` (`partial`)
**Effort:** M · **Risk:** low

## Problem

The "did this release silently weaken hardening?" usage model is **partially**
served today: abicheck detects `executable_stack` and `runpath_changed`/
`rpath_changed`, and per-kind policy gating already works (a `--policy-file`
with `overrides: { executable_stack: break }` flips the verdict to `BREAKING`,
exit 4). Two gaps remain:

1. **Discoverability** — there is no built-in `security` severity preset or
   shipped policy, so gating requires hand-authoring YAML and knowing the kind
   slugs.
2. **Thin captured surface** — `elf` carries only `has_executable_stack` +
   `rpath`/`runpath`. Not captured: RELRO / BIND_NOW (full/partial/none), PIE,
   stack-canary, FORTIFY_SOURCE, writable+executable segments. The most common
   checksec-style regressions (full-RELRO → partial, PIE dropped) are invisible
   because the property was never recorded.

## Goal & acceptance criteria

- [ ] ELF snapshot captures RELRO, BIND_NOW, PIE, stack-canary, FORTIFY, and
      W^X segment presence (a `checksec`-equivalent block).
- [ ] New `RISK` `ChangeKind`s for the meaningful regressions (e.g.
      `relro_weakened`, `pie_disabled`) added per the root `CLAUDE.md` procedure.
- [ ] A shipped `policies/security.yaml` and/or `--severity-preset security`
      makes hardening gating turnkey.
- [ ] A release that weakens a hardening property fails under the security
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
