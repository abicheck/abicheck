# AGENTS.md — `action/`

The shell-script implementation behind the root `action.yml` composite
GitHub Action. See `/AGENTS.md` for the canonical project-wide contract —
this file covers what's specific to this tree.

## How the pieces fit together

`action.yml` declares the Action's `inputs`/`outputs` and a `runs.steps`
sequence that invokes these scripts in order:

1. `action/validate-inputs.sh` — mode-aware validation of
   `mode`/`new-library`/`old-library`/`format`/`upload-sarif`, run **before**
   Python setup or any dependency install. Exists to fail fast: an
   unsupported input combination (e.g. `mode: scan` with a release-style
   directory, or `format: sarif` on `scan`/`dump`) used to silently fall back
   or surface only after a multi-minute toolchain install. It re-implements a
   local copy of `_is_release_style_operand()` deliberately — not sourced
   from `run.sh` — so this step has zero dependency on `run.sh`'s internal
   layout. `tests/test_action_validate_inputs.py` runs both copies against
   the same fixtures to catch drift between them.
2. System dependencies, dispatched by the resolved `dependency-source`
   (`conda-forge` — the **default**, backward-compat-resolved from the
   deprecated `install-deps` boolean when `dependency-source` is unset (its
   own default `true` also lands on `conda-forge`; `false` maps to `none`) —
   or `conda-forge-gcc14`/`conda-forge-clang20`, `system`, or `none` to skip):
   - `action/install-deps.sh` (`dependency-source: system`) — the *previous*
     default, still available. Installs gcc/g++/clang/bear and invokes the
     checksum-pinned `action/install-castxml.sh` Superbuild installer on
     Linux, or installs castxml via Homebrew on macOS. Windows install is
     not automated (warns only).
   - `action/install-deps-conda-forge.sh` (`dependency-source` starting with
     `conda-forge`, Linux/macOS only — `conda-forge-gcc14` is Linux-only,
     conda-forge's `gcc`/`gxx` packages don't build for macOS) — installs
     one of root `pyproject.toml`'s pixi environments (`scanner` for plain
     `conda-forge`, `gcc14`/`clang20` for the pinned-major variants — see
     `[tool.pixi.feature.native-toolchain*]`, `pixi.lock`-frozen: castxml
     0.7.x + a matching gcc/g++ or clang/clang++) via the
     `prefix-dev/setup-pixi` step in `action.yml` (its `environments:` input
     set from `action.yml`'s own `dependency-source` → pixi-environment-name
     mapping), then symlinks *only* that environment's compiler/scanner
     tools into a dedicated shim directory and prepends that to `PATH` —
     deliberately not the whole pixi environment `bin/`, which also carries
     its own `python`/`pip` (a transitive dependency of the workspace-level
     `abicheck = {path=".", editable=true}` pypi-dependency) that would
     otherwise shadow whatever `actions/setup-python` configured for the
     rest of the calling workflow's job. No clang/bear on the plain
     `conda-forge`/`conda-forge-gcc14` paths (clang comes with
     `conda-forge-clang20` itself, still no bear anywhere) — L4/L5 source
     scanning degrades gracefully, same as when they're absent on the
     system path.
3. `action/run.sh` — assembles the `abicheck` CLI invocation from `INPUT_*`
   environment variables (one per `action.yml` input), runs it, and sets the
   Action's declared outputs from the exit code / report contents.
4. `action/report_query.py` — the one JSON-report reader `run.sh`'s
   `_report_query` shells out to, for every derived value it publishes. A real
   file rather than a heredoc so its query semantics are reachable from
   `pytest`/`mypy`/`ruff`; see the module's own docstring for the exit-code
   contract (`0` answered / `1` cannot tell / `2` unknown query) and for why it
   lives here rather than under `abicheck/` (it must version with the Action,
   not with whatever `abicheck-version:` a workflow pinned). **Add a report
   field's reader here, never as a second parser in `run.sh`.**

**Keep `validate-inputs.sh` and `run.sh` in sync.** `run.sh` independently
re-checks the format/upload-sarif rules right before invoking `abicheck`
(defense in depth for anyone invoking `run.sh` directly, e.g. in tests) — a
rule added to one and not the other reopens the exact silent-fallback bug
`validate-inputs.sh` exists to prevent.

## Testing

