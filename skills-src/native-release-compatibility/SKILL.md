---
name: native-release-compatibility
description: Decide whether a release of a C/C++ (or other compiled native) library can ship as a minor or patch version, or whether it requires a major version and SONAME bump. Use when asked if a release can go out as a minor version, what version number a release should get, whether a SONAME bump is needed, or whether a set of libraries is ready to release without breaking consumers. Judges the whole release — every library, platform, and build profile — against the last supported release, and treats an unrun matrix target as unknown rather than passing.
license: Apache-2.0
metadata:
  abicheck-version-range: ">=0.6.0,<0.7.0"
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
  --depth headers \
  --scope-public-headers \
  --contract-evaluation --contract public \
  --fail-on-removed-library \
  --format json
```

Carry the same `--contract-evaluation --contract public` here as in the
per-library command above. The fan-out applies it to each library and folds
every library's own coverage contribution into the release's exit code, so
dropping it on this path would leave step 4 unable to see incomplete contract
evidence — and a cell would be recorded **pass** that the per-library workflow
would have blocked.

`--fail-on-removed-library` matters at release time specifically: a library
that vanished from the shipped set is a release-level break no per-library
comparison can see. Its exit `8` is checked ahead of the coverage-only
fallback, so a removed library is never masked by an unrelated coverage gap.

Across profiles or environments, use `--env-matrix`; across independently
produced per-target reports, fan in by pointing `aggregate` at the directory
holding them:

```bash
abicheck aggregate release-reports/ \
  --manifest targets.json \
  --format json -o aggregate.json
