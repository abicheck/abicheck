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

**This is the pattern's shape before the "Efficiency constraint" section
below adds a second, composed segment — the two must not be designed or
implemented independently.** The full, final pattern this plan requires
— after the environment-grouping work later in this document adds its own
`!<environment_id>` segment — is:

```
_CHECK_ID_RE = re.compile(
    r"^(?P<target>.+)@(?P<profile>[A-Za-z0-9][A-Za-z0-9._-]*)"
    r"#(?P<channel>[A-Za-z0-9][A-Za-z0-9._-]*)"
    r"@(?P<depth>binary|headers|build|source)"
    r"(?:!(?P<environment_id>[A-Za-z0-9._-]+))?"
    r"(?:~(?P<explicit_id>[A-Za-z0-9][A-Za-z0-9._-]*))?$"
)
```

— i.e. `!<environment_id>` before `~<explicit_id>`, both optional and
independently omittable, matching the exact shape "Efficiency constraint"
below specifies. This is the **one** canonical pattern definition this
plan's "Files & surfaces" section requires extended, in lockstep, across
`_CHECK_ID_RE`/`CheckIdParts` (`contracts.py`), `CHECK_ID_PATTERN`/
`validate_check_id()` (`checker_types.py`), `build_check_id()`
(`check_report.py`), and the JSON report schema — an earlier draft of
this plan described the `~<explicit_id>` extension and the
`!<environment_id>` extension in two separate places without ever writing
down the composed result, which is exactly the gap that let a later
review round find the non-`contracts.py` copies still missing the
environment segment. An optional, non-capturing-when-absent `~<id>` tail
(the `~` is not already produced by `build_check_id`/`_IDENTIFIER_RE`, so
it can't collide
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

**A digest of the resolved *matrix* content is not sufficient once
`providers:` names a sysroot path rather than embedding inline facts — a
real gap confirmed by a fresh review round, not a hypothetical one.** The
provider-resolution phase below reads the *live libraries actually present*
at the declared sysroot path (their real SONAMEs, exports, symbol
versions) to decide presence/version-floor/incomplete-coverage — but a
digest computed purely from the YAML's own resolved content (the sysroot
*path string*, declared expected facts) says nothing about what was
physically found there at probe time. Two different runners — or the same
runner at two different points in time — can share byte-identical
`environments:`/`providers:` YAML and therefore an identical digest under
this narrower definition, while the sysroot path on each actually contains
different library versions, producing different real provider-resolution
verdicts under a digest that claims the two runs are equivalent. This
defeats the entire point of persisting an environment digest: a consumer
comparing two runs' digests to decide whether they represent "the same
environment" would wrongly conclude they do. The digest must therefore
also incorporate a **normalized, content-addressed inventory of the
actually-probed provider facts** (per-provider: resolved SONAME, presence,
exported-symbol set or its own digest, symbol-version set) — computed
*after* the live sysroot probe runs, not merely from the declared YAML —
folded into the same digest the matrix content already contributes to (or
recorded as a separate, explicit field alongside it, whichever the
provider-resolution phase's own return shape makes more natural; either
way, the persisted digest must change when the physically probed contents
change, config held constant). An environment whose sysroot cannot be
probed at all (unreachable path, permission error) must not silently fall
back to a config-only digest — that is exactly the same "fail closed into
a distinct, named failure class" pattern G41 Phase 3 already establishes
for assurance, applied here to environment-digest computation.

**The content-bound digest above cannot live in `RunPlanCheck` as
originally specified — a real job-ordering conflict confirmed by reading
`check-project.yml` directly, correcting the design rather than only the
definition.** `RunPlanCheck.environment_digest` is computed and persisted
into `run-plan.json` by the `plan` job, which always runs on
`ubuntu-latest` (fixed, confirmed in the workflow) — *before* any per-cell
`check` job (which runs on `matrix.runs_on`, potentially Windows/macOS/a
custom self-hosted runner entirely different from the planner) ever
executes. A sysroot an environment's `providers:` names may exist *only*
on that specific check-job runner — the planner has no access to probe it
at all, on any runner, let alone the one where the actual comparison
later executes. Requiring `RunPlanCheck` to already carry a digest that
depends on a live probe is therefore not merely hard, it is structurally
impossible for exactly the cross-platform-environment case this whole
provider-resolution phase exists to serve. The design splits into two
digests, not one:

