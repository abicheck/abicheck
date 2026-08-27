---
doc_type: contributor
level: expert
lifecycle: active
generated: false
---

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

**Generated identity — a plain trailing suffix breaks parsing, confirmed by
reading `workflows/aggregate/contracts.py` directly.** `_CHECK_ID_RE` (the
regex `parse_check_id()` matches every `target_id` against for
profile/finding-matrix grouping) is *end-anchored* on the depth segment:
`` @(?P<depth>binary|headers|build|source)$ ``. Appending anything after
the generated `target@profile#channel@depth` string — as an earlier draft
of this plan proposed — makes the whole id fail that match, so
`parse_check_id()` silently returns `None` and the check drops out of
every profile/finding-matrix grouping this plan's own acceptance test
depends on. The fix has to extend the regex itself, not merely produce a
string and hope it still parses:

```
_CHECK_ID_RE = re.compile(
    r"^(?P<target>.+)@(?P<profile>[A-Za-z0-9][A-Za-z0-9._-]*)"
    r"#(?P<channel>[A-Za-z0-9][A-Za-z0-9._-]*)"
    r"@(?P<depth>binary|headers|build|source)"
    r"(?:~(?P<explicit_id>[A-Za-z0-9][A-Za-z0-9._-]*))?$"
)
```

— an optional, non-capturing-when-absent `~<id>` tail (the `~` is not
already produced by `build_check_id`/`_IDENTIFIER_RE`, so it can't collide
with an existing target/profile/channel value), with `CheckIdParts` gaining
a matching `explicit_id: str | None` field. Absent `id`, the generated
string and its parse are bit-for-bit unchanged (`explicit_id=None`) — this
is the backward-compatibility guarantee, verified against the actual
regex now, not assumed. `abicheck/workflows/aggregate/contracts.py` (the
`_CHECK_ID_RE`/`CheckIdParts`/`parse_check_id()` definitions) is therefore
part of this plan's own required file surface, not merely a downstream
consumer to leave alone.