`.github/workflows/test-action.yml` exercises the composite Action
end-to-end (uses `./` as the action reference) against fixtures in
`tests/fixtures/action/` — compare/scan/appcompat modes, SARIF/JSON output,
severity handling, multi-platform. It is a **required** check when
`action/**`/`action.yml` changes (path-filtered, see `.github/AGENTS.md`).

Unit-level coverage of the shell logic lives in root `tests/` (not a
separate `action/tests/` — keep it there):
`test_action_run_sh_helpers.py`, `test_action_run_sh_dry_run_baseline.py`,
`test_action_run_sh_pr_json.py`, `test_action_run_sh_severity_summary.py`,
`test_action_run_sh_summary.py`, `test_action_run_sh_legacy_aliases.py`,
`test_action_run_contract.py`, `test_action_validate_inputs.py`,
`test_action_baseline.py`, `test_action_collect_facts.py`,
`test_action_report_query.py` (the reader above, including the
workflow-command-injection defenses, exercised by attempting the attacks),
`test_action_unreadable_report_verdict.py` (the `REPORT_UNREADABLE` path,
end-to-end through the whole of `run.sh`). These are plain
Python tests that invoke the shell scripts as subprocesses and assert on
their output/exit codes — run them with the normal fast test command
(`pytest tests/ -k action`), no `bash`-specific test runner needed.

## Shell-script conventions

- `set -euo pipefail` (or `set -uo pipefail` where a non-zero abicheck exit
  code is meaningful output, not a script bug — check which pattern a given
  script already uses before changing it).
- Treat every `INPUT_*` / `GITHUB_*` environment variable as untrusted (PR
  authors control several `INPUT_*` values on `pull_request` triggers) — never
  `eval` an input, and quote every expansion.
- `add_flag()` in `run.sh` supports both a YAML block-scalar (one path per
  line — handles spaces) and legacy whitespace-splitting for single-line
  values; if you add a new list-valued input, use the same helper rather than
  writing a fresh splitting loop.
- Prefer portable bash: contributors and CI runners include macOS's stock
  bash 3.2 (Git Bash on Windows too) — avoid bash 4+-only constructs
  (associative arrays, `readarray`, process substitution where a `<<<`
  here-string works instead).

## How `run.sh` resolves the verdict it publishes

Since ADR-063 Phase 6 (Track T8) there are exactly two sources, and no
third:

1. **The structured report.** `run_outcome` (ADR-063 D6,
   `abicheck/policy/outcome.py`) first — `compatibility` for the verdict,
   `gate` for the severity gate — then the legacy JSON fields
   (`verdict`, `severity.exit_code`,
   `contract_coverage_exit_contribution`, `analysis_assurance.status`) for
   a report from an older abicheck.
2. **The process exit code**, via the `case $ABICHECK_EXIT in ...`
   dispatch, plus `_is_cli_error()`'s stderr check for a Click usage error
   that produces no report at all. This is the *transport-level* fallback:
   it answers "what did the invocation itself say" when there is no result
   to read.

Nothing else is consulted. `run.sh` used to re-derive these facts by
regex-matching rendered output — a `sed` over a markdown/text report's
`Verdict:`/`**Verdict**` line and its `severity gate: exit N ... blocking:`
line, a SARIF `runs[0].properties.abiVerdict` lookup, and `grep`s over
captured stderr for the contract-coverage and analysis-assurance floor
notices — and all of that is gone. **Don't add a reader that parses a
renderer's prose**: a renderer's wording is presentation, it drifts, and it
carries values a PR author can influence. If the boundary needs a fact,
give it a field in the structured report.

The consequence to know when reading a failure report: a run whose report
is genuinely unreadable (a crash, or an `extra-args --write` that
suppressed the internal sidecar) gets no
`SEVERITY_ERROR`/`COVERAGE_INCOMPLETE`/`ANALYSIS_INCOMPLETE` label and no
escalation, since no structured evidence stated one. That is deliberate:
absence of data is not evidence an axis fired.

**But absence of data is not evidence an axis *passed*, either**, and that
half used to be missing. At exit 0 the same absence fell through to
`VERDICT="COMPATIBLE"` — `_resolve_clean_exit_verdict` set that first and only
ever *escalated* from a report it could read — so a run that wrote no usable
report published "No binary ABI break detected" having established nothing.
Two predicates close that without weakening anything above:

- `_report_validity` asks `report_query.py` to classify the document itself
  (`ok` / `absent` / `unreadable` / `unparseable` / `not_object` / `empty` /
  `no_result`), and `_json_report_expected` asks whether the *caller* requested
  a JSON report — because "no report arrived where one was asked for" and "no
  report was requested" are different, and only the first is a failure of this
  step. **Requested** means `format: json` (wherever it lands — `output-file`
  or stdout; asking for json *is* the request) or a caller-supplied
  `extra-args --write json=PATH`. It does not include the internal sidecar.
  `no_result` is the generalization of `empty`: a document can parse, be
  non-empty, and still carry no verdict anything can read (`{"error": "write
  interrupted"}`, a lone `report_schema_version`, `{"findings": null}`,
  `{"no_baseline": true}`). **Validity asks whether a verdict is readable, not
  whether a key is present.** A presence-only recognizer was tried and was
  wrong: admitting a document is exactly what masks a missing result, since
  admission is what licenses the COMPATIBLE fallthrough. `report_query.py`'s
  `_carries_a_result` names each verdict source instead — and note that three
  of the four real emitter shapes carry `verdict: null` by design
  (not-comparable pairs it with `reason`, audit-only with its
  `findings`/`suppressed_findings` arrays, and a release envelope may rely on
  `libraries`), so a naive "non-empty string verdict" rule fails working runs.
  Both directions are pinned in `TestAReadableVerdictSourceIsRequired`. The
  audit rule requires **both** `findings` and `suppressed_findings` as lists,
  not either: `no_baseline_audit` reads an absent `suppressed_findings` as
  "nothing was suppressed", so a half-present pair lets a run whose policy hid
  every finding publish `AUDIT_CLEAN` — absence cannot establish that policy hid
  nothing (ADR-067's "record before disposing", applied to the reader).

  **"Requested" means every caller-named destination, not the first.**
  `compare`'s `--write` is repeatable (`multiple=True`, ADR-068 D4), so
  `--write json=a.json --write markdown=b.md` really does write `a.json`, and
  each `json=` destination is independently required. A stale comment calling
  the option scalar and last-wins had the extractor *clear* an earlier `json=`
  path on seeing a later non-json one — settle contradictions like that against
  the option declaration in `frontends/cli/options/secondary_output.py`, not
  against either comment.

  **A structural rule requires `verdict: null`, not a present `verdict` key.**
  The not-comparable and audit rules exist because their emitters write a
  literal `null` and put the result elsewhere (a `reason` object; the two audit
  arrays). Keyed on presence, they re-admitted what the vocabulary check below
  rejects — `{"verdict": "write interrupted", "reason": {}}` passed on the
  `reason` object alone. And there is deliberately **no** `libraries` rule: no
  reader extracts a verdict from that array, and
  `_format_release_json` emits a rolled-up top-level `verdict` on every release
  document, so a library-only shape is not one any emitter writes.

  **Stdout mode is judged by the mode, not by an empty destination inventory.**
  `format: json` with no effective output path sends the requested report to
  stdout *even when an `extra-args --write json=` names another artifact*, so
  gating the stdout check on "the inventory is empty" let a valid secondary mask
  an unusable stdout report — the same masking this section exists to close, one
  branch over. The stdout document is also validated through its own
  `$_STDOUT_JSON_FILE` rather than `_report_validity`, whose chain falls through
  to a `--write` destination in precisely the case a missing stdout report
  presents.

  **A verdict must be one the emitters actually produce.** `KNOWN_VERDICTS`
  in `report_query.py` is checked by membership, not non-emptiness: an
  arbitrary string (`{"verdict": "write interrupted"}`) parses, reads as a
  result, and then matches none of the tiers `_resolve_clean_exit_verdict`
  acts on, so it kept the COMPATIBLE fallthrough. The table must track the
  producing code in *both* directions — a value the CLI adds and this set
  lacks would fail working runs — so
  `TestTheVerdictVocabularyTracksTheRealEmitters` derives it from
  `checker.Verdict` and `_RELEASE_VERDICT_ORDER` rather than restating
  literals.

  **Any caller-supplied string reaching an annotation goes through
  `_sanitize_annotation` first.** That includes *paths*, not just report text:
  every `--write json=` destination comes from `extra-args`, so a diagnostic
  naming one interpolates PR-controlled input into a `::error::` workflow
  command, and GitHub percent-decodes workflow-command data — `x%0A::add-mask::secret`
  becomes a second command. `_reject_unusable_report` sanitizes at the one
  place that emits, and the guard is asserted by *executing* the attack
  (`TestADestinationPathCannotForgeAWorkflowCommand`), never by asserting the
  text of the defending file — the exact mistake #705 → #758 shipped.

  **Validate destinations, never the fallback chain.** `_json_report_src` is a
  *fallback chain* — it answers "give me a report to read" by returning the
  first destination that arrived. `_caller_json_destinations` answers the
  different question this validation owes the caller: "where was a report
  asked for", every one of them. Routing the validation through the chain let a
  missing `format: json` primary be masked by a valid `extra-args --write
  json=secondary.json` (`--write` names an independent artifact whose path must
  differ from `-o`, so it can never satisfy the primary request). Each
  destination is asked `report_validity` on its own path, and stdout mode — the
  one shape naming no destination — is the only case that falls back to the
  chain.

  **The primary destination is `_effective_output_file`, not `$OUTPUT_FILE`.**
  `extra-args` can carry its own `-o`/`--output`, and since `CMD` puts this
  script's flags first and `extra-args` last, Click's last-wins rule makes the
  override the real destination — the exact sibling of the `--format` override
  `_effective_format` already resolved. Keying on the input alone failed both
  ways: an override with no `output-file` input named no destination, so a
  file-writing run was validated as the *stdout* shape and a working run
  published `REPORT_UNREADABLE`; with both given, the superseded path was
  validated while the report landed elsewhere. `_json_report_src` resolves the
  same effective path, and it has to: fixing only the inventory leaves a report
  that validates and is then never *read*, which falls through to COMPATIBLE —
  this PR's original defect. Mutation-tested in exactly that split
  (`TestAnExtraArgsOutputOverrideIsHonoured`).

  **A requested destination must also be *fresh*.** Parseability alone says a
  document is there, not that this invocation wrote it; a leftover from an
  earlier step or one a PR author committed satisfies the former and not the
  latter. `_json_dest_is_fresh` compares each destination's pre-run (mtime,
  size) against its current one, and `_reject_unusable_report` handles the
  resulting synthetic `stale` token beside the real validity tokens so the
  messages cannot drift apart. The two pre-run scalars
  (`_output_file_pre_fp`/`_extra_write_json_pre_fp`) remain for
  `_json_report_src`'s own chain; the map covers *every* destination, because
  freshness is a property each requested artifact needs and not only the ones
  that can become the verdict source.

  **Each report shape's version key gets its own threshold.** Three sequences
  reach the reader — `report_schema_version` (2.40),
  `audit_report_schema_version` (1.1) and `release_schema_version` (1.3) — and
  the two non-compare ones sit numerically below `(2, 40)` at every real
  version, so measuring either against the compare threshold makes those
  documents read as predating a field they carry. That was missed once for
  audit and again for release; a new shape needs an entry in
  `ASSURANCE_CONTRIBUTION_SINCE`, never a fallback.
