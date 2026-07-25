# Scenario S15: Multiple Independent Targets, One Build

You have several shared libraries, built together (one CI job, one
[build output](../../reference/build-output-schema.md)), but they don't
depend on each other — a break in one says nothing about the others. This is
the oneDAL/PVXS-class case
[ADR-047](../../contribute/adr/047-github-actions-integration-model.md)
§8 names S15: N separate checks, N separate reports, each keeping its own
header/compiler context. Deliberately distinct from
[S14](release-bundle.md) (a release bundle — one report, cross-library
findings): here, one target's failure never invalidates another's report.

## Declaring several targets

Nothing beyond what [S3](existing-build-artifact.md) already sets up — list
every target under `.abicheck.yml`'s `targets:` block, each with its own
`checks:`:

```yaml
targets:
  libfoo:
    binary_pattern: "lib/libfoo.so*"
    public_headers: ["headers/foo"]
    checks:
      - channel: accepted-main
        depth: headers
  libbar:
    binary_pattern: "lib/libbar.so*"
    public_headers: ["headers/bar"]
    checks:
      - channel: accepted-main
        depth: headers
```

[`check-project.yml`](../../reference/reusable-workflows.md) generates one
matrix cell per target from this — each cell downloads its own candidate
binary (via its own `binary_pattern` glob) and runs independently
(`fail-fast: false`); `libfoo`'s check failing never skips or blocks
`libbar`'s.

## Do you need a combined CI status?

Not necessarily. If each target's own check is already a required
branch-protection status check, that's the whole gate — no extra step. A
combined "one CI status from N checks" is
[S28](../index.md#multiple-libraries-profiles-or-channels)'s job
(`check-project.yml`'s own trailing `aggregate` job, which runs automatically
whenever any cell in the plan uses `gate-mode: deferred`), not something S15
itself requires.

## When to move past this scenario

- **Your libraries actually depend on each other and ship together** →
  [S14: Multi-DSO Release Bundle](release-bundle.md).
- **You want one CI status combining every target's result** → S28,
  [Reusable Workflows Reference](../../reference/reusable-workflows.md)'s
  `aggregate` job.
- **Different targets need different profiles, not just different targets on
  one profile** → S17, [Project Targets Schema](../../reference/project-targets-schema.md).

## See also

- [Which Scenario Am I?](../index.md) — the full scenario index.
- [S3: Reuse an Existing Build](existing-build-artifact.md) — the build-output.json/check-project.yml foundation this scenario builds on.
- [Run Plan Schema](../../reference/run-plan-schema.md) — how N targets become N matrix cells.
