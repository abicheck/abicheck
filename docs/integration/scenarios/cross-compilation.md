# Scenario S18: Cross Compilation

Your library is cross-compiled — the binary that matters doesn't run on the
CI host that built it, and the toolchain that produced it isn't necessarily
the toolchain abicheck would auto-detect on that host.
[ADR-047](../../contribute/adr/047-github-actions-integration-model.md)
§8's S18: **no host auto-detection of target context** — the build host
authors the [build output](../../reference/build-output-schema.md), and the
check can run entirely offline/elsewhere, decoupled as two separate steps.

## The decoupling

1. **On the build host** (wherever the cross-compile actually runs):
   produce `abicheck-build-<profile-id>/` exactly as in
   [S3](existing-build-artifact.md) — `profile.id`/`os`/`arch`/`compiler`
   record the *target* platform's identity, not the build host's.
2. **Wherever the check runs** (the same job, a later job, or a completely
   separate workflow run): point the analysis at the cross-toolchain's own
   sysroot/prefix explicitly — never assume the runner's native compiler
   applies:

```yaml
- uses: abicheck/abicheck/actions/check-target@c9e135a3233b6d45e9571533f71293fde458a469  # not yet in a tagged release; pin main or newer
  with:
    name: libfoo
    gcc-prefix: arm-linux-gnueabihf-   # or gcc-path: /opt/cross/bin/arm-linux-gnueabihf-gcc
    sysroot: /opt/cross/arm-sysroot
    # ... rest of the check as in any other scenario ...
```

`gcc-path`/`gcc-prefix`/`gcc-options`/`sysroot` are forwarded straight
through to the analysis step in every mode (`compare`/`scan`/`dump`) — see
the [`check-target` reference](../../reference/check-target.md) for the full
input list.

## When to move past this scenario

- **You're not actually cross-compiling, just building on a different
  distro/toolchain version of the same architecture** → plain
  [S3](existing-build-artifact.md) already covers that; `gcc-prefix`/
  `sysroot` are for when the *target* platform genuinely differs from the
  build host's own.
- **The cross-compiled binary is one member of a multi-profile matrix**
  → combine with [S17: Multiple Build Profiles](multi-platform.md).

## See also

- [Which Scenario Am I?](../index.md) — the full scenario index.
- [S3: Reuse an Existing Build](existing-build-artifact.md) — the build-output.json foundation this scenario decouples across hosts.
- [`check-target` Action Reference](../../reference/check-target.md) — the full cross-compiler input list.
