# G30 — GitHub Actions Integration Model: Project Lifecycle Backlog

**ADR:** [ADR-047](../adr/047-github-actions-integration-model.md)
**Type:** Initiative plan (multi-phase); no `usecase-registry.yaml` entries yet
— GH-Actions integration is a cross-cutting CI/UX surface, not a detector
capability, so it is tracked here rather than in the registry (consistent
with how G19/G24 track their own initiative work).
**Effort:** XL (phased P0/P1/P2) · **Risk:** medium — new schemas and two
new composite Actions are additive, not breaking, but the documentation
reorganization touches most of `docs/user-guide/`.

## Problem

ADR-047 records the target domain model and component surface: a project
integration lifecycle (config → build → evidence → target/baseline
resolution → check → report → optional fan-in → baseline publish) that
demotes `abicheck aggregate` from an implicit architectural center to one
scenario (S28) among 28. This plan is the sequenced backlog that gets there
without one large rewrite PR, plus the pilot-validation plan the ADR's
decision log (D9) flags as an open gap.

## Sequencing principle

Each phase below should land as **multiple small, independently reviewable
PRs**, not one PR per phase. The suggested PR boundaries are listed under
each item. No PR should combine a schema addition with a documentation
reorganization — those are reviewed differently and by different failure
modes (schema PRs need `tests/test_verify_profiles.py`-style contract tests;
doc PRs need `mkdocs build --strict` + `check_ai_readiness.py`).

---

## P0 — Onboarding blockers (no architecture change required)

These fix real defects the audit (ADR-047 §"What the audit found")
identified in the *existing* surface. None require the new primitives.

### P0.1 — Runtime warning when a mode-scoped input is set on an incompatible mode — **done**

**Problem:** `debug-info1/2`, `devel-pkg1/2`, `dso-only`,
`include-private-dso`, `keep-extracted`, `fail-on-removed-library`, `jobs`,
`abi-baseline`, `estimate`, and `audit` are all declared as
unconditional top-level inputs but each is only forwarded/consumed in a
subset of modes (`action/run.sh:387-407`'s `_is_release_style_operand()`
guard for the first seven; `run.sh:150-233` for `abi-baseline`;
`estimate`/`audit` are scan-mode-only). **Correction from an earlier draft
of this item:** `action.yml`'s `description:` text for all of these already
states the scope inline (e.g. `debug-info1`: "compare mode, directory/package
operands only"; `abi-baseline`: "for compare mode ... or scan mode";
`estimate`/`audit`: "scan mode only") — confirmed by re-reading `action.yml`
lines 49-76, 252-266, 284-289. So the documentation half of this item is
**already done**; the remaining gap is purely a *runtime* one: setting one of
these inputs on an incompatible `mode` produces no feedback at all today
(silent no-op), which a reader of the description text would only catch by
reading carefully, not by CI telling them.

**Change:** Add a `validate-inputs.sh` check that **warns** (job summary
annotation, not a hard failure — these are legal-but-inert combinations, not
errors) when a mode-scoped input is set on an incompatible `mode`.

**Files:** `action/validate-inputs.sh`, `tests/` — action shell-mapping
tests already exist per the audit; extend with cases asserting the new
warning fires/doesn't fire.

**Tests:** New `test_action_input_scope_warnings` case(s) in the existing
Action shell-mapping test suite (mirroring however `debug-info1` forwarding
is already tested).

**Docs:** `docs/user-guide/github-action.md`'s input table gains a "Scope"
column.

**PR boundary:** one PR — shell + input descriptions + tests together (small
enough not to split further).

**Status:** implemented. `action/validate-inputs.sh` now warns
(`::warning::`, exit 0) when `debug-info1`/`debug-info2`, `devel-pkg1`/
`devel-pkg2`, `dso-only`, `include-private-dso`, `keep-extracted`,
`fail-on-removed-library`, or `jobs` are set on a mode/operand combination
outside "compare mode, directory/package operands only", when `abi-baseline`
is set outside `compare`/`scan` mode, or when the deprecated `estimate`/
`audit` scan-only aliases are set outside `scan` mode. `action.yml` forwards
the new inputs to the validation step; `tests/test_action_validate_inputs.py`
covers each case (warn and silent) via `TestModeScopedInputWarnings`.

### P0.2 — `collect-facts` `phase: auto` fail-loud for wrapper/plugin — **done**

**Problem:** `phase: auto` silently only runs `prepare` for
`producer: wrapper`/`clang-plugin` (`actions/collect-facts/run.sh:714-716`)
— a caller who doesn't realize this ends up with an unverified pack and no
error.

**Change:** When `phase: auto` resolves to `producer: wrapper` or
`clang-plugin`, emit an explicit job-summary notice *and* set a
`collect-facts` output (`auto-completed: false`) a caller can branch on,
instead of a print-only notice. Document the two-step choreography
explicitly in `docs/user-guide/producing-source-facts.md` rather than
implying `auto` is always one step.

**Files:** `actions/collect-facts/run.sh`, `actions/collect-facts/action.yml`
(new output), `docs/user-guide/producing-source-facts.md`.

**Tests:** Extend `actions/collect-facts`'s existing shell tests to assert
the new output value per producer.

**PR boundary:** one PR.

**Status:** implemented. `actions/collect-facts/run.sh` now writes a new
`auto-completed` output (`'true'`/`'false'`) alongside a `::warning::`
job annotation (upgraded from the print-only `::notice::`) when
`phase: auto` resolves to `producer: wrapper`/`clang-plugin` and therefore
only completes `prepare`. `actions/collect-facts/action.yml` documents the
new output; `docs/user-guide/producing-source-facts.md` spells out the
two-step choreography explicitly instead of only implying it, with a
sample `if: steps.facts.outputs.auto-completed != 'true'` guard.
`tests/test_action_collect_facts.py`'s new `TestAutoCompletedOutput` covers
replay (`auto-completed: true`, no warning), wrapper under `phase: auto`
(`auto-completed: false`, warning fires), wrapper under explicit
`phase: prepare` (`auto-completed: true`, no warning — not flagged like
`auto` is), and `phase: verify` (`auto-completed: true`).

### P0.3 — Report identity envelope (subset of ADR-047 §7) — **done** (schema/model half; CLI-population half still open)

**Problem:** JSON reports don't carry `check_id`/`profile_id`/
`requested_depth`/`effective_depth`/`baseline_channel` today — a P1
prerequisite, but valuable standalone since it's what makes `aggregate`'s
existing coverage/gate logic auditable.

**Change:** Add the identity fields from ADR-047 §7 to the existing
`compare`/`scan` JSON report schema as **additive, optional** fields (schema
version bump, backward compatible — old consumers ignore unknown fields).
Do *not* yet build `resolve-baseline`/`check-target` (P1) — this item only
makes the fields available so P1's primitives have something to populate.

**Files:** `abicheck/reporter.py`, `abicheck/checker_types.py` (or wherever
`DiffResult`/report serialization lives), schema files under wherever report
JSON schemas are versioned, `abicheck/serialization.py`.

**Tests:** Schema round-trip tests; `tests/test_verify_profiles.py`-style
schema-contract test if one doesn't already assert report schema stability.

**Migration:** additive only — `changelog.d/` fragment required (touches
`abicheck/**/*.py`) per AGENTS.md.

**PR boundary:** one PR for the schema/model change, a separate PR to wire
`requested_depth`/`effective_depth` population through the CLI (depends on
PR #601's `DumpDepthNotSatisfiedError` work landing first per ADR-047 §11.2
and the repo's existing Known Gaps entry — do not duplicate that
enforcement, extend it).