```

Two things are required, not optional, and omitting either exits 64:

- **`REPORTS_DIR`** — a positional operand, not an option.
- **An expected-target mode** — `--manifest`, `--run-plan`, or `--expect`.
  Prefer one of these: they declare the targets the matrix *must* produce, so
  a target that produced no report at all is reported as missing instead of
  silently absent. That is precisely the **not run** state of step 4, and
  getting it from the tool beats reconstructing it by hand.

`--discovered-only` aggregates whatever happens to be present with no
coverage gate. It is the wrong default for a release decision — it cannot
distinguish "this target passed" from "this target never ran" — so use it
only when there is genuinely no declared target set, and say so in the
report.

Gate configuration — `--policy`/`--policy-file`, `--severity-*`,
`--exit-code-scheme` — belongs here rather than in review runs; see
[policies and suppressions](../shared/policies-and-suppressions.md).

## Step 4 — Assemble the matrix result

For every enumerated cell record one of exactly five states, taken from the
report's own `verdict`:

| State | Verdict | Meaning |
|---|---|---|
| **pass** | `NO_CHANGE`, `COMPATIBLE` | a real comparison ran at the required depth, found nothing that threatens consumers, **and** its gate and coverage are clear (see below) |
| **risk** | `COMPATIBLE_WITH_RISK` | compiled consumers keep working, but a deployment risk needs manual review — see below |
| **break** | `API_BREAK`, `BREAKING` | a real comparison ran and found a break |
| **not comparable** | `null` | the pair could not be compared; **not** a pass |
| **not run** | — | no result — **unknown**, and it blocks |

There is no sixth state, and none of the last four may be collapsed into
**pass**.

**A compatible verdict is necessary but not sufficient for pass.** The
verdict answers the compatibility axis only; two other axes can fail
independently on the very same report, and both are release-blocking:

- `severity.blocking == true` — the project's configured gate rejects this
  cell even though nothing broke ABI (a policy-blocked addition, say). The
  run exits nonzero; recording it as pass contradicts the project's own gate.
- `contract_coverage_exit_contribution == 1` — the contract this cell was
  judged against was never fully established. Compatible *on the evidence
  gathered* is not the same as compatible, and this signal is unsuppressible
  precisely so it cannot be dropped here.

A cell clearing the verdict but failing either of these is **not pass**.
Record it as **risk** — same handling: name what failed, and make the version
recommendation conditional on resolving it. Read all three axes from every
report; keying the matrix off `verdict` alone is how a policy-blocked or
evidence-incomplete cell becomes a silent minor release.

**`COMPATIBLE_WITH_RISK` is the one that silently disappears if you let it.**
It exits `0`, exactly like `COMPATIBLE`, so an exit-code-only reading of the
matrix cannot see it — you must read the `verdict` field. It means the change
does not break already-linked consumers but carries a deployment risk that has
to be verified by a human: a raised dependency floor that some target
environments lack, or a change that links fine yet is semantically unsafe for
binaries built under the old contract. Record the cell as **risk**, carry its
findings into the report, and never fold it into pass.

Then check the axes that are not per-finding, reading each report per
[report interpretation](../shared/report-interpretation.md):

- `evidence_tier` — did every cell reach the evidence this decision
  requires? A cell run at a shallower depth is diagnostic, not a gate result
  ([evidence and depth](../shared/evidence-and-depth.md)). Compare the tier
  across cells; there is no per-report depth echo to read. For a cell run at
  `--depth build`/`source`, the tier cannot answer this — it stops at
  `header_aware` — so read that cell's `layer_coverage` and require the
  layers it depends on to be `present`, not `not_collected`.
- `contract_coverage_failures` and `contract_coverage_exit_contribution` —
  the orthogonal coverage axis. A `1` means the contract this release was
  judged against was never fully established. It is unsuppressible; report
  it ([safety invariants](../shared/safety-invariants.md) item 5).
- `contract_coverage == "partial"` — only one side carried an extraction
  fingerprint; a weaker guarantee than a full match.

## Step 5 — Decide the version

Given the matrix, the decision rule:

- Any **`BREAKING`** cell in the shipped surface → **major bump and a new
  ABI epoch** (SONAME on ELF, install name on Mach-O, DLL name on PE).
  Already-compiled consumers cannot survive it, so the loader-facing identity
  must change. No amount of gate configuration alters this; it alters only
  whether CI says so.
- Any **`API_BREAK`** cell (source-only) → a **major version bump under most
  schemes, but usually no ABI-epoch change**: already-compiled binaries keep
  working, only rebuilds break. Resolve the version level against the scheme
  from step 0 and say which rule you applied; do not prescribe a SONAME
  change for a break no installed consumer can observe.
- Any **not comparable** or **not run** cell → **cannot decide yet**. Say
  what is missing and what would resolve it. Never default to the
  permissive answer.
- Any **risk** cell → the version follows the rules below, but the
  recommendation is **conditional on resolving what that cell flagged**. Name
  it and what has to be verified — a deployment risk (does every target meet
  the new floor?), a blocking gate decision, or incomplete contract coverage.
  A patch or minor issued without surfacing these is a recommendation the
  evidence does not support.
- Only compatible additions → **minor**.
- No public surface change at all → **patch**.
(The two break rows above already split binary from source-only; the
distinction matters because conflating them either under-protects installed
consumers or forces a needless epoch bump on the whole ecosystem.)

See [SONAME and semantic versioning](references/soname-and-semver.md) for
how the version number and the SONAME relate — they are not the same dial.

If a break is unwanted, the remediation is a design change, not a gate
change: [the remediation catalogue](../shared/remediation-catalog.md) and,
for the design vocabulary, the `native-api-evolution` skill. Re-run this
whole matrix afterwards.

## Step 6 — Report

- **The recommendation**: patch / minor / major+SONAME / cannot decide yet.
- **The matrix**, cell by cell, with its five-state result. Risk, unrun, and
  non-comparable cells listed explicitly, never omitted.
- **The breaks**, grouped by root cause
  ([root-cause grouping](../shared/root-cause-grouping.md)), each traced to
  findings.
- **Coverage caveats** — evidence tier per cell, coverage failures, partial
  contract coverage.
- **What this decision does not cover** — the wider dependency graph
  (`abicheck deps compare`), packaging, and behavioural compatibility. A
  raised symbol-version floor is covered by the comparisons above
  (`runtime_floor_raised`); it belongs in the risk cells, not here.

Once a decision exists, offer to enforce it from CI:
[CI wiring](../shared/ci-wiring.md).

## Termination criteria

Done when every enumerated matrix cell has one of the five states, the
version recommendation follows from them by the rule above, every unknown is
reported as an unknown, and every **risk** cell's findings are surfaced with
what has to be verified. A recommendation issued with unrun cells
silently treated as passing is a false green, not a finished job.