**`contracts.py`'s regex is not the only gate a `~<explicit_id>` string
must pass, and updating it alone is insufficient — confirmed by reading
the actual production call chain, not assumed.** `build_check_id()`
(`abicheck/buildsource/check_report.py`) is what actually *constructs* the
`check_id` string, and it ends by calling `validate_check_id(check_id)`
(`abicheck/checker_types.py`) before returning — so a builder that simply
appended `~<id>` to its result would raise `ValueError` at construction
time, long before `contracts.py`'s parser ever saw the string.
`validate_check_id()` matches against `CHECK_ID_PATTERN`, which is itself
end-anchored on the depth segment
(`` @(binary|headers|build|source)$ ``) — the identical anchoring bug the
"Generated identity" paragraph above already diagnosed for `_CHECK_ID_RE`,
just one module earlier in the pipeline. The JSON report schema
(`abicheck/schemas/compare_report.schema.json`, `check_id` property's
`pattern`, mirrored to `docs/reference/schemas/v1/compare_report.schema.json`
via `scripts/publish_schemas.py`) carries the byte-identical anchored
pattern as its own independent copy, per `CHECK_ID_PATTERN`'s own comment
("Mirrors the `pattern` in compare_report.schema.json's `check_id`
property"). All three must extend in lockstep, or the feature fails at a
different point depending on which layer runs first:

- `checker_types.CHECK_ID_PATTERN`/`validate_check_id()` — extend with the
  identical `(?:~[A-Za-z0-9][A-Za-z0-9._-]*)?` optional tail
  `_CHECK_ID_RE` above adds, so a builder-constructed id with an explicit
  suffix passes the same fail-fast check `reporter._add_check_identity`
  already relies on production code hitting (not just the JSON Schema,
  which "production code never runs against" per this function's own
  docstring).
- `build_check_id()` — gains a new, optional `explicit_id: str | None`
  parameter, appending the validated `~<explicit_id>` tail to the
  generated string before the existing `validate_check_id(check_id)` call
  (not after — the whole point is that the *same* validator must accept
  the extended string, not a second, separately-validated concatenation).
- `abicheck/schemas/compare_report.schema.json`'s `check_id` property
  `pattern` — extended identically, plus a schema version bump per this
  file's own versioning convention, with `docs/reference/schemas/v1/
  compare_report.schema.json` re-synced via `scripts/publish_schemas.py`
  in the same change (not left to drift until the next unrelated schema
  edit notices it).

Without all three, a project author supplying `id: l4-plugin-rhel8` would
either fail at `build_check_id()` (if only `contracts.py` and the schema
were extended) or produce a `check_id` the schema itself rejects on
validation (if only the builder and parser were extended) — this plan's
own acceptance test for explicit check identifiers must exercise the full
construct → validate-against-schema → parse chain, not just the parser
in isolation, to catch either half being missed. The `analysis:` block is
one nested,
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
— this is a *naming and referencing* layer over that existing mechanism for
the runtime-floor axis specifically, **but that format cannot, by itself,
carry what the provider-resolution phase below needs.** Confirmed by
reading `abicheck/environment_matrix.py` directly:
`EnvironmentMatrix`/`_KNOWN_TOP_LEVEL_KEYS` recognizes exactly
`compilers`/`abi_version`/`libstdcxx_dual_abi`/`sycl`/`cuda`/`target_os`/
`target_arch`/`runtime_floors` — no sysroot path, no provider/package
inventory, and an unrecognized key is only warned about
(`_warn_unknown_keys`), never rejected, so a hand-added `providers:`/
`sysroot:` section today would silently do nothing. This plan must
therefore extend `EnvironmentMatrix`'s own schema (a new top-level section,
e.g. `providers:` naming a sysroot path plus, per provider, expected
SONAME/export/symbol-version facts) alongside the naming/referencing layer
`environments:` adds — without this schema extension, every
environment-aware provider lookup in the next section degrades to
incomplete coverage for lack of any real presence/SONAME/export/version
input to resolve against, which is a correctness gap, not a missing nice-
to-have. The environment id and a digest of its resolved matrix content
(runtime floors *and* the new provider section together) must show up in:
the run plan (`RunPlanCheck`), the effective configuration receipt, the
report envelope, and the aggregate's profile/evaluation matrix — the same
"resolved value plus its digest, both persisted" shape `comparability.py`'s
fingerprints and G34's `consumer_compile` projection already use, applied
to a new axis.

**Efficiency constraint, load-bearing**: environment evaluation must not
trigger a new binary/header/source extraction per environment — extract/
diff exactly once, then evaluate the *one* resulting `DiffResult` against
N declared environments, never re-running `dump`/`compare`'s extraction
stages. This mirrors G34 Phase D's existing per-profile finding-matrix
reconciliation (`aggregate`'s `finding_matrix` block) — reuse that
reconciliation shape for "same finding, N environments" rather than
inventing a parallel one.

**"Evaluate the same `DiffResult`" must not mean "call the same in-place
mutator on the same `Change` objects N times" — confirmed a real bug in
that shape by reading `diff_versioning.apply_runtime_floor_contract()`
directly.** It mutates each `Change.effective_verdict` in place and
explicitly skips any finding that already carries a modulation (`if
change.effective_verdict is not None: continue` — the same "findings
already carrying a modulation are left untouched" contract its own
docstring documents, correctly, for its *existing*, single-environment
callers). Calling it a second time for a second environment therefore
does nothing for any finding the first environment already stamped — a
finding evaluated as `BREAKING` under an old runtime floor stays
permanently `BREAKING` once a newer floor is checked next, even if the
newer floor would correctly reclassify it `COMPATIBLE`. The extract/diff-
once requirement stands, but "evaluate against N environments" needs a
pristine `Change` list (or an unmodified raw-result stage) per
environment — e.g. deep-copying the kept `Change` list (or resetting
`effective_verdict`/`modulation_reason`/`modulation_rule` to `None`)
immediately before each environment's own
`apply_runtime_floor_contract()`/provider-resolution pass, never sharing
mutated objects across environments. This applies equally to whatever new
system-provider classification function this plan adds in the next
section, if it follows the same "mutate in place, skip if already
modulated" ADR-025 pattern.

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
- **`abicheck/workflows/aggregate/contracts.py`** — required, not optional:
  `_CHECK_ID_RE`'s extended `~<explicit_id>` suffix and `CheckIdParts`'
  matching `explicit_id` field (see "Explicit check identifiers" above) —
  without this, the whole `id:` feature silently breaks profile/
  finding-matrix grouping the moment it's used.
- **`abicheck/checker_types.py` (`CHECK_ID_PATTERN`/`validate_check_id()`)
  and `abicheck/buildsource/check_report.py` (`build_check_id()`) —
  equally required, not optional.** These run *before* `contracts.py`
  ever sees the string: `build_check_id()` calls `validate_check_id()`
  unconditionally, so a `check_id` builder that appended `~<explicit_id>`
  without extending this pattern first would raise at construction time.
  Both need the identical `(?:~[A-Za-z0-9][A-Za-z0-9._-]*)?` extension.
- **`abicheck/schemas/compare_report.schema.json`'s `check_id` `pattern`**
  (plus a schema version bump and the `docs/reference/schemas/v1/`
  re-sync via `scripts/publish_schemas.py`) — an independent copy of the
  same anchored regex per `CHECK_ID_PATTERN`'s own comment; must extend
  in lockstep with the two Python-side validators above or JSON Schema
  validation of a real report becomes the new failure point.
- Project schema (wherever `.abicheck.yml`'s `checks:`/`environments:` are
  validated — near `abicheck/buildsource/project_targets.py`) — new
  `environments:` top-level block, new `id`/`analysis:`/`environment:` check
  fields.
- **The new provider/sysroot section's parser/model — `extract/`/`model/`,
  not `abicheck/environment_matrix.py` directly.** That module is itself a
  `legacy_root_modules` no-growth entry per `architecture/modules.yaml`
  (confirmed), so the new parser (mirroring `_parse_sycl_constraints`/
  `_parse_cuda_constraints`'s existing shape) belongs in `abicheck/
  extract/` (parsing new environment facts) and the resulting value type
  in `abicheck/model/` (a shared value read by the resolver in "Files &
  surfaces" below) — `environment_matrix.py`'s own `EnvironmentMatrix`
  gains only a thin delegating field/property, not the new
  `_KNOWN_TOP_LEVEL_KEYS`/parsing logic itself. Without this new section
  existing *somewhere* real, `matrix:` cannot carry what the provider
  resolver needs and the whole feature degrades to incomplete coverage by
  construction — that requirement is unchanged; only its placement moved.
- **Environment-aware provider resolution — routed through ADR-061's
  canonical package owners, not `abicheck/bundle.py`** (a
  `legacy_root_modules` no-growth entry per `architecture/modules.yaml`):
  reading the environment's sysroot/package facts (presence, SONAME,
  export, symbol version) is "read a build/debug fact," so that extraction
  belongs in **`abicheck/extract/`**; the resolved provider identity is a
  shared value, so it belongs in **`abicheck/model/`**; matching a
  dependency edge against the resolved environment is **`abicheck/
  compare/`**'s job ("match old/new entities or identify a raw change");
  the incomplete-coverage classification this produces is
  **`abicheck/policy/`**'s ("decide relevance, suppression,
  classification... gating"); and **`abicheck/workflows/`** coordinates
  invoking this resolver from bundle/scan analysis. `bundle.py` gains only
  the minimal call site needed to consult the new resolver, not the
  resolution logic itself.
- `.github/workflows/check-project.yml` — per-cell environment id/digest
  forwarding into the report envelope.
- **`abicheck/workflows/aggregate/`** — the environment axis in the
  profile/evaluation matrix, reusing G34 Phase D's `finding_matrix`
  reconciliation shape. This package (confirmed to already own
  `finding_matrix`) is the canonical home per ADR-061's routing table
  ("Coordinate dump, compare, scan, release, aggregate, project, or
  dependency behavior" names `aggregate` explicitly) — `abicheck/
  cli_aggregate.py`, a `frozen_root_families["cli_"]` no-growth entry,
  gains only the thin CLI presentation call, not the reconciliation logic
  itself.

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
- Provider resolution (L): includes the confirmed `environment_matrix.py`
  schema extension (a real prerequisite, not a formality — see "Named
  environments" above) alongside the resolver itself, which is new logic
  against real sysroot/environment data. Needs real multi-environment
  fixtures (RHEL 8 vs. Ubuntu 24 class of difference) to validate against,
  which may not all be available in every development/CI environment —
  treat missing fixture environments as a documented gap rather than
  skipping the acceptance test silently.

## Out of scope

- Redesigning G10's runtime-floor/env-matrix format itself — this plan adds
  naming/referencing and digesting on top of it, not a new floor model.
- A general per-edge dependency-resolution engine beyond system providers
  (e.g. resolving a project's *own* sibling libraries against an
  environment) — that's bundle-internal linkage, tracked separately in G38.
