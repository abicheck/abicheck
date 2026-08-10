---
name: native-consumer-compatibility
description: Determine whether one specific application, plugin, or host program will keep working against a new version of a C/C++ (or other compiled native) library. Use when asked whether an existing or old application will still work with a new library build, whether a plugin will still load in a host, whether upgrading a shared library will break a particular program that links it, or which of several consumers a library change actually affects. Answers per consumer, which can differ from the library's own global verdict in either direction.
license: Apache-2.0
metadata:
  abicheck-version-range: ">=0.6.0,<0.7.0"
  layer: A
  source: skills-src/native-consumer-compatibility/SKILL.md
---

# Will this specific consumer keep working?

The question is scoped to **one named consumer**, and its answer can diverge
from the library's global verdict in both directions: globally breaking but
this consumer untouched, or globally compatible but this consumer's required
entrypoint gone. That divergence is the whole point — this is not a filtered
view of `native-binary-compatibility-review`'s output.

Read [safety invariants](../shared/safety-invariants.md) first. The one that
bites hardest here: an import scan cannot see dynamically-resolved symbols,
so "not in the scanned set" is not "unaffected" (item 1).

## Preflight — abicheck availability and version range

Run `abicheck --version` before anything else and check the reported version
against this skill's declared version range (`metadata.abicheck-version-range`
in this file's own frontmatter). Outside that range — **below the minimum or
at/above the maximum** — stop and say so rather than proceeding on an
unvalidated version: below the minimum the surface this workflow uses may not
exist yet, and at/above the maximum it may have changed.

If abicheck is not installed, say so and how to install it. Do not install it
yourself ([safety invariants](../shared/safety-invariants.md) item 8).

## Step 0 — Identify the consumer and its coupling

Ask which of the two shapes this is; the branch runs to the end of the
workflow. See
[application vs. plugin branches](references/application-vs-plugin-branches.md)
for the full comparison, and
[consumer scoping](../shared/consumer-scoping.md) for the model behind both.

| Shape | The consumer is... | Contract established by |
|---|---|---|
| **Application** | a program or library that links the subject and calls into it | scanning what it actually imports |
| **Plugin / host** | a boundary where the subject must *provide* named entrypoints | the host declaring what it requires |

If both apply — a plugin that also links the library normally — run both
branches and report both.

## Step 1 — Establish the two library sides

Old and new builds of the library, under the same toolchain, exactly as in
any comparison
([baselines and comparability](../shared/baseline-and-comparability.md),
[compiler and build profiles](../shared/compiler-and-build-profiles.md)).
"Old" here is the version the consumer was **built against**, which may be
older than the project's own last release.

## Step 2A — Application branch

```bash
abicheck compare OLD NEW \
  --used-by path/to/consumer-binary \
  --depth headers \
  --report-mode root-cause \
  --format json \
  -o consumer.json
```

**One run per consumer when you were asked about several.** `--used-by` is
repeatable, and a single run does answer every consumer — but only its
*per-app summary* is per app (each app's own verdict, required-symbol count,
missing symbols/versions, relevant-change count, symbol coverage). The
findings themselves are reported as one **deduplicated union across all
apps**, with no app-to-finding association. So a multi-app run can tell you
*that* app B is affected, but not *which* finding affects it — and reading
the merged finding list as if it were app B's would misattribute app A's
break to app B, exactly the provenance failure
[safety invariants](../shared/safety-invariants.md) item 11 forbids.

Run the comparison once per consumer, into its own report, whenever you must
name the findings that reach each one. Use a single multi-app run only for the
coarser question "which of these consumers is affected at all", and report
only the per-app summary from it.

Go to `--depth source` (with `--sources`/`--build-info`) when the question is
"does the changed field actually reach this consumer" rather than "does this
consumer import the removed symbol". At shallower depths the answer is
sound for removals and weaker for reachability; say which you have
([evidence and depth](../shared/evidence-and-depth.md)).

## Step 2B — Plugin / host branch

The host's required entrypoints are declared, not discovered:

```bash
abicheck compare OLD NEW \
  --required-symbol plugin_init \
  --required-symbol plugin_shutdown \
  --format json \
  -o plugin.json
```

or, from a maintained list:

```bash
abicheck compare OLD NEW --required-symbols host-contract.txt --format json
```

Two failure shapes are specific to this branch, and are different findings:

- A required symbol present in the old side and **missing in the new** — the
  plugin stops loading. A regression.
- A required symbol missing from the **old side too** — the contract was
  already unsatisfied. A pre-existing defect, not a regression this change
  introduced. Say so; do not report it as a break caused by the upgrade.

## Step 3 — Read the result consumer-first

Per [report interpretation](../shared/report-interpretation.md), then narrow
to the consumer-relevant fields. These are per *report*, so they are the
answer for one consumer only when the run scoped to one consumer (above):

- `verdict` — **the scoped answer, for this consumer.** Under
  `--used-by`/`--required-symbol(s)` the CLI promotes the scoped result into
  the top-level `verdict`, because that is the gate-relevant one. This is the
  answer you were asked for; do not reach past it.
- `full_verdict` — the library-wide result, preserved separately. Present on
  **every** scoped run, whether or not it differs, so its presence signals
  nothing on its own: **compare the two values.** They diverge when
  `full_verdict != verdict` (e.g. `full_verdict: "BREAKING"` with
  `verdict: "COMPATIBLE"` — globally breaking, this consumer unaffected), and
  that is the case worth calling out. Report both, and say which is which.
- `changes[].affected_symbols` — what each finding touches.
- `changes[].public_reachable`, `reachability_state`, `reachability_kind`,
  `reachability_proof_path` — whether the finding reaches the scoped surface.
- `changes[].impact_is_direct`, `impact_proof_path`, `impact_assessment`,
  `affected_public_roots` — the impact view.

A finding that reaches nothing in this consumer's scope is not this
consumer's problem — but say that it is a real finding for someone else,
rather than dropping it silently.

## Step 4 — State the residual uncertainty, always

This branch of the workflow is not optional, because the scoping is
inherently incomplete:

- **Dynamic resolution.** `dlopen`/`dlsym`, `GetProcAddress`, plugin
  registries, and scripting bridges are invisible to import scanning. If the
  consumer does any of this, the scope is incomplete — say so.
- **Stripped or unresolvable consumer.** No scoping is possible; fall back
  to the global verdict and state that consumer scoping was unavailable.
- **Indirect consumers.** Something the consumer links may itself use the
  library. Scope those separately or state that you did not.
- **Behaviour.** ABI compatibility is not behavioural compatibility. A
  consumer can keep loading and still misbehave.
- **Runtime floors.** Even an unaffected consumer fails if the new build
  raised its glibc floor — `abicheck deps compare`, reported separately
  ([compatibility contracts](../shared/compatibility-contracts.md)).

## Step 5 — Report

Per consumer:

- **Verdict for this consumer**, explicitly distinguished from the library's
  global verdict, including when they differ and why.
- **Which findings reach it**, traced to specific findings and their
  reachability evidence.
- **Which do not**, and that they remain real for other consumers.
- **What was not covered** — the step 4 list, concretely, not as boilerplate.
- **Remediation**, if it is affected: rebuild the consumer, pin the old
  library version, or fix the library
  ([the remediation catalogue](../shared/remediation-catalog.md)).

## Termination criteria

Done when every named consumer has its own answer, each traced to findings
and reachability evidence, and the residual-uncertainty list has been stated
for each. A single merged verdict across several consumers, or an
"unaffected" claim with no statement about dynamic resolution, is not a
finished job.
