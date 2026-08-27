# G42 — Explicit check identity, named deployment environments, and environment-aware system-provider resolution

## Problem

An external upstream-only review (base commit
`327df7b5616bcfaea8c330aad418b796c17f3970`, PRs #860/#883 merged) found three
related gaps in the declarative project layer, all downstream of the same
missing concept: **the project schema has no first-class notion of "which
deployment/runtime context is this check evaluated against."**

1. **Check identity is too coarse.** A check's identity is fixed to
   `target@profile#channel@depth` (`abicheck/buildsource/run_plan.py`'s
   `RunPlanCheck`). Two checks sharing that tuple but differing in analysis
   *method* (replay vs. Clang-plugin evidence), *policy* (strict ABI vs.
   plugin policy), *environment* (RHEL 8 vs. Ubuntu 24 deployment), or
   *assurance* requirement cannot both be declared without colliding on the
   same generated `check_id`.
2. **No named deployment/runtime environments.** The check schema covers
   `channel`, `depth`, required status, gate mode, profile selection, and
   new-target handling, but nothing names a deployment environment a
   runtime-floor check is evaluated against. Runtime-floor testing (glibc
   floor, symbol-version floor — see G10, done) stays an invocation-level
   concern rather than a declared project contract with a name and a
   digest that shows up in the report.
3. **System-provider classification is a static basename allowlist.** PR
   #883 broadened `DEFAULT_SYSTEM_PROVIDERS` (oneTBB/oneMKL/Intel-runtime/
   Level Zero) but explicitly left the real fix — resolving each external
   dependency against the *declared* environment/sysroot — undone. A global
   basename list cannot tell "shipped with the product" from "supplied by
   the deployment environment" from "present but too old" from "missing the
   required symbol version" from "excluded by an accidental naming
   collision."

All three point at the same missing primitive: a **named environment**,
declared once, referenced by id from a check, carried through the run plan
and report as a first-class value with its own digest — not a Boolean flag,
not a workflow-global input, not a basename list baked into the tool.

## Goal & acceptance criteria

1. A project can declare two checks for the same `target`/`profile` that
   differ only in analysis method/policy/environment/assurance, and both run
   and report under distinct, non-colliding identifiers.
2. A project can declare named environments (`environments:` block, each
   naming a runtime matrix — e.g. a glibc floor, symbol-version floor,
   available system libraries) and reference one by id from a check; the
   environment id and its digest appear in the run plan, the effective
   configuration, the report envelope, and the aggregate matrix.
3. Evaluating the same runtime-floor change against multiple named
   environments does **not** re-trigger a binary/header/source extraction
   per environment — extract/diff once, evaluate the one result against N
   environments.
4. System-provider resolution consults the selected environment/sysroot
   (presence, SONAME, export, symbol version, runtime floor) rather than
   relying solely on the static basename allowlist; an unresolvable
   provider produces an explicit incomplete-coverage result, never a
   silent "must be system" classification.

### Acceptance tests

- **Check identity**: one `check-project.yml` invocation runs two
  source-depth checks for the same target/profile — one replay-evidence,
  one Clang-plugin-evidence — producing separate reports, separate
  aggregate entries, and a conformance result with no `check_id` collision.
- **Environments**: the same runtime-floor change evaluates as risk with no
  declared environment, breaking against an old deployment floor, and
  compatible against a sufficiently new one — all three distinguishable in
  one project aggregate, computed from a single extraction/diff pass.
- **Provider resolution**: a project declaring an environment whose sysroot
  lacks a required provider version reports an explicit incomplete-coverage
  finding for that dependency edge, not a silent system-provider
  classification; a project whose environment does carry a sufficient
  provider resolves cleanly.

## Design

### Explicit check identifiers

Add an optional, project-owned logical id:

```yaml
checks:
  - id: l4-plugin-rhel8
    channel: accepted-main
    depth: source
    analysis:
      evidence: clang-plugin
      policy: strict_abi
      environment: rhel8
      assurance: complete
```

Generated identity stays backward-compatible
(`target@profile#channel@depth`) when `id` is absent; when present, the
generated identity gains a suffix derived from `id` so existing consumers of
the un-suffixed form are unaffected. The `analysis:` block is one nested,
named object (or a reference to a named preset resolved the same way
`environments:` below resolves) — not another flat family of
workflow-global inputs growing on `check-project.yml`. `evidence` selects
which extraction path produced the facts this check consumes (see G39's
per-finding evidence-provider model for the underlying vocabulary);
`policy`/`assurance` reference the already-existing policy-profile and
G41-Phase-3 assurance mechanisms respectively — this plan adds the
identity slot they're selected through, not a second copy of either
mechanism.

### Named environments

```yaml
environments:
  rhel8:
    matrix: ci/environments/rhel8.yaml
  ubuntu24:
    matrix: ci/environments/ubuntu24.yaml
checks:
  - id: rhel8-runtime
    environment: rhel8
```

