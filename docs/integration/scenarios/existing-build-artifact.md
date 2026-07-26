# Scenario S3: Reuse an Existing, Expensive Build

Your project already has a build — possibly a slow one (a large C++ codebase,
a from-scratch toolchain bootstrap, a cross-compile). You don't want abicheck
re-building anything, and you don't want to hand-wire binary/header paths
into every check. This is
[ADR-047](../../contribute/adr/047-github-actions-integration-model.md) §8's
S3: "build once, scan many" — the preferred flow for any repository beyond
S1's single-file minimal case.

## The model

Your build (whatever produces it — CMake `install`, a Meson step, a
hand-written `install.sh`) publishes one **build output** directory per
[build profile](../concepts.md#build-profile): binaries, public header roots,
and a `build-output.json` manifest naming them —
[the exact schema](../../reference/build-output-schema.md). abicheck never
runs your build; it only reads what your build already published.

```text
abicheck-build-linux-x86_64-gcc13-release/
  build-output.json
  artifacts/lib/libfoo.so.1.5
  headers/foo/
```

> **No `abicheck project emit-build` helper exists yet** — author
> `build-output.json` by hand, or generate it from your build system's own
> `install`/manifest step. See the
> [schema reference](../../reference/build-output-schema.md) for the exact
> shape and validation rules (`abicheck project validate-build` checks it
> before anything consumes it).

## The check

Upload that directory as a `abicheck-build-<profile-id>` artifact, upload
your build's own candidate binaries as a `abicheck-candidate-<profile-id>`
artifact, and call
[`check-project.yml`](../../reference/reusable-workflows.md) — it downloads
both, generates a [run plan](../../reference/run-plan-schema.md) from your
`.abicheck.yml` `targets:`/`profiles:` block plus the build output, and runs
one check per target:

```yaml
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      # ... your existing build, producing the directory shown above ...
      - uses: actions/upload-artifact@v7
        with:
          name: abicheck-build-linux-x86_64-gcc13-release
          path: abicheck-build-linux-x86_64-gcc13-release/
      - uses: actions/upload-artifact@v7
        with:
          name: abicheck-candidate-linux-x86_64-gcc13-release
          path: build/lib/

  check:
    needs: build
    uses: abicheck/abicheck/.github/workflows/check-project.yml@v1
```

This is the same primitive S13 (package-only inputs), S17 (multiple build
profiles — one such directory per profile), and S18 (cross compilation — the
directory is authored on the build host, the check runs elsewhere) all build
on; only the number of profiles and how the directory gets populated differ.

## When to move past this scenario

- **Your build doesn't naturally produce a compile database / source facts,
  and you want source-level (not just binary/header) checks** → wire
  `abicheck_inputs/` into the build output — see
  [Producing Source Facts](../../use/producing-source-facts.md).
- **Your libraries ship as a release bundle with cross-library dependencies,
  not independently** → [S14: Multi-DSO Release Bundle](release-bundle.md),
  [Multi-Binary Releases](../../use/multi-binary.md).

## See also

- [Which Scenario Am I?](../index.md) — the full scenario index.
- [Build Output Schema](../../reference/build-output-schema.md) — the exact
  contract and validation rules.
- [Reusable Workflows Reference](../../reference/reusable-workflows.md) —
  `check-project.yml`'s full artifact-staging contract.
- [Run Plan Schema](../../reference/run-plan-schema.md) — how `.abicheck.yml`
  plus `build-output.json` becomes the concrete list of checks a run performs.
