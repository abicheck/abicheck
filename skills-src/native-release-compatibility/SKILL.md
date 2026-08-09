---
name: native-release-compatibility
description: Decide whether a release of a C/C++ (or other compiled native) library can ship as a minor or patch version, or whether it requires a major version and SONAME bump. Use when asked if a release can go out as a minor version, what version number a release should get, whether a SONAME bump is needed, or whether a set of libraries is ready to release without breaking consumers. Judges the whole release — every library, platform, and build profile — against the last supported release, and treats an unrun matrix target as unknown rather than passing.
license: Apache-2.0
metadata:
  abicheck-version-range: ">=0.5.0,<0.6.0"
  layer: A
  source: skills-src/native-release-compatibility/SKILL.md
---

# Deciding whether a release can ship compatibly

The object here is a **release**, not a diff: several libraries, several
platforms, several build profiles, judged against the version users actually
have. A release can be compatible on every individual diff and still be
un-shippable because its evidence matrix is incomplete — which is why this is
not `native-binary-compatibility-review` with a wider input.

Read [safety invariants](../shared/safety-invariants.md) first. Two govern
this workflow: a matrix cell that did not run is **unknown**, never passing
(item 4), and a diagnostic run may not back a release decision (item 3).

## Preflight — abicheck availability and version range

Run `abicheck --version` before anything else and check the reported version
against this skill's declared version range (`metadata.abicheck-version-range`
in this file's own frontmatter). Outside that range — **below the minimum or
at/above the maximum** — stop and say so rather than proceeding on an
unvalidated version: below the minimum the surface this workflow uses may not
exist yet, and at/above the maximum it may have changed.

If abicheck is not installed, say so and how to install it. Do not install it
yourself ([safety invariants](../shared/safety-invariants.md) item 8).

## Step 0 — Establish the release contract

- What is the **versioning scheme**, and what does each level promise? See
  [SONAME and semantic versioning](references/soname-and-semver.md).
- What is the **support window** — which released versions must keep
  working?
- What is the **shipped set** — which artifacts go out together?

## Step 1 — Choose the baseline: the last supported release

Not `HEAD~1`, not the last nightly. The comparison that matters is against
the version users have installed. Comparing against the previous commit
reports a compatible increment while the cumulative release delta is
breaking — the single most common way a release gate gives a false green.
See [baselines and comparability](../shared/baseline-and-comparability.md).

**If several releases are still supported, one baseline is not enough.** The
oldest is not a safe stand-in for the rest: a symbol added in v2 and removed
by this candidate is invisible against v1 (which never had it), yet every
binary built against the supported v2 breaks. Compatibility with the oldest
release does not imply compatibility with a newer one.

Treat the supported release as a **matrix dimension** — compare the candidate
against *each* supported release, and take the worst result. Add it to the
enumeration in step 2:

`{ library } × { supported release } × { platform / target } × { build profile }`

Where that is genuinely too many cells to run, say which releases you did
compare against and treat the rest as state **not run** (step 4) — an
unknown that blocks, not an assumption that the oldest covers them.

## Step 2 — Enumerate the matrix, before running anything

Write down every cell that must be checked:

`{ library } × { supported release } × { platform / target } × { build profile }`

The supported-release axis is a real dimension, not a single chosen baseline —
see step 1.

This list is the gate. A cell you did not enumerate cannot later be reported
as missing, and a cell that failed to run is not a pass
([safety invariants](../shared/safety-invariants.md) item 4).

For a project with an `.abicheck.yml`, validate it first so the matrix is
derived from real configuration rather than assumption:

```bash
abicheck project validate
```

`project plan` turns that configuration into the concrete list of check cells,
but it resolves a cell only for a profile whose build output it is given —
each as `--build-output PROFILE=DIR` — and exits 1 if that resolves to zero
checks. So it belongs *after* the builds exist, not at this enumeration step:

```bash
abicheck project plan .abicheck.yml \
  --build-output gcc=build/gcc/abicheck-build \
  --build-output clang=build/clang/abicheck-build
```

A profile you cannot supply a build output for is a matrix cell in state
**not run** (step 4) — an unknown that blocks, never a pass by omission.

## Step 3 — Run the release comparison

Per library:

```bash
abicheck compare OLD_RELEASE NEW \
  --depth headers \
  --scope-public-headers \
  --contract-evaluation --contract public \
  --report-mode root-cause \
  --format json \
  -o release-<library>.json
```

For a whole directory or package of libraries, one invocation covers the
fan-out and reports per library:

```bash
abicheck compare old_release_dir/ new_dir/ \
  --output-dir release-reports/ \
  --fail-on-removed-library \
  --format json
```

`--fail-on-removed-library` matters at release time specifically: a library
that vanished from the shipped set is a release-level break no per-library
comparison can see.

Across profiles or environments, use `--env-matrix`; across independently
produced per-target reports, fan in by pointing `aggregate` at the directory
holding them:

```bash
abicheck aggregate release-reports/ --format json -o aggregate.json
```

`REPORTS_DIR` is a required operand, not an option — `aggregate` with only
flags exits 64. Add `--manifest targets.json` when the expected target set is
declared, so a target that produced no report at all is reported as missing
rather than silently absent (matrix state **not run**, per step 4).

Gate configuration — `--policy`/`--policy-file`, `--severity-*`,
`--exit-code-scheme` — belongs here rather than in review runs; see
[policies and suppressions](../shared/policies-and-suppressions.md).

## Step 4 — Assemble the matrix result

For every enumerated cell record one of exactly four states:

| State | Meaning |
|---|---|
| **pass** | a real comparison ran and returned a compatible verdict at the required depth |
| **break** | a real comparison ran and returned `API_BREAK` or `BREAKING` |
| **not comparable** | `verdict: null` — the pair could not be compared; **not** a pass |
| **not run** | no result — **unknown**, and it blocks |

There is no fifth state. Do not collapse "not comparable" or "not run" into
either pass or break.

Then check the axes that are not per-finding, reading each report per
[report interpretation](../shared/report-interpretation.md):

- `evidence_tier` — did every cell reach the evidence this decision
  requires? A cell run at a shallower depth is diagnostic, not a gate result
  ([evidence and depth](../shared/evidence-and-depth.md)). Compare the tier
  across cells; there is no per-report depth echo to read.
- `contract_coverage_failures` and `contract_coverage_exit_contribution` —
  the orthogonal coverage axis. A `1` means the contract this release was
  judged against was never fully established. It is unsuppressible; report
  it ([safety invariants](../shared/safety-invariants.md) item 5).
- `contract_coverage == "partial"` — only one side carried an extraction
  fingerprint; a weaker guarantee than a full match.

## Step 5 — Decide the version

Given the matrix, the decision rule:

- Any **break** in the shipped surface → **major bump and SONAME change**.
  No amount of gate configuration changes this; it changes only whether CI
  says so.
- Any **not comparable** or **not run** cell → **cannot decide yet**. Say
  what is missing and what would resolve it. Never default to the
  permissive answer.
- Only compatible additions → **minor**.
- No public surface change at all → **patch**.
- Breaks that are source-only (`API_BREAK`) but not binary → allowed under
  some projects' schemes and not others; resolve against the scheme
  established in step 0, and say which rule you applied.

See [SONAME and semantic versioning](references/soname-and-semver.md) for
how the version number and the SONAME relate — they are not the same dial.

If a break is unwanted, the remediation is a design change, not a gate
change: [the remediation catalogue](../shared/remediation-catalog.md) and,
for the design vocabulary, the `native-api-evolution` skill. Re-run this
whole matrix afterwards.

## Step 6 — Report

- **The recommendation**: patch / minor / major+SONAME / cannot decide yet.
- **The matrix**, cell by cell, with its four-state result. Unrun and
  non-comparable cells listed explicitly, never omitted.
- **The breaks**, grouped by root cause
  ([root-cause grouping](../shared/root-cause-grouping.md)), each traced to
  findings.
- **Coverage caveats** — evidence tier per cell, coverage failures, partial
  contract coverage.
- **What this decision does not cover** — runtime/dependency-floor changes
  (`abicheck deps compare`), packaging, and behavioural compatibility.

Once a decision exists, offer to enforce it from CI:
[CI wiring](../shared/ci-wiring.md).

## Termination criteria

Done when every enumerated matrix cell has one of the four states, the
version recommendation follows from them by the rule above, and every
unknown is reported as an unknown. A recommendation issued with unrun cells
silently treated as passing is a false green, not a finished job.
