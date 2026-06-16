# Security-hardening drift

A library upgrade can be perfectly ABI-compatible yet silently **weaken the
security hardening** of the shipped binary — full RELRO downgraded to partial,
the stack canary dropped, `_FORTIFY_SOURCE` disabled, or a writable+executable
segment introduced. None of these break existing consumers, so a normal
compatibility gate stays green. abicheck captures a `checksec`-equivalent
surface from the ELF and reports a *weakening* transition as a deployment risk.

## What is captured

For every ELF, the snapshot records:

| Property | Source | Meaning |
|----------|--------|---------|
| `relro` | `PT_GNU_RELRO` + `BIND_NOW` | `none` / `partial` / `full` RELRO |
| `bind_now` | `DT_BIND_NOW`, `DF_BIND_NOW`, `DF_1_NOW` | eager binding (whole GOT read-only) |
| `is_pie` | `ET_DYN` + `DF_1_PIE` | position-independent **executable** |
| `has_stack_canary` | `__stack_chk_fail` / `__stack_chk_guard` references | `-fstack-protector` |
| `has_fortify_source` | `*_chk` wrapper references | `_FORTIFY_SOURCE` |
| `has_writable_executable_segment` | `PT_LOAD` with `PF_W` and `PF_X` | W^X violation |
| `has_executable_stack` | `PT_GNU_STACK` with `PF_X` | NX disabled |

## Change kinds

A regression (and only a regression — improvements are never findings) emits:

- `relro_weakened` — full→partial or →none RELRO
- `pie_disabled` — PIE executable became non-PIE
- `stack_canary_removed` — `-fstack-protector` no longer referenced
- `fortify_source_weakened` — `_FORTIFY_SOURCE` wrappers no longer referenced
- `writable_executable_segment` — a W^X segment was introduced
- `executable_stack` — executable stack introduced (regression)

All of these are `COMPATIBLE_WITH_RISK` by default, so they surface in the
report without failing a standard compatibility gate.

The opposite, improving transition — removing an executable stack — is reported
as the distinct `executable_stack_removed` kind (`COMPATIBLE`, informational).
Keeping the two directions as separate kinds is what lets the `security` policy
gate the regression (`executable_stack`) without ever failing a hardening
*improvement*.

## Turnkey gating: the shipped `security` policy

abicheck ships a built-in policy that promotes the hardening kinds to hard
breaks. Reference it by name — no file to author:

```bash
abicheck compare libfoo.so.1 libfoo.so.2 --policy-file security
```

`--policy-file security` resolves to the packaged
[`abicheck/policies/security.yaml`](https://github.com/abicheck/abicheck/blob/main/abicheck/policies/security.yaml).
It uses `base_policy: strict_abi` and gates `relro_weakened`, `pie_disabled`,
`stack_canary_removed`, `fortify_source_weakened`, `writable_executable_segment`
and `executable_stack` to `break`, and `rpath_changed` / `runpath_changed` to
`warn`. To customize, copy it and pass your own path.

## Example

```console
$ abicheck compare libfoo.so.1 libfoo.so.2 --policy-file security
BREAKING
  relro_weakened     GNU_RELRO    RELRO weakened: full → none
  stack_canary_removed __stack_chk_fail  Stack canary removed: -fstack-protector no longer referenced
```

Without the policy the same comparison reports `COMPATIBLE_WITH_RISK` and the
two findings appear under the deployment-risk section.

## Scope

This covers the ELF hardening surface. Non-ELF hardening (PE `/GS`,
`/DYNAMICBASE`; macOS hardened runtime) is out of scope for now.
