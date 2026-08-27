# G43 — Safe projection: inferred (TU-to-target) evidence attribution

## Problem

PR #860 correctly rejects `evidence.projection: inferred`/shared evidence
packs today — `check-project.yml` explicitly errors when a target's
declared evidence is anything other than `projection: declared`, because
nothing yet filters an inferred pack's translation units by target
attribution before analysis, and forwarding it unfiltered would let one
target's evidence incorporate every other target's facts too
(`check-project.yml`'s own inline comment, confirmed present at the review's
base commit). `build-output.json` documents the identical constraint.

This fail-closed behavior is correct and must not be relaxed without the
real fix. It is, however, expensive for a large multi-target project:
today every target must publish its own separately-filtered evidence pack,
even when a single shared build already produced facts for every target in
one pass.

## Goal & acceptance criteria

Build a real translation-unit-to-target attribution chain so a single
shared, build-wide evidence pack can be safely filtered *at consumption
time* per target, with the same safety guarantee `projection: declared`
already provides — no target ever sees another target's facts — instead of
requiring N separately-published packs for N targets.

Attribution chain:

```
translation unit
  → compile action
  → object/archive member
  → link action
  → produced DSO
  → project target
```

The pack must record, and a consumer must be able to check without
re-deriving:

- selected translation units (attributed to this target);
- rejected/unattributed units (evidence present but ownership unresolved);
- ambiguous ownership (a TU whose attribution genuinely cannot be resolved
  to exactly one target — e.g. a header-only utility object linked into
  several DSOs);
- target coverage (what fraction of the target's own compile units were
  successfully attributed);
- attribution source and confidence (compile-DB derived, link-command
  derived, Bazel/CMake-file-API derived, etc., since different producers
  give different-quality evidence for this).

### Acceptance test

One shared, build-wide pack covers `core`, `math`, and `strings`. A
source-only change in `strings` must not enter either the `core` or `math`
source snapshot. Removing target attribution (or degrading it below a
usable confidence) must produce an assurance/operational failure — never a
shallow green result that silently fell back to "no filtering."

## Design

This is the generalization of the TU→link-unit→DSO attribution core G30
already built for a narrower purpose (see
[ADR-053](../adr/053-tu-link-unit-dso-attribution.md), referenced from
G30's own plan as "P2's first slice... implemented, with pipeline wiring
still open"). Read that ADR and G30's P2 section before designing this —
the attribution *core* (TU → link-unit → DSO) may already answer most of
what this plan needs; what's additionally required here is the last hop
(DSO → *project target*, a `.abicheck.yml`-level concept G30's core has no
reason to know about) and the consumption-time filter that actually gates
`check-project.yml`'s evidence routing on the result.

Consumption-time filtering means: given a shared pack and a target id,
compute the attributed TU set for that target, and construct (or mark as
usable) a *view* of the pack scoped to exactly that set — the same
guarantee a separately-published `projection: declared` pack gives, derived
instead of pre-published. `check-project.yml`'s existing
`projection == 'inferred'` rejection (see its own comment, quoted in the
Problem section) becomes: accept `inferred` only when the pack also carries
a resolved attribution manifest meeting a minimum confidence/coverage
threshold for the requested target; otherwise keep rejecting exactly as
today.

Ambiguous ownership must never silently resolve to "include it" — an
ambiguous TU is evidence the attribution is incomplete for *every* target
it might belong to, and should either widen the pack's own reported
coverage gap (visible in the assurance contract from G41 Phase 3) or, at
minimum, never silently count toward any one target's completeness.

## Files & surfaces

- ADR-053 and `abicheck/buildsource/`'s existing TU→link-unit→DSO
  attribution core (per G30's plan — locate the actual module(s) before
  writing new code; this is very likely an extension, not a new subsystem).
- A new DSO→target resolver, likely living alongside
  `abicheck/buildsource/project_targets.py` (the module that already knows
  what a "project target" is) rather than inside the build-evidence
  collection layer itself — the attribution core should stay agnostic to
  `.abicheck.yml`'s vocabulary.
- `check-project.yml` — the `projection: inferred` rejection gains its
  attribution-confidence-gated exception.
- `abicheck/buildsource/build_output.py` — pack manifest fields for
  attribution source/confidence/coverage, mirroring the shape G41 Phase 1's
  baseline-manifest fields use for a different axis (reuse the pattern, not
  the fields).

## Tests

- Unit tests on the DSO→target resolver against a synthetic multi-target
  build graph (unambiguous, ambiguous, and unattributed TU cases).
- An `integration` fixture building `core`/`math`/`strings` as one shared
  build, publishing one evidence pack, and asserting per-target filtering
  matches the acceptance test above exactly.
- A regression test asserting a pack with attribution confidence below
  threshold (or none at all) is still rejected the same way an
  unconditionally-`inferred` pack is today — this fix must never regress
  the existing fail-closed behavior for the common case where attribution
  genuinely isn't available.

## Effort & risk

L. The riskiest part is not the filtering logic — it's building a *correct*
DSO→target resolver across enough build-system shapes (CMake, Bazel, Make,
a plain compile database with no link-graph information at all) that "no
attribution available" is the honest, common answer for at least one real
build system, and the fail-closed default must degrade gracefully to that
rather than guessing. Do not attempt to infer attribution from naming
heuristics (a file path convention, a directory prefix) — that reintroduces
exactly the "accidentally shared" risk this whole feature exists to close,
just moved from evidence-pack scope to attribution-source scope.

## Out of scope

- Relaxing `projection: inferred`'s rejection for any build system this
  plan cannot build a real, verified attribution resolver for — a partial
  fix that "usually works" is worse than today's fail-closed default,
  which is honest about the gap.
- Retrofitting historical (already-published) evidence packs that predate
  attribution manifests — this only applies going forward.
