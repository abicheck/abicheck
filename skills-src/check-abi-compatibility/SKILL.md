---
name: check-abi-compatibility
description: Review a diff, branch, commit, or pull request in a C/C++ shared library (built for Linux ELF with GCC or Clang) for binary and source compatibility with existing consumers. Use when asked to review a PR for binary compatibility, to check whether a change will break existing consumers or already-compiled applications, whether an existing application, plugin, or host will still work against a new library build, whether a change is ABI-safe, or to investigate why a compatibility check suddenly reported dozens of breaks. Produces a verdict with a root-cause explanation, backed by deterministic analysis rather than reading the diff alone. Can still be pointed at other native platforms/toolchains (Mach-O, PE/COFF, other compilers), but reports that outside its evaluated scope rather than with the same confidence.
license: Apache-2.0
metadata:
  abicheck-version-range: ">=0.6.0,<0.7.0"
  layer: A
  source: skills-src/check-abi-compatibility/SKILL.md
---

# Reviewing a native change for compatibility

Someone is asking, in effect: **will this break existing users, why, and
what is the least costly safe fix?** That is the job — an engineering
decision backed by deterministic evidence, not a transcript of commands run
and not a paraphrase of the diff. You are reviewing a change that has
**already been made** to a compiled library. Diagnosis, not design, and not
a whole-release qualification decision — this skill covers a single change's
compatibility review, and it ends in the
[structured decision below](#the-decision-you-report), every time.

Read [safety invariants](../shared/safety-invariants.md)
before reporting anything. The rule that governs this whole workflow: a
category you could not check is unverified, never clean.

## Candidate evaluation scope (v0.1)

This workflow's evidence pipeline targets: **C and C++ shared libraries;
Linux ELF; GCC and Clang; old and new built artifacts plus their public
headers; matched compiler and target profiles; reviewing a PR, branch, or
candidate build.** This is the scope the workflow is designed and scoped
for, not a scope with completed behavioral validation behind it — this
skill is an internal candidate (see `skills-src/CLAUDE.md`'s portfolio
status) with no comparative-lift evidence yet. Work confidently within it
on technical grounds; do not describe it to a user as "validated."

Outside it — Mach-O or PE/COFF binaries, a DPC++/SYCL library, a migration
across compiler vendors, or a binary with no headers available at all — the
tool can still be pointed at the problem and will often produce a real,
useful answer. But do not report that answer with the same confidence: state
plainly that this combination is outside this workflow's candidate
evaluation scope, and let the [decision](#the-decision-you-report) read
`NOT_VERIFIED` for the parts it could not stand behind, rather than
silently dropping the attempt or silently promoting it to a scoped verdict.

## Preflight — abicheck availability and version range

Run `abicheck --version` before anything else and check the reported version
against this skill's declared version range (`metadata.abicheck-version-range`
in this file's own frontmatter). Outside that range — **below the minimum or
at/above the maximum** — stop and say so rather than proceeding on an
unvalidated version: below the minimum the surface this workflow uses may not
exist yet, and at/above the maximum it may have changed.

If abicheck is not installed, say so and how to install it. Do not install it
yourself ([safety invariants](../shared/safety-invariants.md) item 8).

## Step 1 — Establish the compatibility promise and the decision

Which compatibility contract does the user mean — binary (installed
consumers, no rebuild), source API (their code still compiles), or runtime
(the binary still loads in a new environment)? The answers diverge. See
[compatibility contracts](../shared/compatibility-contracts.md).
When the user has not said and the answers would differ, answer both.

**Runtime is only partly this workflow's to answer.** This workflow's own
comparison (steps 2–9, all `compare`) proves the symbol-version-floor half of
runtime compatibility — a raised `GLIBC_*`/`GLIBCXX_*`/`CXXABI_*` requirement
surfaces as `runtime_floor_raised` and is read in step 5 like any other
finding. It does **not** drive `abicheck deps tree`/`abicheck deps compare`,
so it cannot confirm the rest of runtime compatibility: whether the binary's
full dependency graph actually resolves in a specific sysroot or container
image. When the contract is runtime, report the symbol-floor half from this
workflow's own evidence, and report the "does it actually load there" half as
`NOT_VERIFIED` unless the user separately ran `deps tree --sysroot` or `deps
compare` — do not let a clean `compare` verdict read as a full runtime
`VERIFIED_COMPATIBLE`.

Also confirm what the answer is actually *for*: ship this PR as-is, restore
a removed entry point, decide a version bump, or clear one named consumer.
That decision is what step 10's report exists to serve — everything before
it is evidence-gathering in service of it.

## Step 2 — Choose the correct baseline

Not `HEAD~1` by default. "Since the last release" means the **released**
artifact users actually have is the old side, not the previous commit; "does
this PR break anything" means the merge base. See
[baselines and comparability](../shared/baseline-and-comparability.md) for
the full decision table and for what a comparability refusal (`verdict:
null`) means and how to remediate it — that is not a pass, and step 5 below
depends on catching it before any verdict is read.

## Step 3 — Establish comparable old and new artifacts and build profiles

You need an old and a new **artifact**, not a diff. Reading the diff alone
cannot tell you a struct's size changed. Build both sides under the
**same** toolchain and flags, or the comparison will be refused or dominated
by toolchain noise
([compiler and build profiles](../shared/compiler-and-build-profiles.md)).

The mechanics — worktrees, released artifacts, stored snapshots, and the
preflight checklist — are in
[getting the two sides](references/getting-the-two-sides.md).

## Step 4 — Gather the strongest available deterministic evidence

`--depth headers` is the floor for reviewing a source change: `binary` depth
cannot see a signature or layout change at all. Go deeper (`--depth build`,
`--depth source`) when flags, macros, or reachability are in question. See
[evidence and depth](../shared/evidence-and-depth.md).

Run the comparison per
[the abicheck adapter](references/abicheck-adapter.md#the-canonical-comparison) —
that reference holds the exact command and every flag this step or a later
one may need to add, so it is not repeated here.

## Step 5 — Validate comparability and evidence coverage before interpreting a verdict

Do not read the verdict first. In order:

1. `verdict: null` → **not comparable**. Read `reason`, remediate the
   inputs per [baselines and comparability](../shared/baseline-and-comparability.md),
   re-run. Do not continue into the findings, and never report this as "no
   breaking changes found" — this is the workflow's own `NOT_VERIFIED`
   outcome, not a clean pass.
2. `evidence_tier`, `evidence_tiers`, `coverage_warnings`,
   `scope.resolved`, `layer_coverage` → what the run could actually see.
   Anything short of what step 4 asked for is `NOT_VERIFIED` for that part
   of the answer, not evidence of a clean result
   ([safety invariants](../shared/safety-invariants.md) item 1).
3. `verdict` and `summary` → the compatibility answer, only now.
4. `severity.*` → the grading of that answer, present whenever
   severity-aware grading was resolved from any source (a flag, the
   project's `.abicheck.yml`, a run profile, or a gate pack) — not only
   from a flag you passed.
5. `contract_coverage_failures` /
   `contract_coverage_exit_contribution` → the orthogonal coverage axis,
   deliberately unsuppressible ([safety invariants](../shared/safety-invariants.md)
   item 5).

Full field-by-field detail: [report interpretation](../shared/report-interpretation.md).

## Step 6 — Explain root causes and their blast radius

Never summarize the flat `changes` array. Use abicheck's own
`root_causes` grouping and rank the groups; see
[root-cause grouping](../shared/root-cause-grouping.md).

For each group, state the single underlying source change, its blast radius,
and whether it is the library's own change or toolchain/dependency churn.

### Branch: "why did this suddenly report dozens of breaks?"

This is the same workflow, entered from a different question. Work the
causes in this order, and stop at the first that explains the volume:

1. **Scoping.** Was `--scope-public-headers` used? Is `scope.resolved`
   true? An unscoped run reports internal and standard-library churn as
   findings.
2. **Toolchain drift.** Did the two sides use different compilers, standard
   libraries, or `-std=` values? A libstdc++ or dual-ABI change regenerates
   template instantiation symbols wholesale.
3. **Baseline.** Is the old side the intended baseline, or did it silently
   become an unrelated build?
4. **One real cause with a wide blast radius.** A single added struct member
   legitimately produces dozens of findings — `root_causes` shows this
   immediately.
5. **Genuinely many real breaks.** Report them.

Fixing scoping or the profile is the remediation for 1–3. Authoring a
suppression is not.

## Step 7 — Narrow to a named consumer, only when asked and supported

The default answer in every step above is the **library-global** verdict —
is this change breaking for *someone*. A user asking about one specific
application, plugin, or host is asking a different question — does it break
**this** consumer — and that answer legitimately diverges from the global
one in both directions: globally `BREAKING` but this consumer never touched
the removed symbol, or globally `COMPATIBLE` but this consumer's required
entrypoint is gone.

Take this branch only when the user names (or clearly implies) one
consumer, and only when evidence actually supports scoping to it. Otherwise
report the global verdict from step 5 and stop there.

- **An application or library that links the subject** — scope with
  `--used-by CONSUMER` (repeatable), evidence-driven: the consumer binary is
  read and its actual imports become the scope.
- **A plugin/host boundary** — scope with `--required-symbol SYM`
  (repeatable) or `--required-symbols FILE`, declaration-driven: the caller
  states the contract, because a host's requirement is not recoverable from
  the plugin's own imports.

Exact invocations for both: [the abicheck adapter](references/abicheck-adapter.md#named-consumer-invocations).

**Read the verdict the right way round.** On a scoped run, the top-level
`verdict` is promoted to the *scoped* answer — the CLI puts it there because
it is what a gate acts on — and `full_verdict` carries the library-wide
result from step 5, preserved separately on every scoped run whether or not
it differs. So **compare the two fields**; they diverge exactly when
`full_verdict != verdict`, and that divergence is usually the entire point
of having asked the narrower question. Report both, explicitly labelled,
whenever they differ. When the user's question implies both answers matter
(e.g. "is this safe to ship, and specifically will `myapp` still work"),
answer both rather than only the narrower one.

State the residual uncertainty this branch always carries: an import scan
cannot see a symbol resolved via `dlopen`/`dlsym`/`GetProcAddress` or a
plugin registry, so "not in the scanned set" is not "unaffected" — see
[consumer scoping](../shared/consumer-scoping.md) for this and the other
failure modes (an unresolvable consumer, a required symbol missing on both
sides, multiple consumers in one run) that must be stated rather than
papered over.

## Step 8 — Recommend the least disruptive remediation

For each root cause from step 6 (or each affected consumer from step 7),
recommend a fix — from
[the remediation catalogue](../shared/remediation-catalog.md) for the
break-family-to-fix mapping, and
[the remediation pattern reference](references/remediation-patterns.md)
for the deeper design vocabulary (pImpl, reserved slots, versioned
interfaces, capability negotiation, deprecation lifecycles) and their
costs — for the cause, not per symptom. State the cost of the option you
recommend; there is no free pattern.

When there is no compatible path, say so plainly and lay out the honest
options — ship a major/SONAME bump, keep both surfaces, or do not make the
change — rather than reaching for a suppression as a fourth option
([policies and suppressions](../shared/policies-and-suppressions.md)).

## Step 9 — Apply a remediation only with authorization, then rebuild and rerun

You may propose a remediation and, with explicit confirmation for the
specific edit, apply it
([safety invariants](../shared/safety-invariants.md) item 8). You may never
end here. Rebuild both sides under the same profile
([compiler and build profiles](../shared/compiler-and-build-profiles.md)),
re-run the **identical** comparison from step 4, and report the new result.
"This should now be compatible" without a re-run is not a finished
remediation step — see safety invariants' closing rule.

## Step 10 — Report the decision, proof, and remaining uncertainty

### The decision you report

End with this shape, not a command transcript:

| Field | Content |
|---|---|
| **Decision** | one of the five states below |
| **Contract reviewed** | binary / source / runtime — from step 1; for runtime, name which half (symbol-version floor vs. dependency-graph loadability) was actually reviewed |
| **Baseline** | what users actually have, or were built against — from step 2 |
| **Profiles** | compiler, target, language standard, ABI-affecting flags — from step 3 |
| **Evidence** | artifacts and evidence depth actually reached, coverage limitations — from steps 4–5 |
| **Root causes** | the smallest set of underlying changes, not a flat symptom list — from step 6 |
| **Affected consumers** | the global answer, and — only when asked and provable — named applications/plugins — from step 7 |
| **Recommended action** | compatible redesign, restore the old entry point, rebuild consumers, pin the old library, or a version/ABI-epoch change — from step 8 |
| **Verification** | what was rerun after a remediation, and the resulting outcome — from step 9 |
| **Remaining unknowns** | dynamic symbol resolution, unrun evidence depths, shallow coverage, behavioural risk this workflow does not check |

**Decision** is one of:

| Decision | Real abicheck verdict | Meaning |
|---|---|---|
| `VERIFIED_COMPATIBLE` | `NO_CHANGE` or `COMPATIBLE` | no ABI or source break found, at the evidence depth actually reached |
| `COMPATIBLE_WITH_DEPLOYMENT_RISK` | `COMPATIBLE_WITH_RISK` | no break, but a risk finding remains — most commonly a raised runtime/symbol-version floor |
| `SOURCE_BREAK` | `API_BREAK` | consumers must recompile; no already-compiled binary is affected |
| `BINARY_BREAK` | `BREAKING` | an already-compiled, already-linked consumer stops working without a rebuild |
| `NOT_VERIFIED` | `verdict: null` (comparability refused), or an evidence tier short of what step 4 required, or a combination outside this skill's [candidate evaluation v0.1 scope](#candidate-evaluation-scope-v01), or a runtime contract's dependency-graph-loadability half when no `deps tree`/`deps compare` was run | the question was not actually answered — state why, and what would answer it |

A *raised symbol-version floor* is **not** a "remaining unknown": `compare`
emits `runtime_floor_raised` as a risk and can promote it to a break against
declared floors, so read it from the findings and fold it into the decision
rather than deferring it. What is genuinely out of scope of this answer —
say so explicitly — is behavioural compatibility, packaging, and the wider
dependency graph (`abicheck deps compare`).

Optionally, once a decision exists, offer to wire the same check into CI:
[CI wiring](../shared/ci-wiring.md). Offer; do not do it
unasked.

## Termination criteria

The job is done when:

- a decision exists from a real comparison (not a refused one), **or** the
  refusal is reported as its own `NOT_VERIFIED` outcome with its
  remediation;
- every claim traces to a finding and an evidence tier;
- anything unverified is stated as unverified, including anything outside
  this skill's candidate evaluation v0.1 scope;
- if a remediation was applied, the **same** comparison was re-run and the
  new result reported (step 9).

It is not done because a plausible explanation was produced. See
[safety invariants](../shared/safety-invariants.md).
