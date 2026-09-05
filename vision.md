# abicheck — vision

This is the canonical statement of what abicheck is for and where it is going.
The README, the documentation landing page, and the package metadata
summarize it; technical decisions live in the [architecture decision
records](https://abicheck.github.io/abicheck/contribute/adr/), work in
progress lives in the [implementation plans](https://abicheck.github.io/abicheck/contribute/plans/),
and the shipped contract is whatever the code, schemas, and CLI help say
today. When this document and one of those disagree about *current*
behavior, the code is right and this document describes direction.

## Purpose

**abicheck helps library and package maintainers understand and validate
API/ABI evolution.** Grounded in ABI/API compatibility analysis, it makes
additions, removals, modifications, relevant dependency changes, and
deployment requirements visible; relates them to declared contracts and
known consumers; and supports intentional, reviewable change in CI while
showing what the available evidence could not establish.

Compatibility analysis is the foundation, not one feature among many. The
expansion is from *detecting incompatibilities* to *making the evolution of
a supported surface visible, intentional, and traceable*. A compatible
addition is a real change to a maintained contract and deserves to be seen
and reviewed, not folded into "nothing happened". An intentional break is
still a break: a release policy can accept it, nothing can make it
compatible.

The experience this is aiming at, in the maintainer's words:

> I can see how my library's supported surface changed, understand the
> consequences, confirm that the changes were intended, and apply my
> project's compatibility and versioning policy, with a clear account of
> what was and was not checked.

abicheck does not promise to discover every behavioral change from a static
scan, to prove arbitrary runtime behavior, or to infer human intent. It
promises to report the surface evolution its evidence supports, with the
coverage stated.

## Who it is for, and what success looks like

The primary users are maintainers of C and C++ libraries and of the
packages that ship them. The main delivery model is continuous integration:
a check that runs on every pull request and every release candidate, with
the GitHub Action as the easiest onboarding route and identical semantics
available through the CLI and the typed Python API for any other CI system.

Success is recognition and sustained use in large, well-known projects,
because the tool tells those projects things they trust and act on. A
larger detector count is not success on its own.

## Core model

Three surfaces coexist, each with its own provenance:

- the **declared** surface: what the project says it supports (public
  headers, export manifests, package metadata, a declared contract);
- the **observed** surface: what the shipped artifacts actually carry
  (exports, layouts, dependencies, runtime floors);
- the **consumed** surface: what known consumers actually use.

These answer different questions and are not ranked by a universal priority.
A conflict between them (a public declaration that is not exported, an
internal export a known consumer depends on, a package tag that contradicts
a contained binary) is reported with its sources, not resolved by erasing
one of them.

One semantic model serves one component or many. Comparing two binaries,
comparing two packages, and validating a whole CI build matrix use the same
identities, the same change detection, the same policy, and the same report
document. Cardinality changes the scope, never the product. Package-level
and release-level contracts apply only when that scope is selected: a bare
binary comparison never needs package metadata, and a developer checking
one local build against the matching member of a multi-variant baseline is
not asked to build the other variants, nor told they were removed.

Six facts about one change stay separate because each has its own
consumer: contract violation, observed change, known-consumer impact,
evidence completeness, acknowledgment of intent, and CI acceptance.

## The everyday experience

The standard workflow is a binary plus its public headers on each side, run
in CI. Standalone binaries are first-class inputs, even stripped ones.
Debug information is consumed when present and is never a prerequisite.
Baselines are reusable snapshots that a release publishes and later checks
consume. Build and source evidence, and known consumer artifacts, are
optional additions that buy more assurance on the pull requests that need
it; source-level analysis is a normal part of pull request review, not
something reserved for releases. Adding an optional input must never make
the ordinary path harder.

## Evidence and trust

Evidence is layered, from the binary alone through debug information,
public headers, build data, and sources, with a derived source graph on top
(L0 to L5). More evidence lets the tool prove more and raise fewer false
alarms. Less evidence narrows what can be concluded; it does not invent
declarations, confident exclusions, or breaks.

For every area the report touches it should be clear whether evidence was
available, unavailable, unsupported for this input, not applicable, not
requested, or collected and then failed. A failed extraction is never
reported as an empty surface. A comparison with no debug information keeps
its symbol and declaration results and says which compiled-layout checks
went unverified. A strict project may require certain evidence and fail
its assurance gate when it is missing, without that gate manufacturing an
ABI finding.

A missed real break costs more trust than a false alarm, in pull request
and release workflows alike, so uncertainty is reported as uncertainty and
never upgraded to a clean compatibility claim. Intentional cross-profile
comparisons (another compiler, other flags, another dependency set) are
supported where the compared contract still makes sense: comparable
dimensions are evaluated and profile differences are explained separately
from surface changes, with neither blanket rejection nor blanket acceptance.

## Change governance and evolution

What was detected and what policy allowed are two different totals, and the
report keeps both. Suppressions, scope exclusions, reclassifications, and
deduplication each have a name and a reason; the audit distinguishes an
intentional acceptance, a claimed false positive, a change outside the
declared contract, a reclassification, and mere presentation filtering. A
summary such as "one hundred removals detected, all suppressed by one rule"
must remain visible in a compact report and on a passing check. When
configuration disables a detector or a scope before anything is detected,
the report says that coverage was disabled; it never fabricates a count of
suppressed changes.

Changes can be acknowledged with explicit, reviewable context bounded to
specific findings, components, and release ranges. Whether an
unacknowledged public addition is allowed, warned about, or blocked is a
project setting. A baseline refresh or a broad ignore rule is not an
acknowledgment of everything it happens to cover.

Versioning is a project-defined policy. Some projects promise strict
semantic versioning; some use version numbers without that promise; some
allow breaks only at named release boundaries. The version scheme, the
compatibility promise, the support and deprecation windows, and how strictly
deviations are enforced are separate settings. Changing enforcement
strictness can change whether a release is accepted; it never changes what
was observed. SONAME and version-bump advice stays useful and stays
conditional on the platform and the contract.

Longitudinal tracking is the direction this leads: when did an API first
appear in an observed release, when was it deprecated, when was it removed,
and does that history satisfy the project's stated promise. History is
built from the immutable snapshots a project already keeps, and it is
honest about gaps: "first observed in 2.4" is not "introduced in 2.4" when
2.3 was never captured, and adjacent passing comparisons do not by
themselves prove compatibility across a whole support window.

## Scope and priorities

Linux ELF with C and C++ toolchains is the first priority and the canonical
validation lane, with the same core semantics and honestly reported
capability on Windows and macOS. SYCL and other heterogeneous C++ stacks are
part of the C++ scope. CPython extension modules and the scientific Python
stack are the next optional provider domain, not a change of identity.
Header-only libraries are intended scope: their source contract should be
comparable through the normal pipeline with its capability limits stated,
and without manufacturing a placeholder binary. Static archives merit a
bounded, lower-priority investigation into which questions can honestly be
answered; today they remain unsupported input, and that is a current
limitation rather than a permanent exclusion. Kernel-specific debug formats
and similar niche domains stay lower priority unless a real need arrives.

## What exists today and what is direction

Shipped today: two-sided comparison of binaries, snapshots, release
directories, and packages; the layered evidence model with explicit
coverage reporting; contract-aware evaluation; policies, suppressions, and
severity gating; application and plugin checks against supplied consumers;
multi-profile aggregation; typed requests and one engine shared by the
Action, CLI, and Python API, so equivalent *resolved* inputs give the same
answer (the CLI and Action additionally fold in a discovered project
config, a run profile, and packs that a bare API call does not — full
configuration-resolution parity is direction, not shipped); and the
release recommendation.

Direction, in whole or in part: comparison-scope and completeness semantics
for partial matrices and package inventories; a detected-versus-effective
audit that survives compact reporting; bounded change acknowledgment;
configurable versioning policy and longitudinal history; a prebuilt-consumer
lifecycle; header-only comparison as a first-class task; and task-oriented
report views over the one canonical result. Each is tracked by an ADR or
plan; none is claimed as delivered here.

## Durable principles

- Analyze the scope the user selected; never require inputs that scope does
  not need, and never treat an unselected or unproduced artifact as a
  removed one.
- Record observed changes before policy disposition, and keep them
  auditable after suppression, classification, acknowledgment, and
  rendering.
- Weaker evidence narrows conclusions; it never creates findings, and a
  failure is never an empty result.
- Policy, intent, and versioning strictness decide acceptance. They do not
  decide facts.
- One component or many: one model, one implementation, one report.
- Rendering and layout choices can reorder or collapse detail; they cannot
  hide critical incompleteness or suppression summaries, and they cannot
  change a verdict, a gate, or an exit code.

Contributors use this document to decide *direction*: whether a proposed
capability belongs and which existing model it must extend. It is not
permission to change a default, an exit code, or a public interface.

## Signals of success

Qualitative signals, in rough order of importance:

- **Trusted in CI.** Well-known projects keep the check enabled on every
  pull request, and a red result is investigated rather than overridden.
- **Additions are reviewed.** Maintainers use the report to see and
  acknowledge growth of their supported surface, not only breaks.
- **Coverage is legible.** Users can say from a report alone what was and
  was not checked, without reading the source.
- **Policy is accountable.** A passing check with suppressed findings is
  recognizably different from a passing check with none.
- **No manufactured findings.** Partial inputs, missing evidence, and
  cross-profile comparisons produce qualified results, not fabricated
  breaks or fabricated safety.

Any numeric target attached to these (adoption counts, accuracy on a
benchmark, false-negative rates on a corpus) is a proposal to be agreed in
its own plan, not a commitment recorded here.