`matrix:` points at the existing runtime-floor/env-matrix format G10 already
established (`--env-matrix`'s `runtime_floors`, `platform_baseline_floor_raised`)
— this is a *naming and referencing* layer over that existing mechanism, not
a new runtime-floor model. The environment id and a digest of its resolved
matrix content must show up in: the run plan (`RunPlanCheck`), the effective
configuration receipt, the report envelope, and the aggregate's
profile/evaluation matrix — the same "resolved value plus its digest, both
persisted" shape `comparability.py`'s fingerprints and G34's
`consumer_compile` projection already use, applied to a new axis.

**Efficiency constraint, load-bearing**: environment evaluation must not
trigger a new binary/header/source extraction per environment. The
architecture is extract/diff once, then evaluate the *same* `DiffResult`
against N declared environments — each environment only changes which
runtime-floor/system-provider facts are checked against the findings
already produced, never re-running `dump`/`compare`'s extraction stages.
This mirrors G34 Phase D's existing per-profile finding-matrix
reconciliation (`aggregate`'s `finding_matrix` block) — reuse that
reconciliation shape for "same finding, N environments" rather than
inventing a parallel one.

### Environment-aware system-provider resolution

Today (`abicheck/bundle.py`'s `DEFAULT_SYSTEM_PROVIDERS` plus PR #883's
oneTBB/oneMKL/Intel-runtime/Level-Zero broadening) provider classification
is a static basename allowlist — necessarily a coarse fallback, since it
cannot see what's actually available at deployment time. With a named
environment now resolvable to a sysroot/runtime matrix, resolve each
external dependency edge against it:

- provider presence (does the environment's sysroot/package set carry a
  library with this SONAME at all);
- provider SONAME (does it match what the binary's `DT_NEEDED` names);
- export presence (does the environment's copy export the symbol the
  binary imports);
- symbol version (does the environment's copy satisfy the required
  `GLIBC_x.y`-style version);
- runtime floor (does the environment's declared floor cover what the
  binary requires).

The static `DEFAULT_SYSTEM_PROVIDERS` allowlist becomes a **fallback
classification aid** for when no environment is declared (today's
behavior, unchanged for a project that doesn't opt in), not the source of
truth once an environment is. An unknown/unresolvable provider state
produces an explicit incomplete-coverage result — the same "fail closed
into a distinct, named failure class" pattern G41 Phase 3 establishes for
assurance — rather than silently classifying as "system" or disappearing
from the report.

## Files & surfaces

- `abicheck/buildsource/run_plan.py` — `RunPlanCheck`: new `check_id`
  (explicit, optional), `environment_id`, `environment_digest`,
  `analysis_evidence`/`analysis_policy`/`analysis_assurance_requirement`
  fields, following the exact structural precedent `consumer_compile_*`
  already set (see G34 Phase 0).
- Project schema (wherever `.abicheck.yml`'s `checks:`/`environments:` are
  validated — near `abicheck/buildsource/project_targets.py`) — new
  `environments:` top-level block, new `id`/`analysis:`/`environment:` check
  fields.
- `abicheck/bundle.py` / a new sibling module for environment-aware provider
  resolution (a dependency-edge resolver consulted from bundle analysis,
  not a rewrite of `compare_bundle` itself).
- `.github/workflows/check-project.yml` — per-cell environment id/digest
  forwarding into the report envelope.
- `abicheck/cli_aggregate.py` — environment axis in the profile/evaluation
  matrix, reusing G34 Phase D's `finding_matrix` reconciliation shape.

## Tests

- Schema validation tests for `environments:`/`id`/`analysis:` (valid,
  missing-reference, duplicate-id cases).
- A unit test proving one extraction/diff pass evaluated against N declared
  environments produces N distinguishable verdicts with no additional
  `dump`/`compare` invocation (assert on a call-count mock, not just on the
  output shape — this is the property most likely to silently regress).
- Provider-resolution unit tests: presence/absent, SONAME mismatch, version
  floor met/unmet, each against a hand-built environment matrix fixture.
- End-to-end `integration` fixtures for both acceptance tests above.

## Effort & risk

L, phased:

- Check identity (M): schema + `RunPlanCheck` field + aggregate
  disambiguation; low architectural risk, mostly additive.
- Named environments (M): schema + digest plumbing + the "evaluate once
  against N environments" reconciliation; medium risk in ensuring the
  extract-once invariant is actually enforced rather than merely intended.
- Provider resolution (L): the resolver itself is new logic against
  real sysroot/environment data, and needs real multi-environment fixtures
  (RHEL 8 vs. Ubuntu 24 class of difference) to validate against, which may
  not all be available in every development/CI environment — treat missing
  fixture environments as a documented gap rather than skipping the
  acceptance test silently.

## Out of scope

- Redesigning G10's runtime-floor/env-matrix format itself — this plan adds
  naming/referencing and digesting on top of it, not a new floor model.
- A general per-edge dependency-resolution engine beyond system providers
  (e.g. resolving a project's *own* sibling libraries against an
  environment) — that's bundle-internal linkage, tracked separately in G38.