**Status:** the schema/model half is implemented — `DiffResult`
(`abicheck/checker_types.py`) and `ScanOutcome`
(`abicheck/scan_engine.py`) each gained five optional fields (`check_id`,
`profile_id`, `requested_depth`, `effective_depth`, `baseline_channel`,
all `None` by default and omitted from JSON — never emitted as null — when
unset). `abicheck/reporter.py` writes them into the full/`--stat`/leaf
JSON via a shared `_add_check_identity` helper when a caller sets them.
`abicheck/schemas/compare_report.schema.json` (and its published
`docs/schemas/v1/` mirror, kept in sync via
`scripts/publish_schemas.py`) declares the five properties (`report_schema_version`
bumped `2.10` → `2.11` → `2.12` — `2.11` landed independently via #612's
G31 Phase B3/ADR-048 `affected_public_roots`/etc. fields while this branch
was in flight, so this work's own bump moved to `2.12` on rebase);
`abicheck/schemas/__init__.py` documents both this bump and
`SCAN_SCHEMA_VERSION`'s matching `1.0` → `1.1` bump for the scan
side (no packaged JSON Schema file for scan output to update). Nothing
populates these fields yet — that's still P1.3's job, and the
`requested_depth`/`effective_depth` CLI-wiring PR remains blocked on
PR #601 per the note above.
`tests/test_report_schema.py`'s new `TestReportIdentityEnvelope`/
`TestScanReportIdentityEnvelope` classes cover: unset-by-default (omitted,
not null), round-trip + schema validation when set, `--stat` mode carrying
the fields too, and an invalid `requested_depth` enum value failing schema
validation.

### P0.4 — Canonical single-library and multi-DSO doc pages — **done**

**Problem:** multi-DSO guidance is split three ways with no single canonical
page (ADR-047 finding 4).

**Change:** Promote `docs/user-guide/github-action-source-scans.md`'s
"Recommended flow: multi-library release with one shared facts pack"
section to the canonical multi-DSO recipe; the other two pages
(`github-action.md`, `github-action-recipes.md`) link to it instead of
restating it. No new scenario content yet — this is de-duplication, not the
full scenario-first IA (that's P1's `docs/integration/` tree).

**Required caveat, flagged by review — do not skip:** the existing recipe
being promoted has every library in a multi-DSO release point at the *same*
shared `abicheck_inputs/` pack with no per-target projection check. ADR-047
§9 requires exactly that projection (`evidence.projection: "declared"` vs.
"inferred") before a per-target check may claim `effective_depth: source` —
but the validator that enforces it doesn't exist until P1.1
(`build-output.json` validator). If P0.4 lands (as a docs-only, no-code PR)
before P1.1 ships, promoting the recipe *as-is* to "the" canonical multi-DSO
page teaches exactly the anti-pattern §9 exists to prevent: claiming
source-depth evidence for every DSO from one unprojected, build-wide pack.
**This PR must add an explicit caveat to the promoted section** — e.g. "this
shared-pack recipe currently supports build-wide source audits and
per-target *header*-depth checks; claiming per-target *source*-depth
coverage from a shared pack requires the per-target projection validator
tracked in P1.1, not yet implemented" — rather than promoting the recipe
silently as if it already satisfies §9's safe model.

**Files:** the three docs pages listed; `mkdocs.yml` nav unaffected (no new
pages yet).

**Tests:** `mkdocs build --strict`; `check_ai_readiness.py`'s
`mkdocs-nav-coverage` and `doc-count-sync` checks.

**PR boundary:** one PR, docs-only.

**Status:** implemented. `github-action.md` and `github-action-recipes.md`
already linked to `github-action-source-scans.md`'s "Recommended flow: a
multi-library release with one shared facts pack" section rather than
restating it — that de-duplication predates this item. What was missing is
now added: the section is explicitly marked as the canonical multi-DSO
recipe, and the required scope caveat is in place (shared-pack recipe
supports build-wide source audits and per-target header-depth checks;
per-target source-depth coverage needs P1.1's projection validator, not yet
implemented). `mkdocs build --strict` passes (pre-existing anchor-mismatch
`INFO` lines in that same section predate this change and are unrelated);
`check_ai_readiness.py` shows the same warning count as before this item.

---

## P1 — Integration model (ADR-047's new primitives)

### P1.1 — `build-output.json` schema + validator — **done**

Implements ADR-047 §2/§11.1. New schema module (e.g.
`abicheck/buildsource/build_output.py` or a sibling of `inputs_pack.py`,
following the existing `abicheck/buildsource/CLAUDE.md` module-table
convention), plus `python -m abicheck build-output validate <dir>` CLI
subcommand (or `abicheck buildsource validate-output`, matching existing
`cli_buildsource.py` command-family naming). No producer tooling yet (that's
P1.2) — this PR defines the contract and validates a hand-authored example.

**Files:** new `abicheck/buildsource/build_output.py` (or similarly named
per existing conventions), new `abicheck/cli_buildsource.py` subcommand,
`docs/reference/build-output-schema.md` (new).

**Tests:** unit tests for the validator's failure taxonomy (empty declared
root, digest mismatch, `projection` inconsistency — ADR-047 §11.1).
**Must include a shared-pack-across-two-targets case, corrected across two
review rounds:** a non-empty-only check would pass a `build-output.json`
whose two `targets[]` entries both point at the same `abicheck_inputs/`
pack marked `"declared"`. `abicheck.buildsource.inputs_validate._target_id_issues`
only compares TU `target_id`s against the pack's **own** `manifest.library`
field and explicitly does not flag untagged TUs — it has no parameter for
an externally-known expected target, so calling `validate_inputs_pack`
unmodified does **not** catch either (a) a legacy/untagged-TU pack shared
across targets, or (b) a pack whose `manifest.library` disagrees with which
`build-output.json` target actually references it. The validator needs a
real extension — either a new `expected_target_id` parameter on
`_target_id_issues`/`validate_inputs_pack`, or an equivalent comparison
performed in the new build-output validator using that function's existing
manifest/TU data — not a same-signature call to the function as it exists
today. **Scope corrected in a further review round — do not reject every
untagged-TU pack.** `abicheck/buildsource/inputs_emit.py:169-170` shows
producers already establish the library at pack-creation time via
`manifest.library`, and `inputs_validate.py:111-113` deliberately treats
missing per-TU `target_id`s as additive, not invalid — a single-target,
`manifest.library`-matched pack with untagged TUs is a legitimate legacy
producer output and must still pass. Test cases: (1) two targets sharing
one pack (whether or not its TUs carry `target_id`) must fail, (2) a pack
whose `manifest.library` (or a tagged TU's `target_id`) disagrees with the
specific target referencing it must fail, (3) a single-target,
`manifest.library`-matched pack with untagged TUs must **pass** (regression
guard against over-rejecting the legitimate legacy case). (1) and (2) are
currently unenforced; (3) guards the fix from over-correcting.

**Status:** implemented. `abicheck/buildsource/build_output.py` defines the
schema (`BuildOutput`/`BuildOutputTarget`/`BuildOutputEvidence`/etc., all
optional/defaulted per the `buildsource`-wide forward-compat convention) and
`validate_build_output()`, which implements every §11.1 rule: non-empty
declared header roots (including the S10 `generated_header_roots` hard-error
case), binary-exists + digest-matches, `evidence.projection` must be
`"declared"` (`"inferred"` and any other value hard-fail), and the corrected
shared-pack/manifest-mismatch scope — implemented as an equivalent
comparison in the new validator (the second option the plan offered) rather
than extending `inputs_validate.py`'s existing signature, so no existing
caller of `validate_inputs_pack` changed. `abicheck/cli_build_output.py`
registers `abicheck build-output validate DIRECTORY` (`--format text|json`,
exit `0`/`1`/`64`) — a new top-level command group, since `cli_buildsource.py`
registers no commands of its own. `docs/reference/build-output-schema.md`
(new, linked from mkdocs nav) documents the schema + validation rules.
`tests/test_build_output.py` covers the schema round-trip and the full
failure taxonomy, including all three of the plan's required shared-pack
test cases (verified against a hand-authored example directory manually as
well as in the test suite).

### P1.2 — `actions/resolve-baseline` — **done**

Implements ADR-047 §4/§6. New composite Action; consumes a baseline-set
archive/cache entry + `channel`/`target`/`profile` inputs; outputs a
resolved snapshot path (a resolved bundle instead returns staged member
binary paths, per the S14 correction below) or one of the five typed
failure states.
**Input gap, flagged by review: candidate evidence metadata is missing
from this list, and the `incompatible_evidence` outcome cannot be detected
without it.** §6's taxonomy requires `resolve-baseline` to reject a
baseline whose `evidence_producer` disagrees with the candidate's (wrapper
vs. replay, or a stale scanner/tool-version mismatch) — but comparing
requires knowing the *candidate's* evidence producer/tool version, and
`channel`/`target`/`profile` alone don't provide that. **Fix: add the
candidate's `build-output.json` (or at minimum its `evidence_producer`
block and `tool_version`) as an explicit `resolve-baseline` input.** Without
it, an implementation following only `channel`/`target`/`profile` has
nothing to compare the resolved baseline's own `evidence_producer` against,
and `incompatible_evidence` becomes undetectable in practice — the baseline
would reach `compare` regardless of producer mismatch, exactly the
infrastructure-error-treated-as-compatible failure §6 exists to prevent.
**Bundle-scoped resolution requirement, flagged by review:** when the
resolved unit is a bundle (S14), the Action's output must be the staged
member **binaries** from the archive's `binaries/` directory (added to the
baseline archive per §6's S14 correction), not the `.abicheck.json`
snapshots — `abicheck/bundle.py:80-103`'s `build_bundle_snapshot()` reads
real ELF inputs and silently skips non-ELF (including JSON) ones, so
handing `check-target`'s bundle variant snapshot paths instead of binary
paths would make bundle analysis's old side silently empty, not error out.

**Files:** new `actions/resolve-baseline/action.yml`, `run.sh`. Reuses
`actions/baseline/build_manifest.py`'s manifest-reading logic — extract a
shared helper rather than duplicating the schema/digest-check code (avoid
recreating `IMPORT_CYCLE_ALLOWLIST`-style coupling; this is shell, not
Python import structure, but the same "don't duplicate the parsing logic"
principle applies — factor the manifest reader into
`abicheck/buildsource/`-adjacent Python invoked by both Actions' `run.sh` if
the logic is non-trivial).

**Tests:** shell-mapping tests for each of the five failure taxonomy rows
(ADR-047 §6 table); a bundle-scoped resolution fixture asserting binaries
(not snapshots) come back for a bundle target.

