# Scenario S17: Multiple Build Profiles

The same library gets checked on more than one
[build profile](../concepts.md#build-profile) — Linux/GCC and Windows/MSVC
release, say — and a break on one platform is still a break, even if the
other platform's binary looks fine. [ADR-047](../../development/adr/047-github-actions-integration-model.md)
§8's S17: which lanes are actual ABI contracts (gate CI, get a baseline) vs.
test-only CI lanes is an explicit `.abicheck.yml` allowlist, never "every CI
lane that happens to build this library."

## One build-output artifact per profile, one matrix cell per (target, profile)

Declare every contract profile, marking any lane that is *not* an ABI
promise as `contract: false`:

```yaml
profiles:
  linux-x86_64-gcc13-release:
    contract: true
    os: linux
    arch: x86_64
  windows-x86_64-msvc-release:
    contract: true
    os: windows
    arch: x86_64
  linux-x86_64-gcc13-debug:
    contract: false   # a test-only lane -- never gets a baseline, never gates CI

targets:
  libfoo:
    binary_pattern: "lib/libfoo.so*"   # matched against each profile's own build output
    checks:
      - channel: accepted-main
        depth: headers
        # no explicit `profiles:` selector -- the implicit sweep below applies
```

An implicit (no `profiles:` selector) `checks:` entry runs on *every*
`contract: true` profile that actually builds this target — silently
skipping one that doesn't, never erroring, since the whole point of the
sweep is "wherever this makes sense." Your build produces one
`abicheck-build-<profile-id>` artifact **per contract profile** — one build
job per profile, each uploading its own directory (§2's "one
`build-output.json` = one build profile, always" design point) — and
[`check-project.yml`](../../reference/reusable-workflows.md) fans out one
matrix cell per `(target, profile)` pair automatically.

## When to move past this scenario

- **You need an *explicit* profile selector** (a check that should only run
  on some, not every, contract profile) → `checks[].profiles:` — see the
  [Project Targets Schema](../../reference/project-targets-schema.md) —
  which is also a **hard error**, not a silent skip, if the named profile
  turns out not to build the target (a real misconfiguration, unlike the
  implicit sweep's legitimate "doesn't apply here").
- **One target needs two baselines gated independently, on the same
  profile** → [S21](../index.md), two `check-target` calls differing only in
  `baseline-channel`.

## See also

- [Which Scenario Am I?](../index.md) — the full scenario index.
- [Project Targets Schema](../../reference/project-targets-schema.md) — the full `profiles:`/`checks[].profiles:` contract.
- [Build Output Schema](../../reference/build-output-schema.md#schema-abicheckbuild-outputv1) — why one artifact is always exactly one profile.
