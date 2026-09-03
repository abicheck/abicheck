---
doc_type: reference
audience:
  - library-maintainer
  - ci-owner
level: intermediate
canonical_for:
  - analysis-assurance
lifecycle: active
generated: false
---

# Exit Codes

`abicheck` uses different exit codes for each command family.

**Why they differ:** `compare` is the native interface — `0/2/4` by verdict (or `0/1/2/4` severity-aware), with invalid invocations exiting `64` so a usage error is never mistaken for an ABI verdict. `compat` mirrors `abi-compliance-checker` exit codes (0/1/2) so existing ABICC CI scripts work without changes. `scan` and `deps` have their own narrower contracts, documented below.

## Contract relevance decides what the gate sees (ADR-049)

Under `--contract`, each finding's contract relevance is classified
**before** compatibility policy runs, and policy then scores only the
`EVALUATED` findings — those whose relevance is `IN_CONTRACT` or
`NOT_APPLICABLE`. A `PROVEN_OUT_OF_CONTRACT`, `UNKNOWN_UNPROVEN` or
`UNKNOWN_UNRESOLVED` finding is `NOT_EVALUATED`: its `compatibility_decision`
is JSON `null` and its `gate_contribution` is `0`, so it moves neither the
verdict nor the exit code.

This is a real change to what the *compatibility verdict* scores, but only in
one direction: since policy now scores the `EVALUATED` subset of what it used
to score in full, the verdict can only stay the same or get less severe — a
change proven outside the declared contract stops blocking, and a change one
domain cannot resolve stops gating as an ABI break. What it never does is
make either disappear — an excluded finding keeps its `ChangeKind`, stays in
`changes` and in the audit ledgers, and is rendered with the relevance and
reason code that say why it did not gate. The *other* direction — the
overall process exit getting worse — comes from the separate,
independently-orthogonal axis below (missing evidence contributing its own
exit `1`), not from relevance itself; that's what stops missing evidence
from being the cheapest way to pass.

**Without `--contract` no finding carries a relevance**, so every
finding is scored exactly as before and every exit code below is unchanged.

## Contract-coverage contribution (ADR-049)

`compare` and `scan --against` carry an **orthogonal contract-coverage axis**
under `--contract`. Complete coverage of the mode-selected evidence
domain contributes `0`; missing, partial, stale, failed, contradictory, or
identity-incomplete **required domain evidence** is recorded as a
`CoverageFailure` in the run-level `contract_coverage_failures` ledger and
contributes `1`. Unrelated provider failures stay advisory. This is a
run-level ledger, not a per-finding field: a coverage gap does not by itself
force every finding to `UNKNOWN_UNRESOLVED` — an observed root or a
kind that's `NOT_APPLICABLE` regardless of evidence can still resolve
normally even while the ledger records incomplete evidence elsewhere.

The axis is folded with `max`, so it raises a clean `0` to `1` and **never
lowers** a gate's `2`/`4` — missing coverage cannot demote a real ABI break to
"warnings only". It never rewrites a finding's compatibility decision or its
gate contribution either; it is a floor on the exit status alone. Both
commands fold it identically.

A directory/package `compare` (the per-library release fan-out) applies the
same flag per library, then `max`s every library's own contribution into the
release's exit code — one library's incomplete coverage still raises a clean
release exit `0` to `1`, and the release JSON summary states the aggregate in
the same `contract_coverage_exit_contribution` field. `--fail-on-removed-
library`'s exit `8` is checked ahead of the coverage-only fallback, so a
removed library's own signal is never masked by an unrelated coverage gap
(and a real verdict-based `2`/`4` still wins outright over both, unchanged).

**Without `--contract` there is no selected domain, so the
contribution is always `0`** and every other exit code below is unchanged.

Ordinary change suppressions cannot clear a provider/domain coverage failure —
a coverage failure is not a finding, so the suppression machinery structurally
cannot reach one. To accept incomplete contract assurance, set
`contract.unresolved: warn` (for example via a `kind: contract` pack). That
zeroes this contribution and changes nothing else: the failures remain listed
in `contract_coverage_failures` — but only `--format json` carries that field
at all; markdown, review, HTML, SARIF, and JUnit output surface the same
information as a stderr diagnostic, a SARIF notification, or a JUnit error
suite instead.

Reports state the applied number in `contract_coverage_exit_contribution`,
which distinguishes contract coverage exit `1` from severity or aggregate
required-target coverage. The composite GitHub Action reads the same field and publishes
`verdict: COVERAGE_INCOMPLETE` rather than labelling the axis a severity-policy
or operational failure; on `compare`, where exit `1` is shared, it uses the
report's pre-fold `severity.exit_code` to tell the two apart. The configured `GateDecision` independently
contributes `0/1/2/4`: a compatible addition can block, and a breaking finding
can be demoted. Only legacy output without a gate block falls back from
compatibility verdict to `2`/`4`. Existing command-specific `5`, `8`, and `64`
behavior is as documented below.

## Analysis-assurance contribution (P0.4)