- **`RunPlanCheck.environment_digest`** (computed during planning, in the
  `plan` job, before any check job runs) stays the narrower, config-only
  digest — the resolved `environments:`/`providers:` YAML content
  (sysroot path string, declared expectations), available with no live
  probe. This is what the run plan, the effective-configuration receipt,
  and the aggregate's profile/evaluation matrix already needed for
  identity/grouping purposes, and it is genuinely computable at plan time.
- **A new, separate post-probe field** — e.g. `probed_environment_digest`
  — computed by the `check` job itself, *after* it actually probes the
  live sysroot on its own runner, and stamped onto that job's own report
  envelope (not `run-plan.json`, which has already been written and
  uploaded by the time this value exists). This is the field that
  incorporates the actually-probed provider-fact inventory described
  above, and it is the one a consumer must read to know whether two runs'
  *live* environments genuinely matched — `environment_digest` alone
  never proves that, and this plan must not claim otherwise.
- The report schema and the aggregate's profile/evaluation matrix both
  gain this second field alongside the first, with the aggregate treating
  a same-`environment_digest`-but-different-`probed_environment_digest`
  pair as exactly what it is: same declared configuration, different live
  environment content — not an error by itself, but a fact the aggregate
  must be able to surface, since it is precisely the scenario the earlier
  correction in this section identifies as a real risk.

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
environment — restored from a snapshot taken immediately before the
*first* environment's `_env_matrix_contract_changes()` pass, never
sharing mutated objects across environments.

**Blanket-resetting `effective_verdict`/`modulation_reason`/
`modulation_rule` to `None` — the alternative this section originally
offered alongside deep-copying — is wrong, not merely a less-clean
equivalent, and a review round caught it by reading two of this
function's own callers rather than only its own skip check.** Some
detectors assign an *authoritative* modulation before
`_env_matrix_contract_changes()` ever runs, and that function's own skip
condition is what deliberately protects it: `diff_stdlib_impl.py`
constructs certain findings with `effective_verdict=Verdict.BREAKING`
already set at creation time (never `None` to begin with for those), and
`diff_templates.py`'s own lambda-closure-demotion pass
(`demote_lambda_closure_unexported_findings`) explicitly checks `if
change.effective_verdict is not None: continue` before ever touching a
finding, then sets `effective_verdict=Verdict.COMPATIBLE_WITH_RISK` on
the ones it does demote — precisely so a later stage's skip check (the
identical `if change.effective_verdict is not None: continue` in
`apply_runtime_floor_contract()`) leaves that earlier decision alone.
Resetting the field to `None` before each environment's pass erases
exactly this signal: `apply_runtime_floor_contract()`'s own skip check
would then see a blank slate for a finding that was never meant to be
eligible for floor-based reclassification at all, letting that
environment's runtime floor silently overwrite an authoritative
pre-environment modulation with its own verdict. The fix is not "reset
to a blank value" but "restore to the exact pre-environment state" —
clone the `Change` list (or snapshot each finding's
`effective_verdict`/`modulation_reason`/`modulation_rule` triple) once,
immediately after every upstream detector (including `diff_stdlib_impl`/
`diff_templates`) has run and *before* `_env_matrix_contract_changes()`
is invoked for the first environment, and restore from that one
snapshot before each subsequent environment's pass — never a blanket
`None` reset, which cannot distinguish "not yet modulated" from
"deliberately modulated by an earlier, authoritative stage."

**Resetting/copying the `Change` list is still not sufficient by itself —
it only covers half of `_env_matrix_contract_changes()`, and the other
half must be *rerun*, not copied, per environment — a further gap
confirmed by reading that function directly, not assumed.**
`_env_matrix_contract_changes()` (`checker.py`) does two structurally
different things under one gate, by its own docstring: (1)
`apply_runtime_floor_contract()` reclassifies an *existing* delta finding
already in `kept`/`verdict_redundant` in place — the mutation case the
correction above already fixes; (2) `check_platform_baseline_floor()`,
`check_musllinux_glibc_dependency()`, and the wheel-deployment checks
(`check_macos_deployment_target_floor`/`check_wheel_tag_architecture_
mismatch`/`check_wheel_rpath_not_portable`/`check_wheel_closure_
dependency_violation`) are each **standalone**: they read the new
binary's own raw evidence (`new_elf`/`new_macho`) directly and *produce
new findings* — independent of whatever is already in `kept`, and
independent of whether the floor moved between old and new. The
canonical example this codebase's own comment already names: "a binary
that has always required a newer glibc than its wheel tag promises" —
this produces a `Change` only when this function actually runs against
that environment's own declared floor; there is no pre-existing delta in
the `DiffResult` for a pristine-Change-list reset to preserve or copy.
An unchanged binary that has always exceeded its declared manylinux floor
would therefore have this violation reported for the *first* environment
evaluated (whichever one happens to trigger it once) and silently
omitted from every other grouped environment's report, since resetting/
copying `Change` objects has nothing to reset *to* for a finding these
functions haven't produced yet.