- `_assurance_axis_contradictory` catches the one absence that *is* provably
  wrong: an `analysis_assurance` block on a schema ≥ 2.40 with no
  `analysis_assurance_exit_contribution` beside it. `reporter.py` emits those
  two under one `if`, so a half-present pair is an inconsistent report. A
  report carrying **neither** key is normal and must stay accepted — inferring
  the contradiction from the schema version alone fails ordinary green runs,
  which an earlier draft of this check did.

At exit 0, either one publishes `verdict: REPORT_UNREADABLE` and fails the
step unconditionally; no `fail-on-*` input waives it. At a nonzero exit neither
one replaces the verdict: the dispatch's own compatibility result (`BREAKING`,
`API_BREAK`, ...) stands and only `FINAL_EXIT` is forced to `1` — see the next
paragraph for why that asymmetry is deliberate rather than an oversight.

**The contradiction check's verdict is scoped to exit 0, and that is deliberate.**
`_resolve_clean_exit_verdict` runs only there, so at a nonzero exit a
self-contradictory report keeps the compatibility verdict the dispatch derived
(`BREAKING`, `API_BREAK`, ...) and the `FINAL_EXIT` check supplies the failure
and the error annotation. Do not "fix" that by overriding the verdict on every
exit path: at exit 2 the report's compatibility result is readable and
established, the two axes are orthogonal, and replacing a real break with "no
result was established" would *discard* evidence — the opposite of what this
value exists for. What the late check must never be relied on for is the exit-0
path, where there is no other readable result and the fallthrough would publish
`COMPATIBLE`; that is why the check is duplicated there rather than moved. **Don't "simplify" this by
making the axis predicates fail closed instead** — that is the forgeable-prose
path this section rules out, reached from the other direction.