**Status:** implemented. New `abicheck/buildsource/baseline_set.py` is the
shared reader/resolver — the "extract a shared helper" the plan asked for,
factored into `abicheck/buildsource/`-adjacent Python rather than duplicated
bash/`jq`. It parses `manifest.json` with the same defensive-`.get()`
philosophy `build_manifest.py` itself uses for snapshot files (a corrupt or
hand-edited manifest never raises, it produces a structured outcome), and
implements `resolve_target()`/`resolve_bundle()` covering all six branches
of §6's table: `not_found` (with the `required`/bootstrap split — `required:
false` + missing baseline is an advisory, non-fatal pass, `required: true`
is a hard failure), `ambiguous` (target missing from the manifest, or a
resolved snapshot/binary missing from disk), `wrong_profile`,
`stale_schema` (`manifest_version` outside `SUPPORTED_MANIFEST_VERSIONS =
{1}`, the only version `build_manifest.py` has ever emitted),
`incompatible_evidence` (comparing the baseline's `fact_set.producer`/
`producer_version` against the candidate's `evidence_producer` block — the
review-flagged input gap above, closed via a new `candidate-build-output`
Action input read only for that block), and `resolved`. The bundle-scoped
correction is implemented as specified: `resolve_bundle()` returns paths to
every member's **staged binary** under the baseline-set's `binaries/`
directory (`BASELINE_BINARIES_DIRNAME`), never a snapshot path — a member
with no staged binary fails the whole bundle resolution as `ambiguous`
rather than silently omitting that member. `actions/baseline` does not
populate `binaries/` yet (that's G30 P1.6, not built here); bundle
resolution is exercised against a hand-authored fixture in the meantime, the
same "defines the contract, no producer yet" scoping G30 P1.1 used for
`build-output.json`.

`actions/resolve-baseline/action.yml`/`run.sh` wrap this in a composite
Action: `baseline-path` accepts either an already-staged directory or a
`.tar.zst`/`.tar.gz`/`.tgz`/`.tar` archive (extracted in `run.sh`, including
a one-level directory descent when the archive nests the baseline-set under
a single subdirectory rather than at its root) — this Action never fetches
from a baseline channel's storage backend itself, that stays the calling
workflow's job per §10, exactly as the "actions/baseline never fetches"
precedent already established. `resolve_baseline.py` is the thin
argparse/stdout-key=value CLI wrapper `run.sh` shells out to, mirroring
`build_manifest.py`'s own pattern. `tests/test_baseline_set.py` (pure,
21 cases) covers every resolver branch directly;
`tests/test_action_resolve_baseline.py` (16 cases) covers the bash
orchestration end-to-end, including one test per §6 failure-taxonomy row,
bundle resolution, and archive extraction (flat and one-level-nested).
`docs/reference/resolve-baseline.md` (new, linked from mkdocs nav)
documents the Action's contract.

### P1.3 — `actions/check-target` — **done**

**Scope note, required by review — this item is not done until the S22/S23
root-action gap is resolved, not merely acknowledged.** ADR-047 §4 flags
that `action.yml`/`run.sh` today have no `--used-by`/`--required-symbol(s)`
input or forwarding path, so `app-consumer`/`plugin-contract` kinds cannot
actually route through the root Action as `kind: library` does. This P1.3
item's scope must include picking and implementing one of the ADR's two
options — extend `action.yml`/`run.sh` with the missing inputs, or have
`check-target` invoke the `abicheck` CLI directly for those two `kind`s —
not just create `actions/check-target`'s own files while leaving that gap
for someone else. Landing P1.3 without resolving this means S22/S23
`checks:` entries generated by P1.4 later still cannot run.

**Second scope note, flagged by review: `baseline: none` (S5) must skip
`resolve-baseline` entirely, not just be documented as doing so in the
ADR.** ADR-047 §6 corrects an earlier draft that routed S5's no-baseline
audit through the normal `check-target` → `resolve-baseline` path (which
would hit `not_found`/bootstrap handling for a check that never wanted a
baseline in the first place) to an explicit bypass: `check-target` must
detect `baseline: none` and skip calling `resolve-baseline` altogether,
invoking the existing audit/scan path directly instead. This item's
implementation must include that branch and a fixture asserting a
`baseline: none` invocation never calls `resolve-baseline` and never
produces a `not_found`/bootstrap-shaped outcome — not just cite the ADR
section as though the behavior is already guaranteed by it.

Implements ADR-047 §4/§7. Composes root `action.yml` + `collect-facts`
(**`phase: verify` for wrapper/clang-plugin evidence, `phase: auto` only
for `producer: replay`** — see ADR-047 §4's "collect-facts composition"
note, flagged by review: `check-target` runs after target
resolution/build-output exists, so it structurally cannot run
`collect-facts phase: prepare`, which must happen before the project's own
build. `check-single.yml`/`check-project.yml` document the caller's
required pre-build `collect-facts phase: prepare` step for S8/S9 as a
separate, earlier step — not something folded into `check-target`.) +
`resolve-baseline`; always emits the report envelope;
`gate-mode: local|deferred|advisory` input. **Identity requirement flagged
by review, corrected in a follow-up review pass:** `check-target` must write
the check's full `check_id` (`target@profile#baseline_channel`, §7) into the
report's own `target_id` field for **every** check, unconditionally — not
only when the run plan has more than one check for the same target. An
earlier version of this item scoped the rule to the multi-check case (S17
multi-profile, S21 multi-channel) "since it only matters once a target has
concurrent checks" — that reasoning was wrong: `aggregate.py`'s manifest
matching is an exact string comparison, so if the manifest projection (P1.4)
always emits `check_id`-shaped IDs but `target_id` is only sometimes set to
`check_id`, the *ordinary* single-check case (S1–S15's majority, including
PVXS) mismatches too (report says `target_id: "libpvxs"`, manifest expects
`"libpvxs@profile#channel"` — required target reported missing).
`abicheck/aggregate.py:642-729`'s `collect_reports` keys reports by
`target_id` (preferring the report's own field over the filename) and
hard-errors on a duplicate, so this identity must be exact and consistent
for every check, with no conditional branch.

**Depth-qualified `check_id`, corrected across two further review rounds —
this task must track the final §7 identity, not either intermediate form
above.** ADR-047 §7 first added `requested_depth` to `check_id` only
*conditionally* (when a run-plan generator detected a collision across its
own `checks[]`), then corrected that to **unconditional**: every
`check_id`/`target_id` always includes `@requested_depth`
(`target@profile#baseline_channel@depth`), because the conditional version
depended on a run-plan generator that doesn't exist for S26 shadow/advisory
checks or any standalone `check-single.yml`/direct `check-target` call —
those have no collision-scanning step, so two independent calls at
different depths would both emit the plain unsuffixed ID and collide
exactly as before. `check-target`'s `target_id`-writing logic (this task)
must implement the **unconditional** depth suffix — always append
`@requested_depth`, no collision detection anywhere — not the plain
`target@profile#baseline_channel` form quoted earlier in this item, and not
a conditional version either. Add a fixture case: two independent
`check-target` invocations on one target/profile/channel at different
`requested_depth`s must produce two distinct, non-colliding `target_id`s
with no shared state between the calls.

**Second required sub-task, flagged by review:** `check-target`'s report
must populate `aggregate`'s *existing* verdict/gate fields, not only the new
ADR-047 §7 ones. `abicheck/aggregate.py`'s `parse_report_verdict` reads
top-level `verdict` (a `Verdict` enum string); its gate parsing
(`GateInfo.from_report_data`/`from_scan_report`) reads a `severity` block or
a scan report's own `exit_code`/`scan_schema_version` — none of these read
`compatibility_verdict` or `policy_gate_decision`, the new field names §7
introduces. Ship one of: (a) `check-target` dual-writes both the legacy
fields (`verdict`, `severity`/`exit_code`) *and* the new ones — the
lower-risk default, since it needs no `aggregate` code change — or (b) a
scoped `aggregate` parser update to also read the new field names. Either
way, this must land before P1.4, or `check-project.yml`'s `aggregate` step
will see every `check-target` report as verdictless/ungated.

**Third required sub-task, flagged by review — the dual-write above must
not defeat `gate-mode: advisory` for mixed plans.**
`abicheck/aggregate.py:425-437`'s `exit_code()` computes the aggregate gate
as `max()` over every included report's legacy `severity.exit_code` — it
has no concept of `gate_mode`/`policy_gate_decision` at all. In a mixed
run-plan (e.g. a required `local`/`deferred` header-depth gate plus an
`advisory` source-depth shadow check on the same target, per this ADR's own
S21/S26 corrections), if `check-target` dual-writes the *real* legacy
`severity.exit_code` for the advisory cell's finding, `aggregate` would
still max it into the blocking gate — a real "advisory" break would fail
CI, exactly the outcome `gate-mode: advisory`'s definition rules out.
**Required fix:** `check-target`'s dual-write must be `gate_mode`-aware —
for `gate-mode: advisory` checks specifically, the legacy `severity`
block's `exit_code`/`blocking` must be written as non-blocking (`0`/`false`)
regardless of the underlying finding, with the real finding still fully
visible in `compatibility_verdict`/`policy_gate_decision` (the new,
richer fields) for human/PR-comment/SARIF consumers. `local`/`deferred`
checks keep the real legacy severity unchanged. Add a fixture: an advisory
cell with a real BREAKING `compatibility_verdict` must not raise
`aggregate`'s computed `exit_code()` above what the required cells alone
would produce.

**Fourth required sub-task, flagged by review:** the internal analysis step
(the nested `uses:` invocation of root `action.yml`) must run with
`continue-on-error: true`, with a trailing step owning `check-target`'s
actual exit code. Without this, a genuine ABI break under
`gate-mode: local` (where the internal step is *supposed* to exit nonzero)
or an operational failure mid-analysis under `deferred`/`advisory` halts
`check-target` before its report-writing step runs at all — the exact
failing checks whose reports `aggregate`/PR comments most need to see.

**Files:** new `actions/check-target/action.yml`, `run.sh`.

**Dependencies:** P1.1, P1.2, P0.3.

**Tests:** end-to-end fixture workflow (`.github/workflows/test-action.yml`
already exercises the root action per AGENTS.md's tag-pinning note on that
file — extend it, don't create a parallel test harness); add a
multi-profile-same-target fixture case asserting `aggregate` does not
collide/error.

**Status:** implemented. **First required sub-task (S22/S23 root-action
gap) — resolved, correcting the ADR's own stale premise:** re-reading
`action.yml`/`action/run.sh` as they exist today (not as ADR-047 §3/§4
describe them) shows `used-by`/`verify-runtime`/`required-symbol`/
`required-symbols` are already declared inputs, already forwarded to
`compare --used-by`/`--required-symbol`/`--required-symbols`
(`action/run.sh:377-386`) — added by #570/#579, both landed *before*
ADR-047 (#610) was written. The ADR's "the root action.yml cannot express
`--used-by`/`--required-symbols` today" finding (§3) was already false at
the time it was written; neither of its two proposed fixes (extend
`action.yml`, or have `check-target` call the CLI directly) was needed.
`check-target` simply exposes its own `target-kind: library|app-consumer|
plugin-contract` input and forwards `consumer-binary`/`contract-file` to
the root Action's existing `used-by`/`required-symbols` inputs when
building its nested `Run analysis` step. The *other* gap ADR-047 §3
correctly identifies — the "library redirect" (an `app-consumer`/
`plugin-contract` target's baseline/candidate lookup must resolve through
its `library` field, while the check's own identity stays the contract
target's name) — is real and is implemented via a separate
`baseline-target` input (defaults to `name`; the caller sets it to the
referenced library's id), keeping `resolve-baseline`'s lookup key and the
report envelope's `check_id`/`target_id` deliberately distinct, per §3.
**Second required sub-task (`baseline: none` bypass) — implemented as a
real branch, not documentation:** `action.yml`'s `Resolve baseline` step
carries `if: inputs.baseline-channel != 'none'`, and every downstream step
(`Run analysis`, the two `collect-facts` steps) conditions on
`inputs.baseline-channel == 'none' || steps.resolve.outputs.outcome ==
'resolved'` — a skipped `resolve` step's outputs are empty strings, so this
expression evaluates correctly with no separate branch needed.
`baseline-channel: none` runs `mode: scan` (no `--against`) instead of
`compare`, matching S5's audit path exactly; `tests/test_action_check_target.py::
TestFinalizeAugmentMode::test_baseline_channel_none_skips_resolve_and_still_augments`
covers it end-to-end at the shell level, and `test-check-target` in
`.github/workflows/test-action.yml` exercises the full YAML composition
(including this bypass's sibling branches) against a real `abicheck
compare` run. **Third/fourth required sub-tasks (unconditional depth-suffixed
`check_id`/`target_id`, dual-write, `gate-mode`-aware neutralization,
`continue-on-error` + trailing finalize step) — all implemented exactly as
specified,** in a new pure module, `abicheck/buildsource/check_report.py`
(`build_check_id`, `resolve_effective_depth`, `augment_report`,
`build_operational_error_report`, `build_bootstrap_report`,
`final_exit_code`), backing a thin CLI wrapper
(`actions/check-target/report_envelope.py`, mirroring
`resolve_baseline.py`'s pattern) that `run.sh` drives. **A real gap found
and closed during implementation, not anticipated by the ADR:** the root
Action's *legacy* (no `--severity-*` flag) compare exit scheme omits the
`severity` JSON block entirely — confirmed by running `abicheck compare`
directly — which would leave `gate-mode: advisory` with nothing to
neutralize and let `abicheck/aggregate.py`'s `GateInfo.from_report_data`
fall back to `legacy_from_verdict(verdict)`, still deriving a blocking gate
from the real `BREAKING`/`API_BREAK` verdict regardless of `gate-mode`.
Fixed by giving `check-target`'s own `severity-preset` input a `'default'`
default (root `action.yml`'s own input is deliberately left unset) instead
of leaving it unset, so the nested `Run analysis` step always requests the
severity-aware scheme and a `severity` block is always present to dual-write
and (for `advisory`) neutralize. `deferred` reports keep that block's real
`exit_code`/`blocking` untouched by design — `check-project.yml`'s future
trailing `aggregate` job (P1.4) needs the real value to compute the gate
centrally; only `advisory` zeroes it. Verified end-to-end by hand (not only
via the test suite): staged a real `manifest.json` + snapshot, ran
`actions/resolve-baseline/run.sh`, then a real `abicheck compare
--severity-preset default`, then `actions/check-target/run.sh`'s finalize
step, for all three `gate-mode` values, confirming the exit
codes/persisted-severity behavior documented above. The S14 bundle-scoped
path is implemented as ADR-047 §8's correction actually resolves it: no
separate "bundle compare" CLI command exists (`compare-release` is
intentionally unregistered on `main`, invoked only by `compare`'s own
directory-operand fan-out per ADR-037 D7), so `kind: bundle` simply hands
`resolve-baseline`'s `binaries-dir` output to the same nested `Run analysis`
step as a directory `old-library` — `compare`'s existing directory fan-out
handles the rest. `actions/baseline` still doesn't stage a `binaries/`
directory (G30 P1.6, not built here), so this path is exercised against a
hand-authored fixture in the same "defines the contract, no producer yet"
scoping P1.1/P1.2 already used. **The multi-profile-same-target `aggregate`
non-collision fixture is deferred to P1.4, not skipped:** `check-target` on
its own never invokes `aggregate` or produces more than one report per
call, so there is nothing to fan in yet; `build_check_id`'s own uniqueness
across `requested_depth` is unit-tested here
(`tests/test_check_report.py::TestBuildCheckId::
test_unconditional_depth_suffix_disambiguates_shadow_checks`), and the real
multi-check `aggregate` fixture belongs with P1.4's `run-plan.json`
generator, which is what actually produces more than one `check-target`
call to fan in. `abicheck/schemas/compare_report.schema.json` gained
`compatibility_verdict`/`policy_gate_decision`/`check_evidence_coverage`/
`operational_errors`/`publication`/`baseline_bootstrap`/`project`/
`head_sha`/`base_ref`/`tool_version`/`action_version` as additive/optional
properties (`report_schema_version` bumped `2.12` → `2.13`,
`scan_schema_version` `1.1` → `1.2`, both documented in
`abicheck/schemas/__init__.py`); `docs/reference/check-target.md` (new,
linked from mkdocs nav) documents the full contract, and
`docs/reference/resolve-baseline.md`'s "not built yet" status note is
updated to point at it. `tests/test_check_report.py` (100% line/branch
coverage of `check_report.py`) covers the pure logic;
`tests/test_action_check_target.py` covers `validate-inputs.sh`/`run.sh`'s
bash orchestration end-to-end, including every `gate-mode` × outcome
(resolved/operational-error/bootstrap) combination and the
effective-depth-degradation branch; `test-check-target` in
`.github/workflows/test-action.yml` is the required end-to-end fixture job,
exercising the real nested `uses:` composition (`resolve-baseline` → the
root Action → the finalize step) against real `abicheck compare` output,
not simulated env vars.

**Two real, confirmed bugs found and fixed via PR review after initial
implementation (PR #625), not anticipated above:**

- **Effective-depth degradation was computed from the wrong signal.** The
  first implementation guessed `effective_depth`/`check_evidence_coverage`
  from whether the composed `collect-facts` step reported readiness — but a
  caller can legitimately reach build/source depth via a direct `build-info`/
  `sources` input with **no** `collect-facts` composition at all (the
  "producer-less" path this same page's input table already documents). That
  heuristic misreported a real build/source-depth result as `degraded` purely
  because no producer step ran (Codex review). **Fixed by reading the
  authoritative signal the tool itself already emits**, not inferring one:
  `abicheck compare --format json` always carries `old_evidence_depth`/
  `new_evidence_depth` (`cli_compare_helpers._fold_evidence_depth_into_json`,
  unconditional for JSON output) and `scan`'s JSON carries `level.depth` — the
  real depth *achieved*, independent of how it was achieved. Renamed
  `resolve_effective_depth(requested_depth, evidence_ok, degraded_reason)` to
  `derive_effective_depth(report, requested_depth)`, dropped the
  `evidence-ok`/`degraded-reason` plumbing from `report_envelope.py`/`run.sh`/
  `action.yml`'s finalize step entirely (the `collect-facts` composition
  steps themselves are unchanged — they still produce the pack the analysis
  step consumes; only the *finalize* step's now-redundant success/readiness
  reads were removed). For `compare`, the shallower of the two sides is the
  check's own achieved depth (a build/source result on only one side isn't a
  build/source-depth comparison); a report deeper than requested is reported
  honestly as achieved, not capped down to the request.
- **Nested `uses: ./x` steps do not resolve against this Action's own
  repository when consumed externally — a real, confirmed architectural gap,
  not a false positive.** Verified independently (GitHub Community Discussion
  actions/runner#1348 "Local composite actions always relative to top level
  repository"; confirmed `uses:` accepts no expressions at all, ruling out a
  dynamic-reference workaround) before fixing: a relative `uses: ./x` step
  inside a composite Action **always** resolves against `$GITHUB_WORKSPACE`
  — the *calling workflow's* own checkout — never against the repository
  that contains the composite Action doing the `uses:`. `check-target`'s
  nested `uses: ./actions/resolve-baseline`/`./actions/collect-facts`/`./`
  (root Action) therefore only ever worked because the added
  `test-check-target` fixture happens to invoke `check-target` from *within*
  `abicheck/abicheck`'s own workflow — the one case where the caller's
  checkout and this Action's own repository are the same thing. A real
  external consumer (`uses: abicheck/abicheck/actions/check-target@v1` from
  their own repository, exactly as this page's own examples show) would have
  every nested `uses:` fail before ever reaching baseline resolution. Fixed
  by adding an unconditional `Checkout abicheck (for nested Action
  composition)` step (first thing `check-target` does, before any nested
  `uses:`) that checks out `${{ github.action_repository ||
  github.repository }}` at `${{ github.action_ref || github.sha }}` into a
  side directory (`.abicheck-check-target-src`, `persist-credentials:
  false`), and rewrote every nested `uses:` to reference that directory
  instead of bare `./`. The `||` fallback makes this correct for both the
  external-reference case (`github.action_repository`/`github.action_ref`
  set) and the local same-repository case
  (`.github/workflows/test-action.yml`'s own `uses: ./actions/check-target`,
  where both are empty) without a conditional branch — `uses:` cannot itself
  be an expression, so the checkout step had to be unconditional instead.

**A third bug, in the fix for the second one above, caught by the real CI
run of the new `test-check-target` fixture (job 89082423642) rather than by
review or local testing — the self-checkout step read back its own
identity, not check-target's.** `github.action_repository`/`github.
action_ref` describe whichever action is *about to run* — the runner
updates them while preparing each step, including composite-nested ones,
**before** evaluating that same step's own `with:` expressions. The
`Checkout abicheck (for nested Action composition)` step's own `with:`
block read `${{ github.action_repository || github.repository }}`/`${{
github.action_ref || github.sha }}` directly — but by the time those
expressions were evaluated, the context had already flipped to describe
*that step's own target*, `actions/checkout@v6`. The real CI run confirmed
this exactly: the step's resolved `with:` logged `repository:
actions/checkout` / `ref: v6`, checking out the wrong repository entirely
and leaving `.abicheck-check-target-src/actions/resolve-baseline` empty —
`Resolve baseline` then failed with "Can't find 'action.yml' ... Did you
forget to run actions/checkout". **Fixed** by adding a `Capture this
Action's identity` step (`id: identity`, a plain `run:` step, the first
thing `check-target` does) that reads `github.action_repository`/`github.
action_ref` into `$GITHUB_OUTPUT` before any nested `uses:` step has a
chance to overwrite them, and pointing the checkout step's `with:` at `${{
steps.identity.outputs.repository }}`/`${{ steps.identity.outputs.ref }}`
instead of reading the raw context directly. The `action-version` input's
default (evaluated once, before any of check-target's own steps run —
a different, earlier timing than the checkout step's `with:`, so it was
never affected by this specific bug) gained the same `||` fallback for
consistency, so a local same-repo test run reports a real identity instead
of an empty `"@"`.

**A fourth, fifth, and sixth real bug, all caught by a second Codex review
round, all fixed:**

- **The root `action.yml`'s `compare` mode branch never forwarded
  `sources`/`build-info`/`compile-db`/`build-config`/`depth` at all** —
  confirmed by grepping `action/run.sh`: those five inputs were only wired
  in the `dump`/`scan` branches (`action.yml`'s own input descriptions said
  "Used by scan and dump modes," which was accurate but incomplete —
  `compare` genuinely supports `--sources`/`--build-info`/`--depth`/
  `--config` directly, confirmed via `abicheck compare --help`). This meant
  a `check-target` build/source-depth check against a real baseline (the
  normal, non-audit `compare`-mode path) had no way to actually reach the
  CLI's evidence flags — `requested-depth: source` would silently only ever
  achieve `headers`, regardless of what `sources`/`build-info` were set to.
  Fixed by adding the missing forwarding to `action/run.sh`'s `compare`
  branch, scoped to the **new (candidate) side only**
  (`--sources new=...`/`--build-info new=...`, falling back to `compile-db`
  when `build-info` is unset, matching `dump` mode's own fallback) — the old
  side's evidence, if any, is expected to already be embedded in whatever
  baseline snapshot was resolved; this Action has no live old-side source
  tree to point at in `compare` mode. `action.yml`'s five input descriptions
  updated to document the new `compare`-mode support. This is a general fix
  to the root Action, benefiting any direct `compare`-mode caller wanting
  build/source-depth evidence, not `check-target`-specific.
- **A collect-facts verify/replay failure was never checked before running
  analysis** — `collect_verify`/`collect_replay` run with
  `continue-on-error: true` (correctly, so the finalize step always runs),
  but the `Run analysis` step's own `if:` only checked
  `resolve.outcome == 'resolved'`, never `collect_verify`/`collect_replay`'s
  outcome — so a broken/empty wrapper or clang-plugin pack (a real
  `collect-facts phase: verify` failure) would still be handed to `compare`
  as `--build-info`, silently running the comparison against invalid
  evidence and reporting a plain degraded-or-normal result instead of the
  operational error it actually is. Fixed by adding
  `steps.collect_verify.outcome != 'failure' && steps.collect_replay.outcome
  != 'failure'` to the analysis step's `if:`, and giving `run.sh`'s finalize
  logic two new, specific `operational-error` branches (ahead of the
  generic "analysis produced no report" catch-all) so the resulting report
  names collection failure specifically, not an ambiguous unexplained gap.
- **`validate-inputs.sh` never validated `evidence-producer`** — every other
  enum-like input (`kind`/`target-kind`/`gate-mode`/`requested-depth`) is
  checked up front, but a misspelled `evidence-producer` value would just
  silently fall through the `case` statement composing `collect-facts`
  (neither `wrapper`/`clang-plugin`/`replay` branch matches), skipping fact
  collection entirely with no error — a build/source-depth check would then
  silently run at whatever depth the analysis naturally reached, never
  telling the caller their typo was ignored. Fixed by adding the same
  `case` validation for `evidence-producer`
  (`''`/`wrapper`/`clang-plugin`/`replay`) as every other enum input already
  has.

Also, separately: the two synthesized envelope builders
(`build_operational_error_report`/`build_bootstrap_report`) wrote
`compatibility_verdict: null` — schema-invalid, since the schema declares
that field a plain string enum with no null alternative (Codex review,
third round). Fixed by omitting the key entirely for those two cases
instead (matching how `augment_report` already only sets it when there's a
real value) — the broader "these two envelope shapes don't satisfy compare's
full `required` field list either" question Codex also raised is real but
out of scope here, matching the same precedent ADR-047 §7 already
established for the pre-existing `verdict: "ERROR"` enum gap (a known,
accepted limitation of the sentinel-envelope pattern, not something this
task resolves).

`tests/test_action_run_sh_compare_build_source.py` (new) runs the real
`action/run.sh` end-to-end against a fake `abicheck` stub on `$PATH` to
prove the evidence-forwarding fix reaches the actual command line, not just
that the shell logic looks right on paper; `tests/test_action_check_target.py`
gained cases for both new collect-facts-failure branches and the
`evidence-producer` validation.

A fourth round of Codex review then caught a regression the evidence-
forwarding fix above (73f1143) itself introduced: **`action.yml` always
sets `depth: inputs.requested-depth` on the analysis step**, and for
`kind: bundle` (or any directory/package comparison), `old-library`/
`new-library` are directories, which routes `compare` through the CLI's
per-library release fan-out (ADR-037 D7) — and that fan-out's own
`_reject_evidence_flags_for_set_inputs` rejects `--depth`/`--sources`/
`--build-info` outright as a `UsageError`, since the per-library fan-out
never collects inline build/source evidence for a set input. Confirmed
by reading `abicheck/cli_resolve.py`'s `_reject_evidence_flags_for_set_inputs`
and its call site in `cli_compare_helpers.py` (fires whenever either operand
classifies as `directory`/`package`). Before this fix, **every** `kind:
bundle` check-target invocation with a resolved baseline would fail as a
hard usage/orchestration error before ever producing the intended bundle
comparison — `requested-depth` stays required in the envelope identity
regardless, only the CLI flag was wrong to force. Fixed by gating the
`--sources`/`--build-info`/`--config`/`--depth` block in `action/run.sh`'s
`compare` branch on `action/run.sh`'s existing `_is_release_style_operand`
helper (already used a few lines above to skip `--secondary-format` for the
same directory/package shape) — checked against both `old-library` and
`new-library`, matching the CLI's own either-side rejection condition.
`tests/test_action_run_sh_compare_build_source.py` gained a
`TestCompareModeSkipsEvidenceFlagsForDirectoryOperands` class proving the
flags are omitted when either operand is a directory, even when the
corresponding evidence inputs are set.

A fifth round of Codex review then caught three more issues, all fixed in
one follow-up commit:

- **The directory/package guard above over-suppressed `--config` too** —
  `--config` is not one of the flags `_reject_evidence_flags_for_set_inputs`
  actually rejects (`_EVIDENCE_SET_INPUT_FLAGS` lists only `depth`/`sources`/
  `build_info`); the release fan-out still consumes the project
  `.abicheck.yml` for severity/scope/suppression/exit-code settings
  (`_resolve_compare_config` runs before the directory/package dispatch), so
  a bundle caller's `build-config` was being silently dropped. Fixed by
  pulling `--config` out of the release-style-operand guard entirely — it
  now always reaches the CLI, matching every other compare mode.
- **`target-kind: app-consumer`/`plugin-contract` combined with
  `baseline-channel: none` silently ran an unscoped audit** —
  `baseline-channel: none` routes the analysis step to `scan`, but `scan`
  has no `--used-by`/`--required-symbols` equivalent at all (confirmed via
  `abicheck scan --help`); those flags only exist in the `compare` branch of
  `action/run.sh`. A contract check with no baseline therefore ran as a
  plain unscoped scan under the contract target's name and could pass
  without ever checking the consumer/plugin contract it claimed to. Fixed
  by rejecting the combination up front in `validate-inputs.sh` — there is
  no way to honor a contract scope without a two-sided comparison, so
  failing loud (rather than trying to thread `--used-by`/`--required-symbols`
  through a mode that structurally can't use them) is the correct fix.
- **The operational-error/bootstrap sentinel envelopes still didn't validate
  against `compare_report.schema.json`** — the earlier `compatibility_verdict:
  null` fix (third review round, above) only addressed one field; the schema
  unconditionally required compare-specific fields (`library`, `old_file`,
  `summary`, `changes`, `policy`, `suppression`, `detectors`, `confidence`,
  `evidence_tier`, `evidence_tiers`, ...) and restricted `verdict` to the
  five real `Verdict` values, so `build_operational_error_report`/
  `build_bootstrap_report`'s `verdict: "ERROR"`/`"NO_BASELINE"` envelopes —
  and the pre-existing per-library release fan-out's own `verdict: "ERROR"`
  shape in `cli_compare_release.py` (not new to this task) — never actually
  validated, confirmed by running `jsonschema.validate` against both shapes
  by hand. The "out of scope, mirrors an accepted ADR-047 §7 gap" reply
  given in the third round was too quick to wave this away as unfixable;
  Codex's fourth pass on it correctly pushed back with concrete schema
  evidence. Fixed properly this time: `compare_report.schema.json`'s
  top-level `required` now only demands `report_schema_version`/`verdict`,
  an `allOf`/`if`/`then` requires the full compare-specific field list only
  when `verdict` is one of the five real values, and `verdict`'s enum grew
  `ERROR`/`NO_BASELINE` (additive, consistent with the existing
  `report_schema_version` MINOR-bump convention for new enum members).
  Verified against all four shapes by hand: a full compare report validates
  and still rejects a truncated one, and both sentinel envelopes (plus the
  minimal pre-existing release-fan-out `{library, verdict: "ERROR", error}`
  shape) now validate. `docs/schemas/v1/compare_report.schema.json`
  re-synced via `scripts/publish_schemas.py`.

The same review round separately caught that the schema fix above didn't
cover every report shape `augment_report` can receive: a successful
`baseline-channel: none` scan report (its own `scan_schema_version` shape --
`level`/`risk`/`coverage`/... , no `library`/`old_file`/`summary`/`changes`)
or a `kind: bundle` directory-compare report (the per-library release
fan-out's own summary shape -- `verdict`/`old_dir`/`new_dir`/`libraries`,
also no singular `library`/`old_file`/`summary`/`changes`) still got
`report_schema_version` stamped onto them unconditionally, same as a normal
single-pair compare report. Confirmed by reading `scan_engine.py`'s report
dict and `cli_compare_release_helpers.py`'s `_format_release_json` by hand
— neither shape has ever had a schema, let alone this one. A downstream
validator selecting a schema by `report_schema_version`'s presence would
pick `compare_report.schema.json` for either shape and reject it against
that schema's real-verdict branch. Fixed in `augment_report`: a report
carrying `scan_schema_version` gets that field bumped to the current
`SCAN_SCHEMA_VERSION` instead of also gaining `report_schema_version`; a
report shaped like the release fan-out's summary (`libraries` + `old_dir`
present) gets neither schema marker, since that shape has never had one to
claim. ADR-047 §7's identity/policy-gate-decision fields (`check_id`,
`policy_gate_decision`, etc.) are unaffected either way — only the schema
marker choice is shape-aware now. New
`test_scan_report_gets_scan_schema_version_not_report_schema_version` /
`test_bundle_release_report_gets_no_schema_version_stamp` cases in
`tests/test_check_report.py`.

A sixth review round on the same commit caught two more real issues:

- **A `kind: bundle` (or any directory/package `compare`) request for
  build/source-depth evidence was silently downgraded instead of failing** —
  the directory/package guard added earlier (fifth round) correctly stopped
  forwarding `--depth`/`--sources`/`--build-info` to avoid the CLI's hard
  rejection, but that meant a caller who explicitly asked for
  `requested-depth: build`/`source` (or supplied `--sources`/`--build-info`/
  `--compile-db` directly) had that request silently dropped: the
  comparison would still run and report a normal/clean result, just without
  ever actually gathering the requested evidence — a source-only break
  could be missed with no signal anything was wrong (`effective_depth` even
  falls into `derive_effective_depth`'s "no depth signal in report" branch,
  which trusts the *request* rather than reporting a real degradation, since
  the release fan-out's own JSON never carries `old_evidence_depth`/
  `new_evidence_depth` at all). Fixed in two places: `action/run.sh` now
  exits with an explicit error when a directory/package operand is combined
  with `--depth build`/`source` or an explicit `--sources`/`--build-info`/
  `--compile-db` (covers any direct caller of the root Action, not just
  check-target) — `--depth binary`/`headers` against a directory/package
  operand is untouched, since nothing requested there is actually
  unservable. `actions/check-target/validate-inputs.sh` additionally
  rejects `kind: bundle` combined with `requested-depth: build`/`source` up
  front, before `resolve-baseline`/`collect-facts` even run, for a cheaper
  and clearer failure than waiting for the nested analysis step to fail.
  New `TestCompareModeFailsFastOnUnservableDirectoryEvidenceRequest` class
  in `tests/test_action_run_sh_compare_build_source.py` (four failure cases
  plus one confirming `headers` depth still succeeds) and
  `test_bundle_kind_rejects_build_depth`/`test_bundle_kind_rejects_source_depth`/
  `test_bundle_kind_allows_headers_depth` in `tests/test_action_check_target.py`.
- **`augment_report`'s successful-path `publication` default was simply
  false** — it defaulted every successful report's `publication` to
  `{"state": "published", "channels": ["job_summary"]}`, but check-target's
  own "Run analysis" step always passes `add-job-summary: 'false'`,
  `pr-comment: 'false'`, `upload-sarif: 'false'` to the nested root Action
  (confirmed by reading `action.yml`), and the finalize step itself only
  writes the report JSON to disk plus `GITHUB_OUTPUT` values — nothing is
  actually published anywhere for a real check-target run. The
  operational-error/bootstrap sentinel envelopes already got this right
  (`{"state": "skipped", "channels": []}`); only the common success-path
  default was wrong. Fixed to match. New
  `test_publication_defaults_to_skipped_not_a_false_claim` case in
  `tests/test_check_report.py`.

A sixth round of Codex review caught two more issues, both fixed in one
follow-up commit: the fixed report filename risked collisions across
multiple `check-target` invocations in the same job, and `augment_report`'s
operational-error classification missed scan guard sentinels.

- **A fixed `check-target-report.json` filename collides across multiple
  `check-target` invocations in the same job** — e.g. the same target
  checked against two baseline channels, or several targets checked without
  per-step output directories. Each call overwrote the previous one's
  report file, so an earlier step's own `report-path` output would end up
  pointing at a *later* check's envelope by the time anything read it.
  Fixed: the filename is now scoped to
  `check-target-report-<name>-<profile>-<baseline_channel>-<requested_depth>.json`,
  sanitized via `tr -c 'A-Za-z0-9._-' '_'` (a slug helper) so an
  unsanitized identifier component can't affect the filesystem path
  regardless of when Python-side identifier validation runs. Deriving the
  name from the check's own already-unique identity components was chosen
  over adding a new caller-specified output path input, since no caller
  input was actually needed. New
  `test_two_invocations_in_the_same_job_do_not_overwrite_each_others_report`
  in `tests/test_action_check_target.py` runs two finalize calls against
  the same `tmp_path` and asserts both report files survive with distinct
  content. `docs/reference/check-target.md` updated to document the
  filename pattern and point at the `report-path` output instead of
  hard-coding the old name. (Mechanically, every test hard-coding the old
  filename was switched to read `outputs["report-path"]` instead, since the
  filename is no longer predictable from the identity fixture alone.)
- **`augment_report`'s operational-error classification only checked
  `verdict == "ERROR"`, missing scan guard sentinels** — a
  `baseline-channel: none` scan run that exceeds `--budget` (or hits
  `service_scan.py`'s other guard, `EVIDENCE_CONTRACT_ERROR`) gets
  `verdict: "BUDGET_OVERFLOW"`/`"EVIDENCE_CONTRACT_ERROR"` and a nonzero
  `exit_code`, and the root Action's own `run.sh` already treats
  `BUDGET_OVERFLOW` as an always-failing guard (never gated by a
  `fail-on-*` flag, unlike `BREAKING`/`API_BREAK`) — confirmed by grepping
  `action/run.sh`. But neither of these verdict strings is `"ERROR"` or one
  of the five real `Verdict` values, so the old classifier fell through to
  the "else: leave `operational_errors` empty" branch, and
  `report_envelope.py`'s own `operational_error = report.get("verdict") ==
  "ERROR"` check missed it too — meaning `gate-mode: deferred`/`advisory`
  would return exit `0` for a scan that never actually completed its
  comparison, silently turning an infrastructure guard trip into a green
  check, in direct contradiction of `final_exit_code`'s own documented rule
  that "deferred only defers the *compatibility* verdict's effect on exit
  code, never operational errors." Fixed: `augment_report` now treats *any*
  verdict outside the five real `Verdict` values (not just the literal
  `"ERROR"`) as operational, populating `operational_errors` with a new
  `"scan_guard_triggered"` kind for the non-`"ERROR"` case;
  `report_envelope.py` now derives its own `operational_error` flag by
  reusing `augment_report`'s already-computed `operational_errors` list
  rather than re-deriving it from `verdict` a second, narrower way. New
  `test_scan_guard_sentinel_verdicts_are_operational_errors` (parametrized
  over both guard strings) in `tests/test_check_report.py`, and
  `TestFinalizeScanGuardSentinel::test_budget_overflow_always_fails_regardless_of_gate_mode`
  (parametrized over all three `gate-mode` values) in
  `tests/test_action_check_target.py`.

A seventh round of Codex review caught two more issues, both fixed in one
follow-up commit:

- **A removed-library gate on a bundle/directory compare could silently
  pass `gate-mode: local`** — `abicheck compare`'s per-library release
  engine gives `--fail-on-removed-library` its own dedicated exit code (8),
  applied "in preference to the severity code"
  (`cli_compare_release_helpers._exit_compare_release`'s own docstring) —
  meaning the persisted JSON report's `severity.exit_code` can read `0`
  (e.g. `verdict: COMPATIBLE_WITH_RISK`, the removal only shows up in
  `unmatched_old`) even though the real CLI process exited 8. Since
  `augment_report`'s `real_exit_code` was computed purely from the report
  body, and `report_envelope.py`'s own `real_exit_code` variable did the
  same, `gate-mode: local` would read `real_exit_code: 0` and both the
  composite job exit code and the persisted `policy_gate_decision` would
  read as a clean pass — silently allowing a removed library the caller
  explicitly asked to gate on. Confirmed by reading
  `_exit_compare_release`'s docstring and code directly. Fixed: the nested
  root Action's own real `exit-code` output is now captured
  (`actions/check-target/action.yml`'s finalize step gains
  `ANALYSIS_EXIT_CODE: ${{ steps.analysis.outputs.exit-code }}`), forwarded
  through `run.sh` (defensively defaulted to 0 for anything not a clean
  non-negative integer) and `report_envelope.py`'s new
  `--analysis-exit-code` flag, and folded into `augment_report`'s
  `real_exit_code` via `max()` alongside whatever the report body itself
  says — the same precedence pattern `_exit_compare_release` already uses
  internally. `report_envelope.py`'s own `real_exit_code` (used for
  `final_exit_code()`) applies the identical fold, so the persisted
  `policy_gate_decision` field and the actual composite exit code agree.
  Scoped deliberately to `gate-mode: local`'s correctness, the most severe
  form of the bug (the job silently passed outright); `gate-mode: deferred`
  still defers to `check-project.yml`'s trailing `aggregate` job, and
  `abicheck/aggregate.py`'s own `GateInfo.from_report_data` reads only the
  persisted `severity.exit_code` (unaffected by this fix, since that field
  is deliberately left untouched — only the gate *decision* folds in the
  analysis exit code, not the persisted severity block itself) — so a
  removed-library gate on a `deferred` bundle check can still be missed by
  a later `aggregate` pass; that gap is in `aggregate.py` itself, predates
  this task, and applies to any consumer of `compare`'s bundle JSON output
  relying on `severity.exit_code` alone, not something check-target
  introduced or is positioned to fix on its own. New
  `test_analysis_exit_code_overrides_a_clean_severity_block`/
  `test_analysis_exit_code_of_zero_does_not_flip_a_clean_report` in
  `tests/test_check_report.py`, and
  `test_analysis_exit_code_folds_into_local_gate_even_with_clean_severity`
  (full `run.sh` + `report_envelope.py` integration) in
  `tests/test_action_check_target.py`.
- **`compare`/`scan` modes never forwarded cross-compiler flags** —
  `--gcc-path`/`--gcc-prefix`/`--gcc-options`/`--sysroot` are documented
  root-Action inputs and `abicheck compare --help-all`/`abicheck scan
  --help` both expose the equivalent CLI flags, but `action/run.sh` only
  ever wired them into `dump` mode's branch — confirmed by grepping the
  file. A `check-target` compare/scan needing a cross compiler or sysroot
  to parse headers correctly would silently fall back to the host
  toolchain/includes and could produce false ABI results for cross-target
  libraries. Fixed by adding the same four `add_single_flag` calls to both
  the `compare` and `scan` branches (check-target's own `action.yml`
  already forwarded these inputs to the nested root Action's `with:` block
  — the gap was entirely inside `action/run.sh`). New
  `TestCompareModeForwardsCrossCompilerFlags`/
  `TestScanModeForwardsCrossCompilerFlags` classes in
  `tests/test_action_run_sh_compare_build_source.py`, each running the real
  `run.sh` end-to-end against a fake `abicheck` stub to prove the flags
  reach the actual command line.

### P1.4 — `check-single.yml` / `check-project.yml` reusable workflows

Implements ADR-047 §4/§5 (`run-plan.json` generation + matrix + trailing
`aggregate` job for `check-project.yml`). **Includes a required sub-task
flagged by review**: `run-plan.json`'s `checks[]` schema is not
wire-compatible with `abicheck aggregate --manifest`'s existing
`{"targets": [{"id", "required"}]}` shape (`abicheck/aggregate.py:753-769`
hard-errors on anything else). The `check-project.yml` aggregate step must
project `run-plan.json` down to that shape before invoking `aggregate
--manifest` — using each check's `check_id` (not the bare target name) as
`targets[].id`, matching P1.3's report-identity requirement above, so S17/S21
don't collide in `aggregate`'s duplicate-target-id check. Implement as
either an inline `jq`/Python step in the workflow, or a small
`abicheck run-plan to-aggregate-manifest` CLI helper if the projection turns
out to need real validation logic beyond the `check_id` derivation. Decide
which during implementation; do not skip this and assume `run-plan.json` can
be passed straight through, and do not project down to bare target names.

**Second required sub-task, flagged by review:** in `gate-mode: deferred`
(ADR-047 §7), an individual matrix cell is *expected* to fail its own job on
an operational error — that visibility is the point. Plain GitHub Actions
`needs:` semantics skip a dependent job when any dependency fails, and a
skipped job reports `success` — so the trailing `aggregate` job in
`check-project.yml` **must** be defined with `if: always()` (or
`!cancelled()`), never a bare `needs:` with no `if:`. Without this, one
matrix cell's operational failure silently skips the aggregate job and the
branch-protection-required status goes green with a missing target —
exactly the failure mode ADR-047 is meant to close. Cover this with a
fixture-workflow test that deliberately fails one matrix cell and asserts
the aggregate job still runs and reports the failure.

**Third required sub-task, flagged by review — the same always-on problem
applies one step earlier.** `check-project.yml`'s per-cell report-artifact
upload step (the pattern already used in
`docs/user-guide/github-action-recipes.md`) runs *after* `check-target` in
each matrix job. Under `gate-mode: deferred`, `check-target`'s own exit
(per P1.3's continue-on-error fix) can still fail the matrix *job* on an
operational error even though it wrote its report — and a subsequent step
in a failed job is skipped by default unless it too carries
`if: always()`/`!cancelled()`. Without that on the upload step specifically
(not just on the trailing `aggregate` job), the report artifact for a
failing cell never gets uploaded, and `aggregate` sees a missing target
instead of the promised operational-error report. Both must carry an
always-on condition: the aggregate job (already required above) and each
matrix job's report-upload step.

**Files:** `.github/workflows/check-single.yml`, `.github/workflows/check-project.yml`,
possibly a new small CLI helper per the sub-task above.

**Dependencies:** P1.3, **P1.5** — corrected, flagged by review: this item
generates `run-plan.json`/the matrix from `.abicheck.yml`'s `targets:`/
`profiles:` block, which P1.5 defines. An earlier draft listed only P1.3 as
a dependency while P1.5's own entry said it "should land before P1.4" —
inconsistent instructions that would leave an implementer with no real
config schema to generate the matrix from. P1.5 must land first.

### P1.5 — `.abicheck.yml` `targets:`/`profiles:`/`baseline:` block — **done**

Implements ADR-047 §3. Config schema extension + `abicheck/policy_file.py`
(or wherever `.abicheck.yml` is parsed) support; `docs/reference/config-file.md`
update. **Real design gap this item must close, flagged by review:** §3's
excerpt declares which baseline channels *exist* but not which
channel(s)/depth/`required` policy each target/profile actually runs —
P1.4's run-plan generator needs that per-check assignment and none of the
schema shown so far provides it. This item must design and add a `checks:`
list (per target, or per `bundle`) naming explicit
`{channel, depth, required, gate_mode}` tuples — supporting S21/S26's
same-target-multiple-channels-or-depths case — not just the
`targets:`/`profiles:`/`baseline: channels:` blocks ADR-047 §3 already
shows. Do not treat those existing excerpts as a complete config schema;
this new `checks:` shape is the missing piece P1.4 actually consumes.

**Dependencies:** none of the above strictly. **Must land before P1.4** —
not merely "should" — since P1.4 depends on this item (corrected above);
sequence P1.5 ahead of P1.4 in the actual PR order, not just in ordinal
numbering.

**Status:** implemented. New `abicheck/buildsource/project_targets.py`
defines `TargetSpec`/`BundleSpec`/`ProfileSpec`/`BaselineChannelSpec`/
`CheckSpec` (the `{channel, depth, required, gate_mode, profiles}` tuple
that closes the gap above) plus `ProjectTargetsConfig.from_dict()` (strict
structural/type validation, ADR-043 convention — raises immediately on an
unknown key or wrong-typed value, matching `BuildConfig`'s own strict
`.abicheck.yml` parsing) and `validate_project_targets()` (cross-reference/
semantic validation: kind-specific required/forbidden fields per §3's
`library`/`app-consumer`/`plugin-contract` discriminator, the
`app-consumer`/`plugin-contract` → `library` redirect rule resolving both
of §3's "unstated rule" corrections, bundle membership agreement, and every
`checks[].channel`/`profiles[]` reference resolving — or the `channel:
"none"` sentinel for a §6 S5 no-baseline audit check). Every
target/bundle/profile/channel id is validated against the same
`[A-Za-z0-9][A-Za-z0-9._-]*` charset the report-identity envelope (§7)
already requires for `check_id` components, so no id produced here can
later become an unparseable `check_id`.

`targets`/`bundles`/`profiles`/`baseline` are registered as recognized
`.abicheck.yml` top-level keys in `BuildConfig._KNOWN_TOP_KEYS`
(`abicheck/buildsource/inline.py`) — the same recognized-but-not-parsed
treatment already given `risk_rules`/`crosschecks` — so their presence
never trips `BuildConfig`'s own strict unknown-key error, but `BuildConfig`
does not parse them itself; `project_targets.py`'s own loader
(`load_project_targets_config`) re-reads the same file. This keeps
`inline.py` (already at the file-size soft-limit warning) unchanged in
size and matches the existing sibling-module-owns-its-block precedent.

**Profile-scoping gap resolution, per the module's own docstring:** rather
than assume the naive cross-product of every `checks:` entry with every
`contract: true` profile is safe (§3 explicitly warns this produces
impossible cells for a target that doesn't exist on every profile), each
`checks:` entry carries an *optional* explicit `profiles:` selector
(validated against declared `profiles:` ids when set); when omitted, this
schema deliberately does not resolve a profile list itself — G30 P1.4's
run-plan generator is the one responsible for deriving the actual
`(target, profile)` cells from each profile's own `build-output.json`
`targets[]` list (the ADR's second, safer option), never from a blind
cross-product. This module's validator cannot enforce that downstream
behavior; it documents the split explicitly rather than silently picking
the unsafe default.

New `abicheck project-targets validate [CONFIG]` CLI command
(`abicheck/cli_project_targets.py`, registered as a new top-level command
group exactly like P1.1's `build-output validate` — `tests/
test_cli_root_surface.py`/`test_cli_surface_diff.py` updated to include it
in the public command set, and `scripts/check_ai_readiness.py`'s
`IMPORT_CYCLE_ALLOWLIST` documents it joining the existing by-design
CLI-registration SCC the same way `cli_build_output`/`cli_aggregate`
already do). No producer/run-plan-generator tooling yet — `dump`/
`compare`/`scan` do not read this block at all, matching P1.1's same
"defines the contract, no consumer yet" scope. `docs/reference/
project-targets-schema.md` (new, linked from mkdocs nav) documents the
full schema; `docs/reference/config-file.md`'s top-level key table and
`risk_rules:`/`crosschecks:` section gain the four new keys, pointing at
the new page rather than duplicating it. `tests/test_project_targets.py`
covers the schema round-trip, `BuildConfig`'s recognition of the new keys,
the from_dict structural-error taxonomy, every cross-reference validation
rule (including the exact ADR-047 §3 PVXS two-target-one-bundle shape as a
positive case), the loader, and the CLI command.

### P1.6 — `publish-baseline.yml` / `update-main-baseline.yml`

Implements ADR-047 §6/§10. `publish-baseline.yml`: release-triggered,
`actions/baseline` → atomic archive → release-asset upload.
`update-main-baseline.yml`: default-branch-push-triggered, targets the
`accepted-main` channel's storage backend (Actions cache by default per
ADR-047 §10). Both use `actions/baseline`'s existing publish contract
unchanged (it already documents itself as read-only/non-publishing —
`actions/baseline/action.yml:6-8` — so these workflows own the publish
step) **but `actions/baseline` itself is not unchanged — correction, flagged
by review:** today it only writes per-library `.abicheck.json` files plus
`manifest.json` (`actions/baseline/run.sh`, `actions/baseline/build_manifest.py`);
it has no code path that stages the member ELF binaries §6/§10's S14
correction requires for a bundle-scoped baseline archive's `binaries/`
directory. Without that change, P1.2's bundle-scoped `resolve-baseline` has
no producer for the binaries it must return — S14 bundle baselines fail at
resolution time (or worse, silently fall back to snapshots and lose old-side
bundle analysis, exactly the failure this correction exists to prevent).
**This item must therefore include a real `actions/baseline` code change**
(extend `run.sh`/`build_manifest.py` to also copy each bundle member's
source binary into `binaries/` and record its path/digest in
`baseline-set.json`) alongside the two new workflows — not treated as an
unrelated, already-solved dependency.

**Open design gap, not resolved by ADR-047, flagged by review:** `binaries/`
alone serves bundle-graph findings (soname skew, provider-set changes) but
not necessarily a header/source-depth per-library diff within the bundle —
`compare-release`'s per-library flow needs old-side headers/compile-context
for that, which `binaries/` doesn't carry and which `.abicheck.json`
snapshots don't help either (`build_bundle_snapshot()` ignores non-ELF
inputs regardless). Before this item is implemented, resolve whether the
archive also needs a per-member `headers/` directory, or whether
`compare-release` needs a new snapshot-consuming input path — do not ship
S14 depth-aware bundle checks assuming `binaries/` alone is sufficient.

**Required cache-key detail, flagged by review:** GitHub Actions cache
entries are immutable once written (no overwrite-in-place); the workflow
must write a new key on every refresh — e.g.
`abicheck-baseline-main-<profile.id>-<head_sha>` — and `resolve-baseline`
must use `restore-keys: abicheck-baseline-main-<profile.id>-` to find the
latest match. A single stable key across refreshes silently stops updating
after the first write (the cache action treats it as a hit, not an error) —
this must be a tested behavior (a fixture asserting two consecutive
`update-main-baseline.yml` runs produce two distinct baselines resolvable
by `resolve-baseline`), not an assumption.

**Dependencies:** P1.1, P1.2, P1.5 — P1.2 added per review: this item's own
cache-refresh test requires `resolve-baseline` to be available to verify
consecutive `update-main-baseline.yml` runs produce distinct, resolvable
baselines.

### P1.7 — Scenario-first documentation IA

Implements ADR-047 §8's scenario catalog and the task's requested
`docs/integration/` tree. **File tree and migration map:**

```
docs/integration/
  index.md                                  # NEW — the "answer these questions" landing page
  concepts.md                               # NEW — glossary (ADR-047 §1's table, prose form)
  scenarios/
    single-library.md                       # NEW — absorbs github-action.md quick-start (S1)
    existing-build-artifact.md              # NEW — S3, the preferred large-repo flow
    header-aware-check.md                   # NEW — absorbs relevant scan-levels.md section (S6)
    source-replay.md                        # NEW — absorbs github-action-source-scans.md (S7)
    build-integrated-facts.md               # NEW — absorbs producing-source-facts.md (S8, S9)
    single-build-audit.md                   # NEW — absorbs choose-your-workflow.md's audit path (S5)
    multi-dso-project.md                    # NEW — the P0.4-promoted canonical page (S15)
    release-bundle.md                       # NEW — absorbs multi-binary.md's bundle framing (S14)
    packages-and-sdks.md                    # NEW — absorbs github-action-recipes.md's package section (S13)
    multi-platform.md                       # NEW — absorbs recipes.md's matrix section (S17)
    cross-compilation.md                    # NEW — absorbs recipes.md's cross-compile section (S18)
    application-and-plugin-contracts.md     # NEW — S22, S23
    dependency-and-container-checks.md      # NEW — absorbs deps-tree/deps-compare docs (S24)
    monorepo.md                             # NEW (S25)
    migration-and-rollout.md                # NEW — absorbs ci-gating.md's rollout guidance (S26, S27)
  baselines/
    lifecycle.md                            # NEW — ADR-047 §6, prose form
    release-contract.md                     # NEW (S19)
    accepted-main.md                        # NEW (S20)
    baseline-sets.md                        # NEW — schema reference
    storage.md                              # NEW — ADR-047 §10 table, prose form
  reference/
    actions.md                              # NEW — replaces scattered per-Action doc sections
    reusable-workflows.md                   # NEW
    project-config.md                       # supersedes reference/config-file.md's GH-specific parts
    build-output-schema.md                  # from P1.1
    report-schema.md                        # from P0.3
    failure-semantics.md                    # NEW — the resolve-baseline taxonomy + report envelope axes
```

**Migration map for existing pages:** `choose-your-workflow.md` stays as the
CLI-command-level decision tool (it already serves that job well per the
audit) and gains a link to `docs/integration/index.md` as the
GH-Actions-specific front door; `github-action.md` becomes the input/output
*reference* only (content moves to `reference/actions.md` +
`scenarios/single-library.md`); `github-action-recipes.md` is retired, its
content distributed into the relevant `scenarios/*.md` pages per the mapping
above (`tests/` or a redirect-check script should assert no orphaned
inbound links remain — reuse `check_ai_readiness.py`'s `mkdocs-nav-coverage`
check, which already flags unlinked pages); `github-action-source-scans.md`,
`baseline-management.md`, `producing-source-facts.md`,
`build-evidence-setup.md` are retired with content distributed similarly;
`scan-levels.md`, `multi-binary.md`, `ci-gating.md`, `real-world-example.md`,
`concepts/build-source-data.md`, `concepts/evidence-and-detectability.md`
are **kept as-is** (per `docs/CLAUDE.md`'s explicit note that the L0-L5
evidence trio and exit-code reference are deliberately single-sourced
elsewhere) — `docs/integration/` pages link to them rather than duplicating.

**This is the single largest item in the backlog** and should itself be
split into ~4-5 PRs (index+concepts; scenarios/ batch 1 — S1/S3/S6/S7;
scenarios/ batch 2 — S8/S9/S13/S14/S15; scenarios/ batch 3 — remainder;
baselines/ + reference/), each verified independently against
`mkdocs build --strict` and the AI-readiness `mkdocs-nav-coverage` /
`adr-index-nav-sync` (n/a here, doc-count-sync applies) checks.

---

## P2 — Deeper architecture (not started here)

- **Full TU→link-unit→DSO source-evidence attribution** (ADR-047 §9/D8) —
  needs linker-invocation capture, extending
  `abicheck/buildsource/build_query.py`'s existing partial zero-config
  compile-DB inference. Its own follow-up ADR when undertaken.
- **Monorepo changed-component planning** at scale (S25's `run-plan.json`
  filtering beyond a simple path-prefix diff).
- **Richer cross-platform baseline storage** (external object store backend,
  ADR-047 §10's fourth row) — no P0/P1 user story currently justifies it.
- **Provider plugins for build systems** beyond the CMake/Bazel/Make
  adapters `abicheck/buildsource/adapters/` already has.
- **Generalized external artifact stores** for baseline sets beyond GitHub
  Release/Actions cache/git.

---

## Pilot validation plan

### PVXS (confirmed pilot — extend, don't re-validate from scratch)

`validation/pvxs-abi-validation-2026-07.md` already validates the core
scanning correctness (3 real defects found and fixed) and proposes a
two-library `compare` workflow. **New validation needed once P1 lands:**
re-run the pilot using `check-project.yml` + `.abicheck.yml`'s `targets:`
block instead of the hand-written directory-fan-out `compare` workflow the
existing report recommends, and confirm:

- The existing Make-based build is reused unmodified (S3/S11 acceptance).
- `libpvxs`/`libpvxsIoc` are correctly modeled as two `targets:` under one
  `bundles:` entry, each keeping its own `public_headers:` scope
  (`--scope-public-headers` — finding F3 in the existing report).
- `resolve-baseline` produces per-target reports distinguishable in the PR
  UI (two `check_id`s, ADR-047 §8 S21 row).
- Fast-PR default does not force full source-depth scan (F1's O(N²)
  perf-bug fix should keep this affordable, but the *policy default*
  — changed-scope, not full-unseeded — is a separate acceptance check).
- The existing `abi-dumper`/ACC flow (already running per the pilot's own
  recommendation) can run in parallel as a `gate-mode: advisory` burn-in
  lane without modification.

### Second complex pilot — open gap (ADR-047 D9)

**Correction from an earlier draft, per review:** oneDAL PR #3693 is *not*
an unlocatable pilot — a repo-wide search for "Vandal" does return zero
matches (that part stands), but `docs/development/adr/044-reachability-aware-suppression.md`'s
Context section documents a real field review of oneDAL PR #3693 that found
a genuine tool-correctness defect and drove that ADR's entire redesign;
`docs/development/plans/g21-oneshot-deep-compare.md` and
`validation/REPORT.md` document the same evaluation's CLI-UX findings. That
review is real and valuable — but it is a **package/binary-level compare
evaluation** (conda-forge release artifacts, no source checkout, no build
reuse, no CI workflow), not a **GitHub-Actions CI-integration pilot** in
PVXS's sense (ADR-047 §"What the audit found," finding 5). **The remaining
backlog item is narrower than "find a second pilot from scratch":**

- Identify and get access to a second real C/C++ project — possibly oneDAL
  itself, revisited with a CI-integration lens this time, or a different
  project — with: a vendor compiler/toolchain (icpx/SYCL or MSVC), multiple
  DSOs with distinct public surfaces, an existing expensive build worth
  reusing, and (ideally) an existing libabigail or ABICC gate to migrate
  alongside.
- Produce a validation report in the same format as
  `validation/pvxs-abi-validation-2026-07.md` — defects found/fixed,
  documented-not-fixed issues, a recommended workflow — before claiming any
  S9/S15/S17/S21/S26 acceptance criteria are met for a vendor-toolchain
  project. Until that report exists, treat those scenario rows in ADR-047
  §8 as **design-validated against PVXS's simpler case only**, not proven
  for the vendor-toolchain/multi-baseline-channel class — oneDAL's existing
  field review does not substitute for it, however useful its own findings
  were.

### Minimal generic pilots (P1 exit criteria)

Each should record: initial integration LOC/YAML complexity, custom shell
line count, build duplication (did abicheck rebuild anything the project's
CI already builds), wall time, evidence depth achieved, report quality,
failure behavior on a deliberately broken case, and remaining manual steps
— the same "ease of enablement" measurements ADR-047/the task both call for,
not just correctness:

- Simple CMake single-library repository (S1/S6 acceptance).
- Make/custom-build repository — can reuse PVXS's own build if a second,
  simpler EPICS module or a synthetic Make fixture is used instead
  (S11 acceptance, distinct from the full PVXS pilot above).
- Bazel repository (S12 acceptance) — no existing pilot found for this;
  needs a fixture or a real small Bazel C++ project.
- Package-only RPM/Deb/tar comparison (S13 acceptance).
- Linux/macOS/Windows matrix (S17 acceptance) — the existing CI matrix
  (ADR-047-unrelated, `.github/workflows/ci.yml`) already exercises
  cross-platform *parsing*; this pilot is specifically about the
  *integration workflow* (`check-project.yml` multi-profile matrix), a
  distinct claim.
- Cross-compiled target (S18 acceptance).

---

## Out of scope for this plan

- Any change to detector logic, `ChangeKind` taxonomy, or snapshot schemas —
  this plan is integration-surface only.
- The P2 items listed above — recorded for visibility, not scheduled.
- Retrofitting the full source-evidence attribution model (D8) into P1's
  `build-output.json` — P1 ships the safe/declared-or-build-wide model only.