The fix: the per-environment fan-out must **rerun the complete
`_env_matrix_contract_changes()` step once per environment** — both
halves, not only the mutation half — using the raw snapshot facts
(`new_elf`/`new_macho`) already held in memory from the single extraction
this section's efficiency constraint already guarantees, with each
environment's own resolved `EnvironmentMatrix`/floors. This still
satisfies "extract/diff exactly once": no new binary/header/source
*extraction* runs per environment, since `new_elf`/`new_macho` are the
already-extracted facts from the one `dump`/`compare` pass — what reruns
per environment is this one environment-*dependent policy* stage
specifically, not the extraction stage the constraint actually protects.

**"Every other stage runs exactly once" (an earlier draft's own closing
claim, immediately above) is wrong for the stages downstream of this one
— confirmed by reading `checker.compare()`'s actual call order, not
assumed.** `_env_matrix_contract_changes()` deliberately runs *before*
`_apply_soname_policy()` and `_compute_verdict_for()`, and `compare()`'s
own comment states exactly why: "so a floor-decided BREAKING finding also
drives the `soname_bump_recommended` advisory... and so the internal-node
demotion inside `_apply_soname_policy` ... cannot race it." Both
downstream stages read directly from what this stage just produced/
modulated. Rerunning only `_env_matrix_contract_changes()` per environment
while sharing one, already-computed `_apply_soname_policy()`/
`_compute_verdict_for()` result across all environments would mean: only
whichever single environment happened to back that one shared pass gets
a correctly-attributed SONAME advisory and a correctly-computed overall
verdict — every other environment's genuinely different floor-driven
outcome (e.g. one environment's floor makes a delta `BREAKING`, another's
doesn't) would silently share the wrong verdict/advisory instead of its
own. The per-environment fan-out must therefore rerun the **complete
downstream policy chain** per environment, not only
`_env_matrix_contract_changes()` in isolation: `_apply_soname_policy()`,
`_compute_verdict_for()`, and — when contract evaluation is active —
`record_compatibility_decisions()`, each fed that environment's own
`_env_matrix_contract_changes()` output, producing that environment's own
verdict and advisory set.

**"Fed that environment's own output" is not automatically true for the
contract stage specifically, and reusing one shared `ContractEvaluationStage`
instance across environment branches would silently contaminate every
branch after the first — confirmed by reading `contract_pipeline.py`
directly, not assumed.** `ContractEvaluationStage.classify()` *appends*
every finding it records to `self.changes` rather than replacing it, and
`build_context()` persists that same accumulated `self.changes` list
verbatim as the stage's context. Calling `classify()` a second time on
one shared stage instance for a second environment does not give that
environment a fresh, empty classification pass — it adds to whatever the
first environment's branch already appended, so the second (and every
subsequent) environment's `record_compatibility_decisions()`/
`build_context()` call persists a context containing every earlier
branch's classified findings too, potentially under duplicate finding
identities once the same underlying `Change` list is evaluated more than
once. `build_contract_stage()` — the expensive half that resolves mode,
both sides' public/export surfaces, and the provider-evidence ledger —
is genuinely reusable across environments (that evidence does not vary
per environment, only the floor/verdict does), but the *classification*
half must not be: each environment branch needs its own fresh
`ContractEvaluationStage` instance (or an equivalent reset of
`self.changes` to empty) before its own `classify()`/
`record_compatibility_decisions()`/`build_context()` calls, sharing only
the immutable evidence-collection result the expensive `build_
contract_stage()` call already produced once. Only the stages genuinely upstream of, and
independent from, `_env_matrix_contract_changes()` — symbol/type diffing,
suppression, build-context reconciliation, the NumPy C-API delta — run
exactly once and are shared/copied across environments as already
established; everything from `_env_matrix_contract_changes()` onward in
`compare()`'s own call order reruns per environment.

