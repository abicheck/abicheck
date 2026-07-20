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
2. `action/install-deps.sh` — installs gcc/g++/clang/bear and invokes the
   checksum-pinned `action/install-castxml.sh` Superbuild installer on Linux,
   or installs castxml via Homebrew on macOS, when `install-deps: true`.
   Windows install is not automated (warns only).
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

## Known sharp edge: requested vs. achieved depth

An Action baseline generated with an explicit depth request (e.g.
`--depth`/build-info flags) can currently still exit successfully on a
degraded/partial snapshot if a layer silently fails to achieve that depth —
tracked as a gap in the Action's baseline-generation path, not yet closed
here. If you're touching depth-related inputs or `run.sh`'s flag assembly,
don't assume a successful exit implies the requested depth was achieved;
check the report's own coverage/degradation fields.
