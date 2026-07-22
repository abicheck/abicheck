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

### P1.3 — `actions/check-target`

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