**That in-process mutation fix is necessary but not sufficient — the real
extract-once boundary in this codebase is a GitHub Actions *job*, not a
Python function call, and nothing in this plan as drafted groups
environment-only-differing checks before the workflow decides how many
jobs to run. Confirmed by reading `check-project.yml` directly, not
assumed.** The `plan` job's own matrix-generation step does
`checks = plan.get('checks', [])` then `matrix={'include': checks}` —
i.e. one `RunPlanCheck` from `run-plan.json` becomes exactly one matrix
cell, and `strategy: matrix` spawns one independent job per cell, each of
which runs `actions/check-target` — its own full `dump`/`compare`
invocation — from scratch. Two `RunPlanCheck`s sharing (target, profile,
channel, requested_depth) but differing only in `environment_id` (exactly
the schema this section's own example encourages a user to write:
separate `checks:` entries per environment) therefore become two
independent jobs performing two independent extractions today — the
"extract/diff exactly once" constraint stated above describes what a
single Python process must do, not what the actual workflow orchestration
does across job boundaries, and as drafted this plan never closes that
gap.

Closing it needs a grouping stage *before* the matrix is built, not
merely a safer mutator once inside one job:

1. **Grouping key vs. per-environment evaluation descriptor — corrected
   three times now, and each correction after the first changes the
   design's shape, not just its key.** The first correction (grouping
   solely on (target, profile, channel, requested_depth) would also
   collapse checks differing in `analysis_evidence`/`analysis_policy`/
   `analysis_assurance_requirement`) is still right — these describe *what
   extraction/comparison to run*, and two checks disagreeing on any of
   them cannot share one extraction. **Explicit `id` does not belong in
   this list — see the third correction below for why it was wrongly
   included here in an earlier draft.** A **second** review round found a
   further gap in the same direction: `RunPlanCheck` also carries
   `required: bool`, `gate_mode: str`, and `allow_new_target: bool`
   (`abicheck/buildsource/run_plan.py`) — none are profile-derived
   (unlike `compile_gcc_path`/`compile_ast_frontend`/`runs_on`/etc., which
   already collapse safely for free once `profile_id` is part of the
   group key, since a fixed profile always resolves to the same values for
   those), but per-*check*-declaration fields that describe **how that
   check's own report should be gated/consumed downstream**, independent
   of the target/profile/channel/depth/evidence tuple. Two environments
   sharing an otherwise-identical extraction key but declared with
   different `gate_mode`/`required` (e.g. one environment meant as
   advisory, a sibling meant as blocking) must not be forced to share a
   single value for either — adding them to the grouping key would be
   correct but defeats grouping precisely for the case a project author is
   most likely to want (the same extraction, evaluated as blocking on one
   environment and advisory on another).

   **A second review round found the identical mistake already applied
   to explicit `id`, which this correction did not originally catch: an
   explicit `id:` was listed as part of the grouping/extraction key
   above, but it has exactly the same "describes report identity, not
   what to extract" shape as `required`/`gate_mode`/`allow_new_target` —
   and keeping it in the key is actively self-defeating, since the
   project schema's own `id:` field is meant to let two otherwise-
   identical checks (differing only by declared environment) each carry
   their own distinguishing name. Two checks sharing (target, profile,
   channel, requested_depth, `analysis_evidence`, `analysis_policy`,
   `analysis_assurance_requirement`) but declared with distinct explicit
   `id:` values — exactly the shape a project author reaches for when
   naming per-environment checks individually — would then never collapse
   into one `RunPlanCheck` under a key that still requires `id` to match,
   so `check-project.yml` keeps creating one extraction job per
   environment regardless of this whole phase's grouping design.**

   **The right fix is therefore not a wider grouping key, but moving all
   four of these fields — `required`/`gate_mode`/`allow_new_target` *and*
   explicit `id`— out of `RunPlanCheck`'s own top-level singular fields
   and into a per-environment evaluation descriptor** — the grouping key
   is exactly the tuple (target, profile, channel, requested_depth,
   `analysis_evidence`, `analysis_policy`, `analysis_assurance_
   requirement`) — all fields that genuinely determine *what to
   extract/compare*, with explicit `id` deliberately excluded alongside
   the three gating fields — while `required`/`gate_mode`/
   `allow_new_target`/explicit `id` — fields that determine *how to gate
   or identify a given environment's own result*, not what to run — move
   onto a new `EnvironmentEvaluation` value (`environment_id`,
   `environment_digest`, `explicit_id: str | None`, and that environment's
   own `required`/`gate_mode`/`allow_new_target`, each defaulting to the
   check's declared values when a project author doesn't differentiate
   them per environment). `RunPlanCheck` gains
   `environment_evaluations: list[EnvironmentEvaluation]` (not the bare
   `environment_ids: list[str]` the first correction proposed) while
   keeping its own top-level `required`/`gate_mode`/`allow_new_target`
   fields exactly as they are today for the ungrouped/single-environment
   case — every existing invocation's shape is therefore unchanged.
   Wherever `RunPlanCheck`s are generated from the project's `checks:`/
   `environments:` declarations (`abicheck project plan`, i.e. the
   run-plan-generation code `abicheck/buildsource/run_plan.py` and/or its
   caller), checks matching on the extraction-only key collapse into
   *one* `RunPlanCheck` carrying this list — one grouped check, one matrix
   cell, one job, but each environment's own gating intent travels with it
   undiluted.

   **The descriptor must also represent "no declared environment," not
   only named environments — a real gap confirmed by checking this design
   against this plan's own "Environments" acceptance test.** That test
   requires *three* results from a single extraction/diff pass: the
   unconfigured/no-environment evaluation (reported as risk), and two
   named-environment evaluations (breaking against an old floor,
   compatible against a new one) — but `EnvironmentEvaluation` as defined
   above requires an `environment_id`/`environment_digest`, which the
   no-environment case has neither of. Forcing that case through the same
   shape would mean either running it as a separate, ungrouped matrix job
   (violating the single-extraction-pass requirement this whole design
   exists to satisfy) or inventing a synthetic environment id for "no
   environment" (a fabricated identity that misrepresents what the report
   actually means). Fixed by making `environment_id: str | None` on
   `EnvironmentEvaluation` — `None` explicitly represents the
   no-environment evaluation, with `environment_digest` also `None` for
   that entry (there is no environment matrix content to fingerprint), and
   its check-id behavior mirrors the plain, unqualified shape every
   existing single-environment/no-environment check already produces
   today — no `!<environment_id>` segment at all for the `None` entry,
   the qualifier appearing only on entries with a real environment id.
   A `RunPlanCheck` may therefore legitimately carry one `None` entry
   alongside any number of named-environment entries in the same
   `environment_evaluations` list, all evaluated from the one extraction
   this phase's design already establishes.