`compare`, and `scan --against`, always compute and report
`analysis_assurance` — a third, orthogonal axis alongside the compatibility
verdict and the policy/severity gate, answering "how complete and
trustworthy was the evidence behind this comparison" independently of
whatever the verdict says. Its `status` field is one of `complete`,
`partial`, `failed`, `not_comparable`, or `not_requested`, and it is always
present in `--format json` output (`analysis_assurance` — a top-level key on
`compare`'s report, nested under `diff` on `scan`'s), regardless of any flag.

By itself this changes **nothing** about any exit code — `analysis_assurance`
is purely informational until a caller opts in. Passing
`--require-complete-analysis` makes `compare`/`scan --against` additionally contribute exit `1`
whenever `analysis_assurance.status` is not `complete`, folded with the same
`max` discipline the contract-coverage axis above uses: it raises a clean `0`
to `1` and **never lowers** a `2`/`4`/`5`/`6` — incomplete assurance cannot
demote a real ABI break to "warnings only", and it never rewrites the
compatibility verdict, any finding, or the severity gate's own contribution.

`--require-complete-analysis` is single-pair only. A directory/package
(release) `compare` rejects it (P0.6, run-plan-aware aggregation, is the
tracked follow-up for extending this axis to the release fan-out); `scan
--against` rejects it without `--against`, alongside every other
baseline-only flag — there is no comparison for it to gate on otherwise.

**Without `--require-complete-analysis` every pre-existing invocation's exit
code is unchanged**, exactly as `--contract`'s own coverage axis
requires no opt-in flag change either.

The composite GitHub Action folds this the same way it folds the
contract-coverage axis: an assurance-gated exit `1` (via the dedicated
`require-complete-analysis` input, on either command) maps to a dedicated
`ANALYSIS_INCOMPLETE` verdict — never the compatibility verdict, and
unconditional (no `fail-on-*` input disables it).

## The `exit` report field (CLI cleanup phase two, PR G1 / PR E)

Every real-verdict `compare --format json` report (`full`/`leaf`/`root-cause`
modes) carries a top-level `exit` object (introduced at report schema 2.41;
schema 2.42 added its `crosscheck_promotion_contribution` field — see
`abicheck/schemas/__init__.py`'s `REPORT_SCHEMA_VERSION` docstring for that
and later additive fields, e.g. `annotations` at 2.43) stating the
already-resolved decision behind the axes above as one explainable
value, rather than requiring a reader to separately combine
`severity.exit_code`/`verdict`, `contract_coverage_exit_contribution`, and
`analysis_assurance_exit_contribution` themselves. `scan --against
--format json` carries the identical object too (scan schema 1.18), nested
at `diff.exit` rather than the report's top level — matching where its own
constituent contribution fields already live, since `scan` and `compare`
keep their own report shapes:

```json
"exit": {
  "code": 1,
  "reasons": ["analysis_assurance"],
  "compatibility_contribution": 0,
  "contract_coverage_contribution": 0,
  "analysis_assurance_contribution": 1,
  "crosscheck_promotion_contribution": 0
}
```

`code` is exactly `max()` over the four contributions — the identical
number the real process exits with for a single-pair `compare`. `reasons`
names every axis whose own contribution equals `code` (a lower,
non-winning contribution is excluded, since it did not determine the
result); `["clean"]` when `code` is `0`.

`crosscheck_promotion_contribution` (schema 2.42) is always `0` on a native
`compare` report — it has no meaning outside `scan --against`'s own
maintainer-promoted `--crosscheck KEY=error` finding
(`scan_engine._promote_published_gate`), which reconstructs the whole
`diff.exit` block through the same resolver whenever the crosscheck
contributes anything positive, so `reasons` can carry `promoted_crosscheck`
even when the crosscheck only *ties* — rather than exceeds — the baseline
comparison's own exit code.

Under `--used-by`/`--required-symbol(s)` scoping, `compatibility_contribution`
and `reasons` describe the **scoped** application/plugin-host gate (the one
the process actually exits on — see the section below), not the
informational full-library verdict/severity gate `full_verdict`/
`full_severity` still report; `reasons` carries `scoped_gate` rather than
`compatibility_gate` in that case.

This field is additive and does not itself change any exit code — it is a
persisted view of a resolution every axis above already performs. It does
not yet cover `not_comparable`, `scan --against`'s own budget-overflow
floor, or a release's removed-required-library policy, each raised through
a different code path today; see `abicheck/exit_decision.py`'s own module
docstring for that scope boundary.

## Commands removed in the ADR-043 CLI reset

