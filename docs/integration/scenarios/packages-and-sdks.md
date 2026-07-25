# Scenario S13: Package-Only / Prebuilt Artifacts

You don't have (or don't want) a source checkout in this job at all — only
prebuilt packages (RPM, Deb, conda, wheel, tar, an SDK drop). No build step,
no compile database, no build integration.
[ADR-047](../../contribute/adr/047-github-actions-integration-model.md)
§8's S13 folds into the same [`check-project.yml`](../../reference/reusable-workflows.md)
flow as [S3](existing-build-artifact.md): "no separate workflow" (D5) — a
package is just another way to populate a
[build-output.json](../../reference/build-output-schema.md) directory,
not a new primitive.

## Two levels this can happen at

**Plain CLI, no `.abicheck.yml` project model at all** — if you only ever
compare two package files directly, you don't need any of the machinery
below:

```bash
abicheck compare old.rpm new.rpm \
  --debug-info old=old-debuginfo.rpm --debug-info new=new-debuginfo.rpm \
  --devel-pkg old=old-devel.rpm --devel-pkg new=new-devel.rpm
```

See [Choose Your Workflow](../../start/choose-your-workflow.md)'s
package row for every archive format `compare` auto-detects (RPM/Deb/tar/
conda/wheel).

**Part of a multi-target project's checks** — extract the package into the
[build-output.json layout](../../reference/build-output-schema.md#directory-layout)
`existing-build-artifact.md` describes (binaries under `artifacts/`, headers
under `headers/`), and the rest of that scenario's flow applies unchanged —
`check-project.yml` doesn't know or care whether `build-output.json`'s
binaries came from a compile step or an `rpm2cpio`/`dpkg-deb -x` extraction.

## When to move past this scenario

- **You need the underlying build's expensive compile step, not just its
  packaged output** → [S3: Reuse an Existing Build](existing-build-artifact.md).
- **The package is one member of a release bundle with cross-package
  dependencies** → [S14: Multi-DSO Release Bundle](release-bundle.md).

## See also

- [Which Scenario Am I?](../index.md) — the full scenario index.
- [Choose Your Workflow](../../start/choose-your-workflow.md) — the plain-CLI package row.
- [Build Output Schema](../../reference/build-output-schema.md) — the directory layout a package extracts into.