**Decide it in `_resolve_clean_exit_verdict`, never only at the `FINAL_EXIT`
fold.** That fold runs after the verdict output, the job summary and the PR
comment are published, so a gate applied only there fails the step while
publishing `verdict=COMPATIBLE` and "No binary ABI break detected" — exit 1
beside a false clean result, which a workflow branching on the output (or under
`continue-on-error`) reads as a pass. The contradictory-assurance check shipped
with exactly that bug and was moved; when adding a condition here, assert the
**verdict output, the summary text and the exit code separately** in its test.
Asserting one and assuming the others agree is how they came to disagree.

### The residual: exit 0 with no JSON report requested

`_json_report_expected` is deliberately narrow, and the part it leaves open is
worth stating rather than discovering. When the primary format is not json,
this script injects an internal `--write json=$PR_JSON` sidecar for its own
PR-comment and annotation rendering. If *that* turns up missing, the run still
publishes `verdict: COMPATIBLE` — and at exit 0 the real tier could equally
have been `NO_CHANGE`, `COMPATIBLE_WITH_RISK`, or a `BREAKING`/`API_BREAK` the
severity policy demoted. So the published verdict can understate a demoted
break for a non-json-format run whose sidecar did not arrive.

That is not the same defect as the one above, and it must not be "fixed" by
extending `_json_report_expected` to the sidecar. Exit 0 is genuine evidence —
source #2 above, the kernel-reported answer to what the invocation itself said,
and `compare` exiting 0 means its own gate did not fire. So the run's
*acceptance* is established even when its *tier* is not, and
`REPORT_UNREADABLE` ("nothing read this run's result") would be the wrong
label: it would fail a step the tool itself passed. Closing this properly means
a new verdict value distinguishing "accepted, tier unverified" from
"accepted, compatible" — a change to this Action's declared `verdict` output
contract that every consumer branching on it sees, so it wants an ADR and its
own migration, not an incremental widening of the predicate here. Tracked as a
`KnownGap` on the `report.unestablished_result_reads_as_success` bug class
(`tests/regressions/manifest.py`).

**Known, accepted limitation (Codex review, P1, fresh evidence):** a
`scan --against` step whose own `extra-args` carries a non-JSON secondary
(`--write text=...`) reaches exactly this "genuinely unreadable" case for
the unconditional coverage/assurance/severity-category floors too — the
CLI's `--write` option takes one `FORMAT=PATH` operand, Click keeps only
the *last* occurrence of a repeated option, and `extra-args` is appended
after the internal injection, so a second, JSON-targeted `--write` this
script also appended would simply lose to the user's own and never
execute. There is no way to recover a structured report in this specific
combination without either silently discarding the user's explicit
`--write` choice (wrong — the whole point of `_extra_args_has_write_flag`
is not to do that) or unconditionally re-running the analysis a second
time purely to obtain one (`_maybe_post_pr_comment` already accepts that
cost, but only for the sticky-comment feature specifically, and only under
its own narrower guards — doing it unconditionally for the floors would
impose a second, potentially expensive analysis on every such run, a
different and larger trade-off than this bug fix's scope). So this one
combination is accepted, not fixed: the floors go blind exactly there, and
the fix is on the caller's side (drop the non-JSON `--write`, or add
`format: json`) rather than in this script.