2. **Fan-out inside the one job**: `actions/check-target` (or the CLI
   command it shells out to) gains a mode that performs the dump/compare
   exactly once, then evaluates the resulting `DiffResult` against each
   environment in the group's list in-process, once per environment,
   rerunning the **complete downstream policy chain** from
   `_env_matrix_contract_changes()` onward for that environment — both
   resetting/copying the pristine `Change` list and fully rerunning
   `_env_matrix_contract_changes()`'s standalone, finding-producing checks
   against the raw extracted snapshot for that environment's own floors
   (established above — a reset alone does not reproduce these), **and**
   rerunning `_apply_soname_policy()`/`_compute_verdict_for()`/contract
   decision recording against that environment's own
   `_env_matrix_contract_changes()` output (established immediately above
   — these two stages read directly from what that stage produces, so a
   shared, single downstream pass would misattribute one environment's
   SONAME advisory/verdict to every other environment) — and emits one
   report artifact **per environment** from that single job.
3. **Each environment's report needs its own `target_id`, not merely its
   own artifact filename — confirmed by reading the aggregate's actual
   loader, not assumed, correcting a wrong claim in an earlier draft of
   this plan.** `abicheck/workflows/aggregate/execute.py`'s
   `collect_reports()` indexes every loaded report by its **in-report**
   `target_id` field (the `check_id`-shaped string) and raises
   `AggregateError` on a duplicate — keyed by that field, not by the
   artifact's filename. Grouping N environments into one `RunPlanCheck`
   with no per-environment identity distinction means all N emitted
   reports carry the *identical* `check_id`/`target_id` (the
   `~<explicit_id>` tail is optional and user-supplied, not automatically
   derived per environment) — the second report loaded aborts the entire
   aggregate run with a duplicate-target-id error, regardless of how
   uniquely the artifact files themselves are named. An earlier draft of
   this plan's claim that "unique artifact names" alone make the fan-out
   transparent to the aggregate was wrong; fixed as follows:
   - **`_CHECK_ID_RE`/`CHECK_ID_PATTERN`/`build_check_id()`/the JSON
     schema pattern** (already required to extend in lockstep for the
     optional `~<explicit_id>` tail per the "Explicit check identifiers"
     section) gain a second, distinct optional segment for the
     environment qualifier — e.g. `(?:!(?P<environment_id>[A-Za-z0-9._-]+))?`
     inserted between the depth segment and the `~<explicit_id>` tail, so
     the full shape is
     `target@profile#channel@depth(?:!environment_id)?(?:~explicit_id)?`.
     A distinct delimiter (`!`, not reusing `~`) keeps the
     system-derived environment qualifier unambiguous against a
     user-supplied explicit id, and the two compose (a check may declare
     both an explicit `id:` and belong to a multi-environment group).
   - **This qualifier is mandatory for every *named*-environment entry
     whenever a grouped `RunPlanCheck` carries more than one
     `EnvironmentEvaluation` — but the `None` (no-environment) entry
     added above always stays unqualified, in every group shape, not
     only the single-entry case.** A fresh review round found the
     original phrasing ("mandatory whenever the group has more than one
     environment") conflicts directly with that `None` entry: it has no
     `environment_id` to qualify with, so a rule requiring *every* entry
     in a multi-entry group to carry `!<environment_id>` either makes the
     `None` entry's own report id impossible to construct, or forces a
     fabricated qualifier onto it, contradicting its own definition above
     (no `!<environment_id>` segment at all for that entry). The
     acceptance test itself — one unconfigured evaluation plus two named
     environments, all from one extraction — requires exactly this mixed
     shape to work. Restated precisely: each named-environment entry in
     the group stamps its report's `target_id` with `!<environment_id>`;
     the `None` entry, if present in the same group, stamps its report
     with the plain, unqualified `target_id` — the same shape a
     single-environment or no-environment `RunPlanCheck` already
     produces today. A `RunPlanCheck` with exactly one entry (named or
     `None`) omits the qualifier entirely regardless of this rule, so
     every existing invocation's `target_id` shape is bit-for-bit
     unchanged.
   - **The aggregate's expected-target contract must expand too, not stay
     one entry per grouped check.** `ExpectedTargets` (built from
     `run-plan.json`'s own manifest projection) is keyed by target_id
     string with one entry expected per planned check; a grouped
     `RunPlanCheck` with N environment entries must therefore project to
     **N** expected target-id entries — one `!<environment_id>`-qualified
     entry per named environment, plus one *unqualified* entry for the
     `None` entry when present — not one collapsed entry, and not N
     qualified entries with the `None` case silently dropped. Otherwise
     `on_missing_required`'s coverage check reads N-1 of the produced
     reports as unexpected/extra and the single expected entry as
     satisfied by whichever report happens to load
     first, silently losing the missing-required guarantee for every
     environment but one.

This is real, new workflow-orchestration logic — a grouping pass over
`RunPlanCheck` generation, a multi-environment fan-out mode inside one
job, a second check-id qualifier segment, and an expanded expected-target
projection — not an extension of the in-process mutation-safety fix
above; the "Effort & risk" section below is revised accordingly.

4. **Two checks that deliberately do *not* group (because they differ on
   the extraction key) still need distinct `check_id`s — a gap this
   plan's own "Check identity" acceptance test reproduces exactly, and
   which a fresh review round found unaddressed, confirmed by reading
   `run_plan.py`'s existing duplicate-id guard directly.** Two checks
   sharing (target, profile, channel, requested_depth) but differing in
   `analysis_evidence`/`analysis_policy`/`analysis_assurance_requirement`
   are, correctly, generated as two separate, single-entry
   `RunPlanCheck`s (they must not share one extraction). Each single-entry
   check omits the `!<environment_id>` qualifier by the rule above (no
   environment to qualify with), and `analysis_evidence`/`analysis_
   policy`/`analysis_assurance_requirement` were never part of the
   generated `check_id` string (only target/profile/channel/depth feed
   `build_check_id()`) — so absent an explicit `id:` from the project
   author, both checks generate the byte-identical `check_id`.
   `run_plan.py`'s own pre-existing duplicate-id guard (`seen_ids`/
   `duplicate_ids`, added specifically to catch exactly this class of
   collision before the matrix even runs, per its own comment) then
   rejects the whole run plan outright — directly contradicting this
   plan's own "Check identity" acceptance test, which requires this exact
   two-check, same-target/profile/channel/depth shape to succeed and
   produce two separate reports. Fixed one of two ways (either closes the
   gap; pick whichever composes more simply with the qualifier work
   above):
   - **Require an explicit `id:` whenever the base tuple collides across
     differing analysis axes** — extend `run_plan.py`'s existing
     duplicate-id guard to detect this specific case (same base tuple,
     differing analysis fields, no explicit `id:` on at least one of the
     colliding checks) and produce a clear, actionable error naming the
     colliding checks and pointing at `id:` as the fix, rather than the
     generic "give it a distinct channel/depth/profile" message (which is
     wrong advice here — the profile/channel/depth are deliberately
     identical); or
   - **Deterministically qualify the generated identity by the differing
     analysis axes themselves** — extend `build_check_id()` to fold
     `analysis_evidence`/`analysis_policy`/`analysis_assurance_requirement`
     into the generated string whenever any is set to a non-default value
     (mirroring how the environment qualifier is appended only when an
     environment is actually declared), so two checks differing only in
     `analysis:` fields disambiguate automatically with no explicit `id:`
     required — matching the acceptance test's own expectation that this
     "just works" without every project author having to hand-name every
     analysis-differentiated check.
   Either way, `run_plan.py`'s duplicate-id guard and its error message
   must be updated to reflect whichever fix is chosen, since the guard's
   current wording assumes profile/channel/depth are the only axes a
   colliding pair could differ on — no longer true once `analysis:`
   exists.

This applies equally to whatever new
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

- **`abicheck/buildsource/run_plan.py` — `RunPlanCheck`: new `check_id`
  (explicit, optional), `environment_evaluations: list[EnvironmentEvaluation]`
  (**a list of a new small structured value, not a bare
  `environment_ids: list[str]`** — see the job-boundary grouping
  requirement below: a bare id list would lose each environment's own
  `required`/`gate_mode`/`allow_new_target`/explicit `id` the moment more
  than one environment shares a group), `analysis_evidence`/
  `analysis_policy`/`analysis_assurance_requirement` fields, following the
  exact structural precedent `consumer_compile_*` already set (see G34
  Phase 0). Each `EnvironmentEvaluation` carries `environment_id`,
  `environment_digest`, `explicit_id: str | None`, and that environment's
  own `required`/`gate_mode`/`allow_new_target` (defaulting to the check's
  top-level values when undifferentiated). `RunPlanCheck`'s own top-level
  `check_id`/`required`/`gate_mode`/`allow_new_target` fields are
  unchanged, still governing the ungrouped/single-environment case. Also:
  whatever generates `RunPlanCheck`s from a project's `checks:`/
  `environments:` declarations must group checks matching on the
  *extraction-only* key (target, profile, channel, requested_depth,
  `analysis_evidence`, `analysis_policy`, `analysis_assurance_requirement`)
  — not the narrower four-tuple, which would wrongly collapse two checks
  differing only in evidence method/policy/assurance, and not a wider key
  including `required`/`gate_mode`/`allow_new_target`/explicit `id`, which
  would defeat grouping for the common case of one extraction evaluated as
  blocking on one environment and advisory on another, or of a project
  author giving each environment's check its own distinguishing `id:` (a
  second review round found explicit `id` had wrongly been left in the
  key in an earlier draft, for the identical reason the three gating
  fields were excluded) — into one `RunPlanCheck` carrying the full
  `environment_evaluations` list. See "Efficiency constraint" above for
  why the naive one-`RunPlanCheck`-per-environment shape silently
  reintroduces one full extraction per environment at the
  job-orchestration layer, not just inside one Python process.**
