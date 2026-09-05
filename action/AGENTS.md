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
`test_action_baseline.py`, `test_action_collect_facts.py`. These are plain
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
suppressed the internal sidecar) gets the plain exit-code-derived verdict —
no `SEVERITY_ERROR`/`COVERAGE_INCOMPLETE`/`ANALYSIS_INCOMPLETE` label and no
escalation, since no structured evidence stated one. That is deliberate:
absence of data is not evidence an axis fired.

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
re-gate that injection on anything but the effective format and whether
the user's own `extra-args` already requested a `--write` — see the
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