**This is why `compare`'s and `scan`'s own `--write json=$PR_JSON` sidecar
injection is unconditional** (not gated on `pr-comment`, since Track T8):
`_severity_gate_categories`/`_coverage_gated`/`_assurance_gated` all read
that same JSON, and ADR-049's contract-coverage/analysis-assurance floors
and the severity-category gate below are *unconditional* checks that no
`fail-on-*` flag disables — they must not go blind just because a run's
nominal `format:` is `text`/`markdown` or `pr-comment: false` was set. A
prior revision of this sidecar injection was gated on `pr-comment` for
`scan`, on the reasoning that its only consumer was the sticky PR comment;
a Codex review (P1) on the PR that landed Track T8 found this false — with
`format: text` (scan's default) and `pr-comment: false`, a coincident ABI
break (which outranks those axes in the CLI's own max-fold) combined with
`fail-on-breaking: false` left no JSON anywhere, silently disabling floors
the AGENTS.md text right here already documented as unconditional. Don't
re-gate that injection on anything but the effective format, whether the
user's own `extra-args` already requested a `--write`, and whether
`extra-args` carries an *effective* `--dry-run` (`_extra_args_has_dry_run_
flag` — a dedicated `INPUT_DRY_RUN` is not the only way to request one,
and injecting `--write`/`-o` alongside a real `--dry-run` is a CLI usage
error, not merely redundant; `_maybe_post_pr_comment`'s own dry-run skip
checks the same effective flag, for the identical reason) — see the
injection's own comment in both mode branches for the exact conditions.

`fail-on-breaking`/`fail-on-api-break` and friends are **step policy on top
of** the published verdict. They decide whether the step fails; they never
rewrite `$VERDICT`.

## Known sharp edge: requested vs. achieved depth

An Action baseline generated with an explicit depth request (e.g.
`--depth`/build-info flags) can currently still exit successfully on a
degraded/partial snapshot if a layer silently fails to achieve that depth —
tracked as a gap in the Action's baseline-generation path, not yet closed
here. If you're touching depth-related inputs or `run.sh`'s flag assembly,
don't assume a successful exit implies the requested depth was achieved;
check the report's own coverage/degradation fields.

## Product invariants for CI integration

Local consequences of root `AGENTS.md`'s "Product decisions and change
routing" section for anything that runs abicheck from a workflow:

- **Shared semantics.** Equivalent *resolved* requests produce the same
  decision whether they came from the Action, a reusable workflow, the
  CLI, or the Python API; a workflow may add convenience (baseline
  resolution, PR comments, SARIF upload), never a second gate algorithm.
  Raw-input resolution still differs by front end today — the CLI and
  Action discover `.abicheck.yml` and apply `--profile`/`--pack`, a bare
  typed-API call does not — and full configuration-resolution parity is
  direction, not a shipped guarantee.
- **Partial scope is normal.** A matrix cell or a local run checks its own
  selected target/profile against the matching baseline member; other
  variants are out of scope, and an expected-but-missing artifact is an
  incompleteness signal (warn by default, block by configuration), never a
  fabricated removal.
- **Trusted baseline selection.** A baseline is chosen by identity and
  coordinates (`channel × target × profile`, digests), never by a moving
  "latest" without recording the exact resolved artifact.
- **Prebuilt consumers are inputs, not builds.** A consumer artifact
  supplied to a check is used as-is for static inspection; rebuilding or
  executing it is a separately designed, explicitly opted-in validation
  mode (ADR-060 remains deferred).
- **Structured outcomes only.** New decision semantics travel through the
  typed report/`ExitDecision` fields and Action outputs; never scrape log
  text to derive a verdict or a gate.

This section does not change the repository's own merge policy, which
stays as recorded in `.github/AGENTS.md`.

## ADR-065 completeness axis (`SCOPE_INCOMPLETE`)

`_scope_incomplete()` is the informational sibling of `_scope_gated()`: it
reads `_report_query ... scope_incomplete` (the report's own
`comparison_scope.completeness`/`run_outcome.scope` reading `incomplete`) and
only decides whether the step summary names an accepted gap under the default
`scope.on_incomplete: warn`; it never fails the step (Codex review).
`_scope_gated()` mirrors `_coverage_gated()` exactly (report contribution
first -- `_report_query ... scope_contribution`, the max of the release
`exit` block's `incomplete_scope_contribution`/`no_comparison_completed_
contribution`, else the stored-baseline dispatch's `comparison_scope`
section's own `*_exit_contribution` pair (that report shape has no root
`exit` block), printing *nothing* when the report carries neither, so
a scope-less document -- an older abicheck, a scalar report, or the
`{}`-shaped placeholder the PR-comment re-run leaves in `PR_JSON` when the
primary run wrote no report -- is "cannot tell", not "did not fire"). There
is deliberately **no** stderr fallback: an earlier revision grepped the
CLI's notice and excluded its `warn`-accepted wording, which a member
failure reason carrying PR-controlled text could forge to suppress a real
`block` contribution -- the same class ADR-063 Track T8 retired for the
coverage and assurance axes (Codex review). With no readable JSON the
process exit still fails the step; only the label is withheld. It feeds
the compare exit-1 dispatch (verdict `SCOPE_INCOMPLETE`),
the job-summary case (`scope_where` names the unchecked members), the
"also contributed" note, and an unconditional `FINAL_EXIT=1`, since no
`fail-on-*` input governs the axis. Tests: `tests/test_action_scope_verdict.py`.