`appcompat` and `plugin-check` are gone as standalone commands; their scoping
folded into `compare` itself — see
[Application- and plugin-scoped comparisons](#application-and-plugin-scoped-comparisons-compare-used-by-required-symbol)
below. `baseline` (the push/pull/list/delete registry), `debian-symbols`,
`collect`, `merge`, `inputs validate`, and `inputs compact` were removed
outright with no CLI replacement — validating a build-emitted
`abicheck_inputs/` pack now happens automatically whenever the pack is
consumed, and the `debian-symbols`/`collect`/`merge` library functions remain
available for programmatic (Python API) use only. None of these have their
own exit codes in the current CLI, so they no longer appear in the tables
below.

---

## `abicheck compare`

### Legacy exit codes (default, no `--severity-*` flags)

| Exit code | Meaning |
|-----------|---------|
| `0` | `NO_CHANGE`, `COMPATIBLE`, or `COMPATIBLE_WITH_RISK` — no binary ABI break |
| `2` | `API_BREAK` — source-level API break — recompilation required |
| `4` | `BREAKING` — binary ABI break |
| `16` | `not_comparable` (ADR-050 D2) — OLD and NEW were not extracted under a comparable profile/scope contract, so no verdict was produced (`verdict: null` in `--format json`, with a `reason` object). Pass `--diagnostic-comparison` to force a tentative diff instead. |
| `64` | Invalid invocation — bad arguments/options or an unreadable/unrecognised input, deliberately outside the `0/2/4` verdict space |

> **⚠️ Exit `0` covers `NO_CHANGE`, `COMPATIBLE`, and `COMPATIBLE_WITH_RISK`.** If your pipeline needs
> to distinguish them (e.g. warn on deployment risk), use `--format json` and
> read the `verdict` field — exit code alone is not sufficient.

### Severity-aware exit codes (with any `--severity-*` flag)

When any `--severity-preset` or `--severity-*` option is provided, the exit code
is computed from the severity configuration rather than the verdict:

| Exit code | Meaning |
|-----------|---------|
| `0` | No error-level findings |
| `1` | Error-level findings in `addition` or `quality_issues` only |
| `2` | Error-level findings in `potential_breaking` (but not `abi_breaking`) |
| `4` | Error-level findings in `abi_breaking` |
| `16` | `not_comparable` (ADR-050 D2) — the comparability gate hard-fails before severity classification ever runs, identical to the legacy scheme's `16`. |

The highest applicable code wins. For example, if both `abi_breaking=error` and
`quality_issues=error` have findings, the exit code is `4`.

> **ℹ️ The two exit code paths are mutually exclusive.** Without `--severity-*`
> flags, the legacy verdict-based path runs. With any `--severity-*` flag, the
> severity-aware path runs. They never both execute.

### Severity presets

| Preset | `abi_breaking` | `potential_breaking` | `quality_issues` | `addition` |
|--------|---------------|---------------------|------------------|-----------|
| `default` | error | warning | warning | info |
| `strict` | error | error | error | error |
| `info-only` | info | info | info | info |

Per-category overrides — `.abicheck.yml`'s `severity:` block
(`abi_breaking`/`potential_breaking`/`quality_issues`/`addition`) — take
precedence over the preset.

### CI gate patterns

```bash
# Production gate: fail on any break (legacy exit codes)
abicheck compare old.json new.json
ret=$?
[ $ret -eq 4 ] && echo "BREAKING — release blocked" && exit 1
[ $ret -eq 2 ] && echo "API_BREAK — source-level break" && exit 1
echo "OK (NO_CHANGE or COMPATIBLE)"

# Block unexpected API expansion (severity-aware; `severity.addition: error`
# in .abicheck.yml, which compare discovers from the working directory)
abicheck compare old.json new.json
ret=$?
[ $ret -eq 1 ] && echo "ADDITIONS — unexpected API expansion" && exit 1
[ $ret -eq 4 ] && echo "BREAKING — release blocked" && exit 1
[ $ret -eq 2 ] && echo "API_BREAK — source-level break" && exit 1
echo "OK"

# Strict mode: all categories at error level
abicheck compare old.json new.json --severity-preset strict

# Permissive gate: fail only on binary breaks
abicheck compare old.json new.json
ret=$?
[ $ret -eq 4 ] && exit 1   # BREAKING only; API_BREAK (exit 2) allowed
exit 0

# Parse exact verdict from JSON (with severity info)
abicheck compare old.json new.json --format json --severity-preset default -o result.json
verdict=$(python3 -c "import json,sys; d=json.load(open('result.json')); print(d['verdict'])" \
  || { echo "ERROR parsing result.json"; exit 1; })
[ "$verdict" = "BREAKING" ] && exit 1
```

---

## `abicheck compare` (multi-library / release inputs)

When `compare` is handed directory or package inputs (RPM/deb/tar/conda/wheel),
it fans out to per-library pairs and aggregates the worst per-library verdict
across the release — the behaviour formerly exposed as the standalone
`compare-release` command (folded into `compare` per ADR-037 D7; the GitHub
Action's own `compare-release`/`stack-check` mode aliases were removed the
same way, per ADR-043 — `mode: compare` handles directory/package operands
directly). By default a set/release comparison uses the verdict-based scheme
below, plus a dedicated code for removed libraries:

| Exit code | Meaning |
|-----------|---------|
| `0` | All libraries compatible (no API/ABI break) |
| `2` | Worst verdict is `API_BREAK` |
| `4` | Worst verdict is `BREAKING`, **or** an operational `ERROR` (a library failed to dump/extract/compare) |
| `8` | A library was removed between releases and `--fail-on-removed-library` is set. In the legacy scheme this is emitted only when no API/ABI verdict exit 2/4 **and no operational `ERROR` exit 4** already applies; in the severity-aware scheme it takes precedence over 0/1/2/4. |
| `16` | `not_comparable` (ADR-050 D2) — at least one library's OLD/NEW DSOs were not extracted under a comparable profile/scope contract. Takes precedence over **every** other outcome in the release, including `8` (removed-library) and a genuine `ERROR`: a not_comparable result means the comparison couldn't establish what changed at all, so it dominates in both the legacy and severity-aware schemes. Identical code to native `compare`'s own `16`. |

On the release path the severity-aware code (`0/1/2/4`) replaces the
verdict-based `2/4` mapping only when a severity *map* is actually in effect —
that is, any `--severity-*` flag is passed **or** `.abicheck.yml` carries a
`severity:` block (a preset or per-category levels). Setting `exit_code_scheme:
severity` on its own is **not** enough for directory/package inputs: with no
severity values to apply, the fan-out has nothing to score against and falls
back to the legacy verdict mapping. Under the legacy mapping, an operational `ERROR` exit 4 or nonzero API/ABI
verdict exit (`2`/`4`) wins before the removed-library check; under an effective
severity map, removed-library exit `8` wins over the aggregated `0/1/2/4` code.
An operational `ERROR` without a higher-priority removed-library result still
floors the severity-aware exit at `4`. (`--exit-code-scheme` is rejected on
directory/package inputs; pin the legacy scheme in config with
`exit_code_scheme: legacy` if you want to force it.) One consequence worth
gating on: with an effective severity map, a release whose worst verdict is
`BREAKING` can still exit `0` if that map downgrades ABI breaks (e.g.
`abi_breaking: warning`) — parse the `verdict` from JSON output if you need
scheme-independent CI behaviour.

---

## `abicheck scan`

The one-shot source-intelligence scan has its own contract (it may compare
`ARTIFACT` against `--against` and adds a budget guard). `--against` is the
only thing that selects the mode: omit it and `scan` runs a one-build
audit/hygiene/source-consistency scan only; pass it and `scan` also compares
`ARTIFACT` against it — there is no separate `--audit` flag:

| Exit code | Meaning |
|-----------|---------|
| `0` | Compatible (or advisory-only findings) |
| `2` | Source-level / API break (incl. `API_BREAK` cross-source findings) |
| `4` | ABI break (from the `--against` comparison) |
| `5` | `--budget` overflow — the time guard tripped (scope is never silently shrunk) |
| `6` | `NOT_COMPARABLE` (ADR-050 D2) — `ARTIFACT` and `--against` were not extracted under a comparable profile/scope contract, so the comparison never ran (`diff.reason` in `--format json`). Distinct from `compat check`'s `9` and native `compare`'s `16` — every command maintains an independent exit-code scheme. |
| `7` | Evidence-contract error (ADR-037 D5) — a pinned `--depth`/`--source-method` whose required source evidence was never collected, or `--abi3` targeting a binary that isn't a recognisable CPython extension module. No comparison ever ran (`verdict: "EVIDENCE_CONTRACT_ERROR"` in `--format json`); this process's own dedicated exit code (`cli_scan.py`'s `_EXIT_EVIDENCE_CONTRACT_ERROR`), unambiguous regardless of format or whether a JSON report was written. |
| `64` | Invalid invocation (bad arguments/options) |

> Exit `5` is unique to `scan`: `--budget 15m` **fails** the run rather than
> quietly dropping evidence. Use `--dry-run` to preview the audit checks and
> (if `--against` is given) the comparison that would run, plus the projected
> per-layer cost, without scanning — like every command's `--dry-run` it only
> ever exits `0`/`1`/`64`, never a verdict code; see
> [`--dry-run`](#-dry-run-dump-compare-scan-deps-tree-deps-compare) below.

### `scan --against` and severity (mirrors `compare`)

`scan --against` accepts the same severity surface as `compare` —
`--severity-preset`, the hidden per-category `--severity-*` overrides, and
`--exit-code-scheme` (plus `.abicheck.yml`'s `severity:`/`exit_code_scheme`
keys) — and, like `compare`, uses them to compute the `0`/`2`/`4` portion of
the exit code above from `severity.compute_exit_code` instead of the raw
verdict when the resolved scheme is `severity`. A `BREAKING` verdict under
`--severity-preset info-only` can therefore exit `0`, exactly as it can with
`compare`.

Under the `severity` scheme the JSON report's `diff` block also carries a
`severity` gate object — the same `config`/`categories`/`exit_code`/
`blocking`/`blocking_categories` shape `compare`'s own report uses (one
shared builder, so the two are comparable field by field), added in
`scan_schema_version` 1.9. It is what makes a non-zero exit on an otherwise
*compatible* diff self-explanatory: `severity.addition: error` on an
additions-only diff exits `1`, and `blocking_categories: ["addition"]` names
the cause, distinguishing it from the orthogonal contract-coverage `1`
above. The default **text** output states the same fact in its
`Baseline comparison` block:

```
Baseline comparison
  breaking=0 api_break=0 risk=0 compatible=1
  severity gate: exit 1 — blocking: addition

Verdict: COMPATIBLE
```

Both are absent under the default legacy scheme, which runs no severity
gate.

The block is the scan's **compatibility gate**, not the baseline diff's
alone: a cross-check the maintainer promoted with `--crosscheck KEY=error`
raises it too, adding a `promoted_crosscheck` entry to `blocking_categories`
(deliberately outside the four severity categories, since no severity level
produced it). The promotion is a floor — it can add a blocking reason but
never clear one a severity category already raised.

`aggregate` reads that `diff.severity` block as the target's compatibility
gate when it is present, exactly as it reads a `compare` report's own
`severity` block (and with the same fail-closed validation). This is what
keeps the orthogonal axes separable for a scan target: a legacy-scheme scan
has no native exit `1`, so a raw `1` can only be the contract-coverage
and/or analysis-assurance contribution (both orthogonal, both readable from
their own report fields regardless of scheme) — but a severity-scheme scan
*also* has a native `1` (an error-level addition), and folding all of these
to `1` would otherwise be indistinguishable. See
[`abicheck aggregate`](#abicheck-aggregate).

`scan --dry-run` previews whichever scheme the invocation resolves —
the scheme label, the per-category severity levels, and that scheme's exit
codes — so the preview matches the run it is predicting.

A gate pack (`--pack`) folds a `gate.*` assignment into a scan's severity
the same way it does for `compare`, and cannot override a value that was
actually stated — by an explicit `--severity-*`/`--exit-code-scheme` flag,
or by `.abicheck.yml`. Every flag in this family is a comparison-only flag (rejected as a
usage error without `--against`, exit `64`) — see the table above. The
budget (`5`), `NOT_COMPARABLE` (`6`), and evidence-contract-error exit codes
are unaffected: they are returned before the baseline comparison — and
therefore before any severity computation — ever runs.

---

## `abicheck aggregate`

The multi-target fan-in gate folds the per-target `compare`/`scan` JSON reports
a CI build matrix produces (one `abi-report-<target>.json` per leg) into one
gate decision. Four axes stay **orthogonal** (ADR-042, extended by ADR-049
Phase 7), and the exit code is the worst contribution across them:

- **gate** — each report already carries its own severity gate decision
  (`severity.{exit_code,blocking,blocking_categories}`); `aggregate` *combines*
  those, it never recomputes a gate from the compatibility verdict. So a
  `COMPATIBLE` report with an `addition=error` policy still contributes exit
  `1`, and a `BREAKING` report under a demoted preset can contribute `0`. A
  `scan` report is read via its own nested `diff.severity` gate block when it
  has one (a severity-scheme `scan --against`, schema 1.9+ — read through the
  identical validator a `compare` block goes through), and otherwise via its
  top-level `exit_code` (keyed on `scan_schema_version`).
  Reports produced without any gate block fall back to
  the legacy verdict→exit mapping (`0`/`2`/`4`). Reading is **fail-closed**: a
  report whose gate block is *present but corrupt* (an out-of-range or
  non-integer `exit_code`, a `blocking` flag that contradicts it, non-string
  categories) makes that target *unavailable* — never silently reverting to the
  greener legacy path.
- **coverage** — did every *required* expected target actually report? An
  incomplete required coverage is a *coverage* failure at exit `1`; it is
  **never** promoted to an ABI-break exit `4`. This is a different question
  from contract coverage below: this one asks whether the matrix *ran and
  reported at all*.
- **compatibility** — the worst verdict over the analyzed targets, reported for
  context; it does not by itself drive the exit code.
- **contract_coverage** — reads back each already-analyzed target's own
  `contract_coverage_exit_contribution` (per-report field; see
  "Contract-coverage contribution" above) and folds it with `max`, same as
  the other axes — added to the aggregate schema alongside this axis
  (`abicheck.aggregate.AGGREGATE_SCHEMA_VERSION` is the versioned fact
  owner); `aggregate` never recomputes it. This is a different question again from
  plain `coverage`: a required target can have reported successfully (no
  coverage gap) while its own evidence for the *selected contract domain*
  was still incomplete (a contract-coverage gap). Both can independently
  produce exit `1`, for unrelated reasons, and `aggregate` records which one
  fired rather than merging them into one undifferentiated `1`.

| Exit code | Meaning |
|-----------|---------|
| `0` | Every required target analyzed, no blocking findings |
| `1` | A required target was unavailable while the effective `missing_required` policy was `fail` (the default; `warn` downgrades this to advisory and contributes nothing here); an analyzed target's gate blocks on an `addition`/`quality` finding only; a target's own contract-coverage evidence was incomplete under `--contract`; **or** a non-verdict per-report failure folds here (e.g. a `scan` report's budget-overflow exit `5`) — these axes are independent and any one of them alone is enough to produce `1` |
| `2` | An analyzed target's gate is a source-level / API break |
| `4` | An analyzed target's gate is an ABI break |
| `64` | Invalid invocation (bad arguments/options, malformed manifest, duplicate target id, or no expected-target set given) |

The highest applicable code wins: a run with both an ABI break and a coverage
gap exits `4`; a run whose *only* problem is a missing required target exits
`1`, never `4`.

> **A required target with no report is _unavailable_ (unknown), never counted
> as compatible.** This is the whole point of the command: a matrix leg that
> failed before uploading its report is reported as a coverage gap and fails
> the gate at exit `1` — it is never silently folded into the verdict as an
> empty, compatible ABI, and a build that simply never ran is never handed an
> ABI-break exit `4`.

**Declaring the expected-target set (required — one of):**

- `--manifest abi-targets.json` (recommended) — the single source of truth for
  which targets the matrix must produce: `{"targets": [{"id": "linux-x86_64",
  "required": true}, ...]}`. Generate it once in the plan job and feed the same
  file to both the matrix and this gate so they never drift.
- `--run-plan run-plan.json` — a project run-plan from `abicheck project
  plan`, projected to the same expected-target shape internally (recommended
  for a `project plan`-driven workflow instead of a separate manifest
  projection step); each check's own `check_id` becomes the expected target
  id, matching what `check-target` writes as every report's `target_id`.
- `--discovered-only` — explicitly aggregate whatever reports are present with
  **no required-target coverage gate** (a missing target is simply not
  counted, never a coverage failure — the `contract_coverage` axis is
  unaffected and still applies to whatever *is* present). Required to run
  without `--manifest`/`--run-plan`: with no declared target set the gate cannot
  tell a missing required target from an intentionally absent one, so a bare
  `aggregate reports/` is a usage error (exit `64`), not a silent pass.

The gate policy for these two situations is no longer a pair of CLI flags
(CLI cleanup phase two, PR 2) — it's the manifest's (or run-plan-projected
manifest's) own versioned `gate` block:

```json
{"aggregate_manifest_version": "2.0",
 "targets": [{"id": "linux-x86_64", "required": true}],
 "gate": {"missing_required": "warn", "unexpected_target": "fail"}}
```

`missing_required: warn` downgrades a coverage gap to advisory — it stops
contributing to exit `1` on its own, but the per-target gate decisions,
contract-coverage evidence, and per-report failures above remain independent
axes that can each still produce `1` for an unrelated reason.
`unexpected_target` (`include`/`warn`/`fail`/`ignore`, default `include`)
controls a report whose target is not in the expected set: `include` counts
its real findings in the gate but not in required coverage. Omitting `gate`
entirely keeps the same defaults this command always had (`missing_required:
fail`, `unexpected_target: include`). The resolved policy is reported back in
the JSON output's `effective_policy` block, including which source
(`manifest`/`run-plan`/`explicit`/`default`) it came from — `explicit` only
appears for a direct Python-API caller of `aggregate()` forcing a value
(there is no CLI spelling for it). The `--format json` output is versioned
(`aggregate_schema_version` — see `abicheck.aggregate.AGGREGATE_SCHEMA_VERSION`
for the current value) and carries the four axes
separately under `gate` / `coverage` / `compatibility` / `contract_coverage`
— the last is `{"exit_contribution": 0, "incomplete_targets": []}`-shaped and
present even when no target used `--contract` (an empty
`incomplete_targets` list, not an omitted block).

When targets are checked under several toolchain profiles (report ids of the
form `target@profile#channel@depth`), two additional reporting-only blocks
group them back together: `profile_matrix` (one entry per logical target,
with `affected_profiles`/`verdict_by_profile`) and `finding_matrix` (one
entry per distinct *finding*, with `affected_profiles` /
`unaffected_profiles` / `undetermined_profiles` and a `scope` of
`all_profiles` / `profile_specific` / `partial` / `undetermined`). A profile
whose report is missing, unreadable, not-comparable, or carries no `changes`
array is `undetermined` — never reported as clean of a finding it was never
checked for. Neither block affects the exit code; the full field list is in
[`aggregate_report.schema.json`](schemas/v1/aggregate_report.schema.json).

---

## Application- and plugin-scoped comparisons (`compare --used-by`/`--required-symbol`)

The standalone `appcompat` and `plugin-check` commands are gone (ADR-043).
Their scoping now folds into `compare` itself:

- **`compare --used-by APP`** (repeatable) — folds `appcompat`. `APP` is a
  real application binary; its actual imports/required symbol versions scope
  the comparison. `OLD`/`NEW` may be real library binaries or JSON snapshots
  that carry binary evidence (a `dump` of a real library, not headers-only).
  Mutually exclusive with `--required-symbol`/`--required-symbols`.
- **`compare --required-symbol SYM`** (repeatable) / **`--required-symbols
  FILE`** — folds `plugin-check`. Scopes the comparison to an explicit
  dlopen/dlsym entrypoint contract instead of the full diff. Mutually
  exclusive with `--used-by`.

The full library comparison still runs once; **the worst app/plugin-scoped
result becomes the primary verdict/exit code**, with the full verdict and
unrelated changes kept as informational context. There is no separate
exit-code scheme for this scoping — it uses exactly the `compare` codes
documented above (legacy `0/2/4`, severity-aware `0/1/2/4`, `64` for a usage
error). In particular, exit `4`/`BREAKING` is also the result when the
application requires symbols or ELF version tags absent from the new
library — even if the unscoped library diff is otherwise compatible —
because the application would fail to load.

---

## `abicheck deps tree`

| Exit code | Meaning |
|-----------|---------|
| `0` | All dependencies resolved, all required symbols bound |
| `1` | Missing dependencies or unresolved symbols (binary would fail to load) |
| `64` | Invalid invocation (bad arguments/options) |

`--dry-run` shows the resolved binary path and search order without
resolving the dependency tree — see
[`--dry-run`](#-dry-run-dump-compare-scan-deps-tree-deps-compare) below.

---

## `abicheck deps compare`

Sysroot flags are **`--old-root`/`--new-root`** (default `/` for each —
renamed from the old `--baseline`/`--candidate`).

| Exit code | Verdict | Meaning |
|-----------|---------|---------|
| `0` | `PASS` | Binary loads and no harmful ABI changes |
| `1` | `WARN` | Binary loads but ABI risk detected in dependencies |
| `4` | `FAIL` | Load failure or binary ABI break in dependencies |
| `5` | — | `not_comparable` (ADR-050 D2) — at least one dependency's before/after DSOs were not extracted under a comparable profile/scope contract, so its per-library ABI diff never ran. Dominates `0`/`1`/`4`, the same "couldn't establish what changed" precedence the gate uses elsewhere. |
| `64` | — | Invalid invocation (bad arguments/options) |

`--dry-run` shows the old/new roots, resolved binary paths, and search order
without running per-library ABI diffs — see
[`--dry-run`](#-dry-run-dump-compare-scan-deps-tree-deps-compare) below.

### CI gate patterns

```bash
# Full-stack check: fail on FAIL, warn on WARN
abicheck deps compare usr/bin/myapp --old-root /old-root --new-root /new-root
ret=$?
[ $ret -eq 4 ] && echo "FAIL — load failure or ABI break" && exit 1
[ $ret -eq 1 ] && echo "WARN — ABI risk detected" && exit 1
[ $ret -ne 0 ] && echo "ERROR — unexpected non-verdict exit code: $ret" && exit 1
echo "PASS"

# Permissive: only fail on load failure / ABI break
abicheck deps compare usr/bin/myapp --old-root /old-root --new-root /new-root
ret=$?
[ $ret -eq 4 ] && exit 1   # FAIL only; WARN (exit 1) treated as OK
[ $ret -ne 0 ] && [ $ret -ne 1 ] && exit 1   # fail closed on non-verdict errors
exit 0
```

---

## `abicheck compat`

Matches `abi-compliance-checker` exit codes (ABICC drop-in):

| Exit code | Meaning |
|-----------|---------|
| `0` | No breaking changes (`NO_CHANGE` or `COMPATIBLE`) |
| `1` | `BREAKING` (mirrors ABICC) |
| `2` | `API_BREAK` (source-level break; non-verdict failures use extended codes below) |

> Non-verdict/tool failures are classified via **Extended compat error codes (ABICC-style)** below (`3`, `4`, `5`, `6`, `7`, `8`, `9`, `10`, `11`).

---


### Extended compat error codes (ABICC-style)

In `abicheck compat`, non-verdict failures are further classified where possible:

| Exit code | Typical cause |
|-----------|---------------|
| `3` | Required external command/tool is missing (for example `castxml`) |
| `4` | Cannot access input files (missing or permission denied) |
| `5` | Header compile/parsing failure during dump |
| `6` | Invalid compat configuration/input (descriptor, suppression, regex flags) |
| `7` | Failed to write report/output artifact |
| `8` | Dump/analysis pipeline failure |
| `9` | `not_comparable` (ADR-050 D2) — OLD and NEW were not extracted under a comparable profile/scope contract, so no verdict was produced. Distinct from native `compare`'s own `16` — the two commands maintain independent exit-code schemes. |
| `10` | Generic internal/tool failure fallback |
| `11` | Interrupted run |

> Note: classification is best-effort and context-dependent; `API_BREAK` remains `2`.

---

## `--dry-run` (`dump`, `compare`, `scan`, `deps tree`, `deps compare`)

Every one of these five commands accepts `--dry-run`: it resolves and
validates the invocation — classifies inputs, discovers config, and (per
command) shows which data layers (L0–L5) are available, the audit checks and
comparison that would run, or the resolved binary path/search order — and
prints a report **without** doing the real work. It is cheap and read-only:
no compiler invocation, no build-system query, no network access, and it
writes nothing — passing `-o`/`--output` together with `--dry-run` is a
usage error.

| Exit code | Meaning |
|-----------|---------|
| `0` | Resolved cleanly — ok to proceed |
| `1` | Blocked — the invocation would fail once actually run |
| `64` | Usage error (e.g. `-o`/`--output` passed together with `--dry-run`) |

**`--dry-run` never returns a verdict code.** It exits `0`/`1`/`64` only —
never `2`, `4`, `5`, or `8`, even on a command whose real run could produce
one of those.

---

## Summary table

| Verdict / State | `compare` exit (legacy) | `compare` exit (severity) | `scan` exit | `deps tree` exit | `deps compare` exit | `compat` exit |
|-----------------|------------------------|--------------------------|-------------|-------------------|----------------------|---------------|
| `NO_CHANGE` / `PASS` / compatible | `0` | `0` | `0` | `0` | `0` | `0` |
| `COMPATIBLE` | `0` | `0` | `0`‡ | — | — | `0` |
| `COMPATIBLE_WITH_RISK` | `0` | `0`–`2`* | `0` / `0`–`2`*‡ | — | — | `0` |
| Additions only | `0` | `0`–`1`* | `0` / `0`–`1`*‡ | — | — | n/a |
| Quality issues only | `0` | `0`–`1`* | `0` / `0`–`1`*‡ | — | — | n/a |
| `WARN` (ABI risk) | — | — | — | — | `1` | — |
| `API_BREAK` | `2` | `0`–`2`* | `2` / `0`–`2`*‡ | — | — | `2` |
| `BREAKING` / `FAIL` | `4` | `0`–`4`* | `4` / `0`–`4`*‡ | — | `4` | `1` |
| `--budget` overflow | — | — | `5` | — | — | — |
| Missing dependencies/symbols | — | — | — | `1` | — | — |
| Load failure | — | — | — | — | `4` | — |
| Invalid invocation / tool error | `64`† | `64`† | `64`† | `64`† | `64`† | `3/4/5/6/7/8/10/11` |

In the `scan` column, the value left of the `/` is the legacy (verdict-based)
mapping — the default — and the value right of it applies once `scan
--against` resolves the `severity` scheme (any `--severity-preset`/
`--severity-*`/`--exit-code-scheme severity`, or a config `severity:` block),
where it follows the same `compare` exit (severity) column; see
["`scan --against` and severity"](#scan-against-and-severity-mirrors-compare)
above.

App/plugin-scoped comparisons (`compare --used-by`/`--required-symbol`) reuse
the `compare` columns above — see
[Application- and plugin-scoped comparisons](#application-and-plugin-scoped-comparisons-compare-used-by-required-symbol).
`aggregate` combines each report's own severity gate (`0`/`1`/`2`/`4`) over its
analyzed targets and adds a coverage gate (a required gap exits `1`, never `4`) —
see [`abicheck aggregate`](#abicheck-aggregate).
`--dry-run` (on `dump`/`compare`/`scan`/`deps tree`/`deps compare`) reuses
none of these rows — it always exits `0`/`1`/`64`; see
[`--dry-run`](#-dry-run-dump-compare-scan-deps-tree-deps-compare) above.

\* Severity exit codes depend on the configuration, and the range covers the
whole configuration space — **including demotion of a real break**. With
`severity.addition: error`, additions exit `1`; with `--severity-preset
info-only` every category is `info`, so *everything* exits `0`, a `BREAKING`
comparison included. The default preset leaves `potential_breaking` at
`warning`, so an `API_BREAK` exits `0` unless `--severity-preset strict` (or
`severity.potential_breaking: error`) raises it to `2`. Read the report's
own `severity` gate block — `exit_code`/`blocking`/`blocking_categories` —
rather than inferring the cause from the code.

† Every command exits `64` for an invalid invocation — bad arguments/options
or an unreadable/unrecognised input — deliberately outside the verdict/result
space so a usage error is never mistaken for a compatibility result. To
reliably distinguish verdicts from errors in a script, use `--format json` and
read the `verdict` field where available.

‡ Two schemes, shown as `legacy / severity`. `scan`'s **legacy** scheme (the
default) collapses every compatible/advisory-only state (no break,
deployment risk, additions, quality signals) to exit `0` — read `--format
json` if your pipeline needs to distinguish them. Under a resolved
`severity` scheme (`scan --against` with any `--severity-*`/
`--exit-code-scheme severity`, or a config `severity:` block) `scan` follows
the `compare` exit (severity) column on the same `*` terms, in **both**
directions: `severity.addition: error` exits `1` on an additions-only diff,
and `--severity-preset info-only` exits `0` on a `BREAKING` one. See
["`scan --against` and severity"](#scan-against-and-severity-mirrors-compare).

---

## Strict mode (`-s` / `-strict`)

`compat` (and only `compat`) supports strict mode to promote lesser verdicts:

```bash
# Strict mode: COMPATIBLE + API_BREAK → exit 1 (BREAKING)
abicheck compat -lib foo -old OLD.xml -new NEW.xml -s

# Strict API-only: only API_BREAK → exit 1; COMPATIBLE stays exit 0
abicheck compat -lib foo -old OLD.xml -new NEW.xml -s --strict-mode api
```

`--strict-mode` values:
- `full` (default when `-s` is set): `COMPATIBLE` + `API_BREAK` → BREAKING
- `api`: only `API_BREAK` → BREAKING; `COMPATIBLE` unchanged

`--strict-mode` has no effect unless `-s` is also passed.

> Note: `abicheck compare` does not have `-s` / `--strict` flags.
> For compare-mode strict pipelines, use CI exit code logic (check exit `2` as a failure).