- **`.github/workflows/check-project.yml`'s matrix-generation step and
  `actions/check-target`'s invocation** — the grouped `RunPlanCheck.
  environment_evaluations` list must reach the single matrix cell/job
  unexpanded (one cell per grouped check, not one per environment), and
  the job's `dump`/`compare` invocation gains a mode that runs
  extraction/diff once and then fans out the pristine-`Change`-list
  per-environment evaluation from "Efficiency constraint" above, emitting
  one report artifact per environment from that single job. Each report
  is stamped with its own `!<environment_id>` check-id qualifier (see
  "Efficiency constraint" above — a unique *filename* alone does not make
  this transparent to the aggregate, which keys on the in-report
  `target_id`) **and** gated according to *that specific environment's
  own* `required`/`gate_mode`/`allow_new_target`, read from its
  `EnvironmentEvaluation` entry rather than one shared value for the whole
  group. This is the real, new workflow-orchestration logic this plan
  requires beyond schema/digest plumbing — see "Efficiency constraint"
  above.
- **`abicheck/workflows/aggregate/contracts.py`/`execute.py`** — the
  second, environment-qualifier segment on `_CHECK_ID_RE`/`CheckIdParts`
  (composable with, and distinct from, the `~<explicit_id>` tail already
  required there), and `ExpectedTargets`'s manifest projection expanding
  one grouped `RunPlanCheck` with N environments into N expected
  target-id entries rather than one — without this, `on_missing_required`
  silently stops guaranteeing coverage for every environment but one in a
  group.
- **`abicheck/workflows/aggregate/contracts.py`** — required, not optional:
  `_CHECK_ID_RE`'s extended `~<explicit_id>` suffix **and** the separate
  `!<environment_id>` segment (see "Explicit check identifiers" and
  "Efficiency constraint" above — both segments, composed, per the full
  shape `target@profile#channel@depth(?:!environment_id)?(?:~explicit_id)?`
  already given there) and `CheckIdParts`' matching `explicit_id`/
  `environment_id` fields — without this, the whole `id:`/multi-
  environment feature silently breaks profile/finding-matrix grouping the
  moment either is used.
