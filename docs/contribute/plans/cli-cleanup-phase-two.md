# CLI cleanup, phase two — reviewed plan

**Type:** Refactor / interface-contract plan. Continues the hard cleanup landed
by [#770](https://github.com/abicheck/abicheck/pull/770)
(`refactor(cli)!: hard cleanup — remove superseded and duplicate flags`), which
physically deleted the superseded aliases and no-op flags with **no deprecation
window**. Related: [ADR-037](../adr/037-cli-interface-contract.md) (CLI
interface contract), [ADR-043](../adr/043-cli-pre-1.0-surface-reset.md) /
[ADR-054](../adr/054-cli-project-integration-surface-consolidation.md)
(root-surface admission),
[ADR-047](../adr/047-github-actions-integration-model.md) (Action model),
[ADR-056](../adr/056-multi-artifact-library-set-scan.md) (`scan --artifact-set`).
**Effort:** L (six independent PRs) · **Risk:** mixed — PR 1 is
presentation-only, PR 4 changes what a CI job's exit code means.

## Problem

Phase one removed the *duplicate spellings*. What is left is a different class
of surface: options that are real, single-spelling, and still wrong to keep on
the native CLI, because they encode something that belongs somewhere else —
in a renderer default, in a GitHub Action, in a versioned manifest, in trusted
project config, or in the gate's own semantics.

A first draft of phase two proposed removing all of them in one hard-cleanup
PR shaped like #770. This plan is the reviewed version of that draft. The
review's two substantive corrections are that `--exit-code-scheme` is **not** a
cosmetic flag (it selects between two different gate algorithms, so deleting it
silently changes existing CI outcomes) and that `scan --artifact-set` is **not**
redundant surface (it is a deliberate safety boundary from ADR-056 D2, and
replacing it with implicit positional-directory dispatch is a regression, not a
simplification).

## Scope at a glance

| Candidate | Verdict | Action |
|---|---|---|
| `--exit-code-scheme` (compare, scan) | Remove, but reworked | Own ADR + semantics PR: keep two orthogonal axes, drop the *manual algorithm selector* |
| `compare --stat`, `compare --recommend` | Remove | `--format review` replaces `--stat`; recommendation becomes an unconditional renderer output |
| `scan --artifact-set` | **Keep** | Refine the value syntax only (repeatable option / manifest); do not overload positional `DIRECTORY` |
| `--annotate`, `--annotate-additions` | Remove from CLI | Render annotations in the composite Action from an existing report |
| `dump --build-query`, `dump --build-compile-db` | Remove from CLI | Move to explicitly-trusted `.abicheck.yml`, with a real trust + dry-run contract |
| `aggregate --on-missing-required`, `--on-unexpected-target` | Remove from CLI | Move the policy into the manifest / run-plan schema alongside the expected target set |

Everything above is a **breaking** change to the native CLI. Consistent with
#770 and ADR-037's stance, none of it gets a deprecation alias: an old spelling
must fail as `No such option` with exit `64`.

## PR 0 — restore a green CI baseline first

This is a prerequisite, not a nicety: a breaking interface change cannot be
evaluated against a red baseline.

**Verified state** (`ci.yml`, run `31882605683`, head `13be967` on `main`):
19 of 20 jobs pass; the single failure is
`unit-tests (windows-latest, 3.13, false)` at the step
*"Run tests (Windows — parallel, no coverage)"*, with **6 failed, 27628 passed**.
All six failures are in the Action shell-script tests, and all six are
MSYS/Git-Bash path-translation assumptions rather than product defects:

- `tests/test_action_run_sh_py_safe_path.py` — `TestPySafeDirFailsClosedWhenMktempFails`,
  `TestPyBinHasAbicheckFallback`, `TestPySafeDirCleanedUpOnEarlyExit`,
  `TestPyBinResolvedAsAbsolute` (a `/tmp/tmp.…` path read back as a
  `WindowsPath`; `/c/hostedtoolcache/.../python3` not importing `abicheck`;
  `/c/Users/...` not `is_absolute()`).
- `tests/test_action_run_sh_severity_summary.py` — `TestReportPathAnchoring`
  (`C:\Users\…` expected, `/c/Users/…` produced).

The same job has been failing on every recent `main` commit, so this is a
standing red lane, not #770's regression.

**Work**

1. Fix the six tests (or the script's path handling if the script is genuinely
   wrong under Git-Bash) — classify each as *test-environment assumption* or
   *product bug on Windows*, and say which in the PR body.
2. Do **not** paper over it with a blanket `continue-on-error` or a
   Windows-wide skip; a marker-level skip needs a named reason and must keep
   `ABICHECK_MIN_EXECUTED` honest (`tests/conftest.py`'s silent-skip guard).
3. Reconcile branch protection with reality. The repository reports
   `protected: true` with `required_status_checks.enforcement_level: off` and
   empty `contexts`/`checks`; unless an equivalent GitHub Ruleset supplies them,
   a red CI does not block merge. Add required checks (branch protection or
   Ruleset) covering the Linux/macOS/Windows unit lanes, `lint-and-types`, and
   `ai-readiness`.
4. Add exact-merge-SHA verification on `push: main`, so a merge that did not
   run the checks it claimed is detectable after the fact.

**Done when** a full `ci.yml` run on `main` is green on Linux, macOS and
Windows, and a red required check demonstrably blocks a merge.

## PR 1 — presentation and GitHub transport

Removes `compare --stat`, `compare --recommend`, `compare --annotate`,
`compare --annotate-additions` (and the same two annotate flags on the
directory/package release fan-out in `cli_compare_release.py`).

### `--stat`

`--stat` produces a compact one-line summary. `--format review` already exists
for compact human output, and every machine consumer already has the full JSON
`summary` block. So `--stat` is a third spelling of an answer the CLI gives
twice already.

- Remove `--stat` and its plumbing (`cli.py`'s `stat=` threading through
  `_emit_report`/`announce_coverage_floor`).
- Any profile or docs snippet using `--stat` moves to `--format review`.
- `--show-only` interactions documented against `--stat` are re-stated against
  `--format review`.

### `--recommend`

`release_recommendation` is *always* in the JSON report; `--recommend` only
decides whether the human renderers print it. That is a renderer default, not a
user-facing capability. Change the defaults first, then delete the flag:

- `review`: always shows the recommendation.
- `markdown`: always shows it (own section).
- `html`: shown in the summary block.
- `json`/`sarif`/`junit`: unchanged — the field is already unconditional.

### `--report-mode` stays as-is

`--report-mode full|leaf|impact|root-cause` answers *how findings are grouped*.
The recommendation answers *whether a release-action summary is printed*. These
are orthogonal, so do **not** fold `--stat`/`--recommend` into new report modes
(`summary`, `release`), and do not let `--report-mode` become the dumping
ground for renderer switches. A genuine `release` mode may be added later if it
describes a whole coherent report shape — but not merely to justify deleting a
boolean.

### Annotations move to the Action

`--annotate`/`--annotate-additions` only do anything when `GITHUB_ACTIONS=true`;
they emit `::error`/`::warning`/`::notice` workflow commands and are inert in an
ordinary shell. That is transport, not comparison semantics.

Target shape:

```text
abicheck compare … → JSON / SARIF / typed CompareResult
composite Action   → reads that result → emits ::error / ::warning / ::notice
```

The Action must **not** re-run the comparison to produce annotations. Behaviour
to preserve verbatim in the move: file/line mapping; escaping of workflow-command
values; the annotation count cap; additions annotated only under an explicit
Action input; suppressed and out-of-contract findings never becoming gating
annotations; annotations reflecting the same scoped gate the report does.

Action inputs `annotate` / `annotate-additions` are **kept** — only the CLI
flags go away.

**Tests.** A saved report fixture must produce, through the Action's renderer,
byte-identical annotations to what the CLI emitted for the same report before
the move. Plus: `compare --stat` and `compare --annotate` exit `64` with
`No such option`; `--format review` output contains the recommendation.

**Risk:** low — no analysis, verdict, or exit code changes.

## PR 2 — aggregate policy into the manifest schema

`aggregate` currently takes the expected target set from `--run-plan` /
`--manifest` / `--discovered-only`, but takes the *policy for violating that
expectation* from two separate CLI flags: `--on-missing-required fail|warn`
(default `fail`) and `--on-unexpected-target include|warn|fail|ignore`
(default `include`).

Expectation and the consequence of breaking it belong in one versioned
contract:

```json
{
  "aggregate_manifest_version": "1.1",
  "targets": [
    {"id": "linux-gcc", "required": true},
    {"id": "windows-msvc", "required": true}
  ],
  "gate": {
    "missing_required": "fail",
    "unexpected_target": "include"
  }
}
```

`project plan` emits the same fields into `run-plan.json` from `.abicheck.yml`.

- Defaults are unchanged (`missing_required: fail`, `unexpected_target: include`)
  when the manifest omits `gate`.
- `--discovered-only` stays, as the explicit escape hatch with no expected set;
  under it neither policy is applicable and supplying them is a usage error.
- The aggregate JSON records what actually applied:

  ```json
  {"effective_policy": {"missing_required": "fail",
                        "unexpected_target": "include",
                        "source": "run-plan"}}
  ```

Because this is a schema change: bump the manifest schema version, update the
packaged and documented schema copies, keep reading the older version, and add
JSON Schema validation for the new `gate` block.

**Tests:** manifest-supplied policy; run-plan-supplied policy; default policy;
missing required target; unexpected analyzed report; unreadable unexpected
report; `effective_policy` preserved in aggregate JSON; incompatible manifest
schema rejected; the removed flags exit `64`.

## PR 3 — build execution moves into trusted config

`dump --build-query` and `dump --build-compile-db` describe how the *project*
is built, not what this snapshot is. They are already documented as CLI
equivalents of the `.abicheck.yml` `build.query` / `build.compile_db` fields,
and `--build-query` is a **trusted executable command**, not a data path.

Kept on the CLI (genuine per-run inputs): `--build-info`, `--build-target`,
`--compile-db-filter`.

Config form (argv list, not a shell string):

```yaml
build:
  query: ["cmake", "-S", ".", "-B", "build", "-DCMAKE_EXPORT_COMPILE_COMMANDS=ON"]
  compile_db: build/compile_commands.json
```

**Prerequisites, all required before the flags are removed:**

1. Only an explicitly-passed `--config` may authorize executing `build.query`.
2. An auto-discovered `.abicheck.yml` never executes a query.
3. `dump --dry-run` prints the exact argv, the cwd, the resulting compile-DB
   path, and why the query will or will not run.
4. A source-depth request with no compile DB produces an actionable error:
   *"source depth requested, compile DB missing; configure `build.query` or
   provide `--build-info`"*.
5. Docs carry a minimal throwaway-config example, so the removed flags have a
   one-paste replacement.

**Risk:** medium — this is a trust boundary, and it is the one item here where
a mistake is a security regression rather than a UX one.

## PR 4 — one gate algorithm (`--exit-code-scheme` removal)

**This is the item the original draft got wrong, and it gets its own ADR.**

`--exit-code-scheme auto|legacy|severity` is not a spelling choice; it selects
between two gate algorithms with genuinely different outcomes:

| | compatibility-based | severity-based |
|---|---|---|
| clean | `NO_CHANGE`/`COMPATIBLE`/`RISK` → 0 | no error-level findings → 0 |
| | — | addition/quality error → 1 |
| source break | `API_BREAK` → 2 | potential-breaking error → 2 |
| ABI break | `BREAKING` → 4 | ABI-breaking error → 4 |

A compatible addition can block CI under one and not the other; a policy can
demote a real ABI break to `0` under the severity scheme. Contract coverage and
analysis assurance add their own orthogonal `1` on top
(`contract_coverage_exit.py`, folded with `max`). Deleting the selector without
defining the successor algorithm silently changes users' CI outcomes.

**Decision to encode in the ADR:** remove the *manual algorithm selector*, keep
both axes, and make today's `auto` the only behaviour:

```text
no gate/severity policy configured  → compatibility verdict decides 0/2/4
severity preset, severity config,
or gate pack in effect              → resolved GateDecision decides 0/1/2/4
```

The user configures *policy*; they no longer choose an implementation.

The report keeps stating both halves explicitly, so the number is explainable:

```json
{"verdict": "COMPATIBLE",
 "gate": {"exit_code": 1, "blocking": true,
          "reason": "addition configured as error"}}
```

**Atomic change set:** remove the flag from `compare` and `scan`; remove the
matching Action input; remove or replace `.abicheck.yml`'s `exit_code_scheme`;
make packs state gate *policy* rather than select an algorithm
(`pack_application.py` currently reads a resolved `gate.exit_code_scheme` —
that read becomes a policy read); record the effective gate mode in
report provenance; update CLI, typed Python API, Action and `aggregate` parity
tests together.

**Tests must separate the axes** — a single `exit_code == expected` assertion is
not enough. Assert independently: compatibility verdict, gate decision, process
exit, contract-coverage contribution, analysis assurance.

**Risk:** high (behavioural, user-visible in CI). Sequence it **after** the
mechanical PRs so a bisect over a red CI job lands on this PR unambiguously.

## PR 5 — `scan --artifact-set` refinement (not removal)

The draft proposed dispatching on the operand type:

```text
scan file.so   → single-artifact scan
scan directory → artifact-set scan
```

**Rejected.** ADR-056 deliberately refused exactly this. `scan ARTIFACT` has
always meant one artifact; a directory can hold dozens of DSOs; scope and cost
change by an order of magnitude; set mode is audit-only and incompatible with
`--against`. Making that switch implicit hides a scope expansion that today is
visible in the command line and in CI logs.

What is actually worth changing is the *value syntax*. The comma-separated form
(`_resolve_artifact_set_paths` in `cli_scan.py`) is the weak part:

```bash
# today
abicheck scan --artifact-set a.so,b.so,c.so

# proposed: repeatable option
abicheck scan --artifact-set a.so --artifact-set b.so --artifact-set c.so

# unchanged
abicheck scan --artifact-set directory/

# optional, for bundles needing stable IDs / expected-provider ownership
abicheck scan --artifact-set-manifest set.json
```

Invariants that stay: exactly one of positional `ARTIFACT` or `--artifact-set`;
`--artifact-set` is incompatible with `--against`.

**Sequencing note:** the syntax cleanup is lower value than finishing set-mode
*semantics* — expected provider DSO, a symbol moved between sibling libraries,
duplicated providers, L4 symbol reconciliation, cost estimation, and a
machine-readable dry-run. Do the semantics first if the two compete.

## Merge criteria for every removal PR here

**CLI**

- The old spelling errors with `No such option`, exit `64`. No hidden alias.
- `--help` / `--help-all` regenerated.
- The root command tree is unchanged (`tests/test_cli_root_surface.py`).

**Front-end parity** — updated in the same PR: CLI, typed Python API (where
applicable), the composite Action, reusable workflows, Agent Skills
(`skills-src/` + regenerated trees), generated CLI/Action references.

**Machine contracts** — when a manifest or report changes: schema version bump,
packaged *and* documented schema copies, JSON Schema validation, an explicit
backward-reading decision, effective values in provenance.

**Semantics** — assert compatibility verdict, gate decision, process exit,
contract coverage and analysis assurance *separately*.

**CI** — Linux, Windows, macOS, Action tests, the `cli-contract` gate and the
docs/schema gates all green.

## Out of scope / explicit non-goals

- No deprecation aliases or transition window (same stance as #770).
- No new root commands (ADR-043/ADR-054 admission bar).
- `--report-mode` does not become a general renderer-switch namespace.
- `scan DIRECTORY` does not silently become an N-artifact analysis.
- The gate/verdict separation is not collapsed.
- The ABICC `compat` dialect stays frozen.
- No further mechanical option-count reduction after this phase. The remaining
  advanced options are load-bearing; subsequent work should go to `--pack`
  parity, multi-DSO ownership, and contract-evaluation precision instead.

## Ordering

```text
PR 0  green CI + required checks     (prerequisite)
PR 1  presentation / transport       (low risk)
PR 2  aggregate policy schema        (schema)
PR 3  build execution → trusted config (trust)
PR 4  gate semantics + ADR           (behavioural, highest risk)
PR 5  artifact-set syntax refinement (optional, after set-mode semantics)
```