- **`abicheck/checker_types.py` (`CHECK_ID_PATTERN`/`validate_check_id()`)
  and `abicheck/buildsource/check_report.py` (`build_check_id()`) —
  equally required, not optional, and must cover *both* new segments, not
  only `~<explicit_id>` — a real gap confirmed by a fresh review round:
  an earlier draft of this bullet extended these two with only the
  `~<explicit_id>` tail, which would leave `build_check_id()`/
  `validate_check_id()` rejecting the environment-qualified shape
  (`target@profile#channel@depth!environment~id`) this same plan's own
  "Efficiency constraint" section requires for a grouped multi-environment
  check, before `contracts.py` ever sees the string.** These run *before*
  `contracts.py` ever sees the string: `build_check_id()` calls
  `validate_check_id()` unconditionally, so a `check_id` builder producing
  either new segment without extending this pattern first would raise at
  construction time. Both need the complete, composed extension —
  `(?:!<environment_id>)?(?:~<explicit_id>)?` in that order, matching
  `_CHECK_ID_RE`'s own shape exactly, not the `~<explicit_id>`-only
  extension from an earlier draft.
- **`abicheck/schemas/compare_report.schema.json`'s `check_id` `pattern`**
  (plus a schema version bump and the `docs/reference/schemas/v1/`
  re-sync via `scripts/publish_schemas.py`) — an independent copy of the
  same anchored regex per `CHECK_ID_PATTERN`'s own comment; must carry the
  identical complete, composed extension (both segments) in lockstep with
  the two Python-side validators above, or JSON Schema validation of a
  real multi-environment report becomes the new failure point.
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
- Named environments (L, revised up from M): schema + digest plumbing +
  the "evaluate once against N environments" reconciliation — **and,
  confirmed by reading `check-project.yml` directly, a genuine
  run-plan-generation grouping pass plus a multi-environment fan-out mode
  inside `actions/check-target`'s single job**, since the naive
  one-`RunPlanCheck`-per-environment shape reintroduces one full
  extraction per environment at the job-orchestration layer regardless of
  how safely the in-process mutation is handled. Medium-to-high risk: the
  in-process extract-once invariant is straightforward to enforce once
  named, but the job-boundary grouping is new orchestration logic with no
  existing precedent in this workflow to model it after (G34 Phase D's
  `finding_matrix` reconciliation covers *aggregating* already-emitted
  reports, not *avoiding generating* N redundant ones in the first place).
  Three further, confirmed pieces belong to this same phase, not a later
  one: the grouping key must match on every extraction-relevant
  analysis/identity field, not just (target, profile, channel,
  requested_depth) — else two checks differing only in evidence
  method/policy/assurance/id wrongly collapse; the grouping key must
  **not** additionally include `required`/`gate_mode`/`allow_new_target`,
  which instead move onto a per-environment `EnvironmentEvaluation` value
  so two environments sharing one extraction can still be gated
  differently (blocking vs. advisory); and each environment's emitted
  report needs its own `!<environment_id>`-qualified `target_id`, with the
  aggregate's `ExpectedTargets` projection expanded to one entry per
  environment per grouped check, since a unique artifact filename alone
  does not stop `collect_reports()`'s in-report-`target_id` duplicate
  check from aborting the run.
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
