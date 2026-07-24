# `check-target` Action Reference

`actions/check-target` composes [`resolve-baseline`](resolve-baseline.md) +
`collect-facts` + the root `abicheck/abicheck` Action into **one resolved
check** — [ADR-047](../development/adr/047-github-actions-integration-model.md)
§4's single high-level primitive — and, once its own input validation
passes, always emits the [report envelope](#report-envelope-adr-047-7)
(§7), regardless of whether the baseline resolved, was a bootstrap "no
baseline yet" pass, or failed outright. An invalid invocation (e.g. a
missing required input, or an unsupported input combination) is rejected
up front, before any of that, and produces no report or outputs at all.

> **Status.** This page documents the `actions/check-target` composite
> Action shipped in G30 P1.3. The reusable workflows that generate a
> `run-plan.json` and fan this Action out over a matrix
> (`check-single.yml`/`check-project.yml`, G30 P1.4) are documented
> separately — see the
> [run-plan schema](run-plan-schema.md) and the
> [reusable workflows reference](reusable-workflows.md). `check-target` can
> also still be called directly as a step (ADR-047's S4 shortcut) or from a
> hand-written per-target workflow (S1/S2/S5/S6/S15/S21) without either.

## What it does

1. **Resolves the baseline** by composing [`resolve-baseline`](resolve-baseline.md)
   — skipped entirely when `baseline-channel: none` (a single-build audit
   with no baseline, ADR-047 §8 S5).
2. **Composes `collect-facts`** when `evidence-producer` requests build/source
   evidence: `phase: verify` for `wrapper`/`clang-plugin` (the caller's own
   workflow must run `collect-facts phase: prepare` *before* its build step,
   earlier in the job — `check-target` runs after that build already
   happened and cannot retroactively instrument it), or `phase: auto` for
   `replay` (no pre-build hook needed).
3. **Runs the analysis** — the root Action's `compare` mode against the
   resolved baseline, or (`baseline-channel: none`) `scan` mode with no
   `--against` (a one-build audit).
4. **Writes the report envelope**, once input validation (the very first
   step) has passed — even when steps 1 or 3 above then fail, since the
   internal resolve/analysis steps run with `continue-on-error: true`
   specifically so this step always runs afterward (ADR-047 §7). A
   validation failure short-circuits before any of steps 1-4 run at all.
5. **Owns its own composite exit code**, per `gate-mode` (below) — the very
   last thing this Action does, never an implicit pass-through of an
   internal step's raw exit code.

## `gate-mode`

| Mode | This job's exit code | Who computes the real gate |
|------|----------------------|------------------------------|
| `local` (default) | Reflects the real compatibility finding (today's root-Action behavior). | This job itself. |
| `deferred` | `0` for a compatibility finding, whatever it is — **but still nonzero on an operational error** (a `resolve-baseline` failure, or the analysis step never producing a report). | A trailing fan-in `aggregate` job, reading this check's *real*, un-neutralized `severity`/`exit_code` from its report. |
| `advisory` | Same as `deferred`. | Nobody — findings are visible (in `compatibility_verdict`/`policy_gate_decision`) but never gate CI (shadow-rollout burn-in, S26). |

**`deferred` vs. `advisory` reports differ in one important way:** a
`deferred` report's `severity`/`exit_code` block stays the **real** value —
that's exactly what a trailing `aggregate` job's `exit_code()` (a `max()`
over every report's real gate) needs to compute the actual gate centrally.
An `advisory` report's legacy `severity.exit_code`/`blocking` are
**neutralized to `0`/`false`** so an advisory check can never accidentally
raise `aggregate`'s computed exit code above what the required cells alone
would produce — the real finding stays fully visible in the new
`compatibility_verdict`/`policy_gate_decision` fields for humans/PR
comments/SARIF, just never in the field `aggregate` gates on.

**Operational errors are never deferred or hidden**, regardless of
`gate-mode`: a `resolve-baseline` failure (`not_found`/`ambiguous`/
`wrong_profile`/`stale_schema`/`incompatible_evidence`) or the analysis step
never producing a report always fails this job's own exit code, exactly
like `resolve-baseline`'s own fail-loud contract requires.

## Target kinds (ADR-047 §3)

`target-kind` selects which `compare` flags this check builds — only
meaningful when `kind: target` (never `kind: bundle`):

| `target-kind` | Compare shape | Extra inputs |
|---|---|---|
| `library` (default) | A plain `compare`. | — |
| `app-consumer` | `compare --used-by` (S22, application compatibility). | `consumer-binary`, `verify-runtime` |
| `plugin-contract` | `compare --required-symbols` (S23, plugin/dlopen contract). | `contract-file` — a `.syms` file, one required linker symbol per line, `#` comments allowed; **not** YAML |

**The "library redirect" (ADR-047 §3):** `app-consumer`/`plugin-contract`
targets have no binary/baseline of their own — they resolve *through* the
library they scope. Set `baseline-target` to that library's id so
`resolve-baseline` looks up the right baseline, while `name` stays the
contract target's own name (`myapp-consumer`, `ioc-plugin-contract`) for
this check's own `check_id`/`target_id` identity, and `new-library` points
at that library's own candidate binary (candidate lookup needs the same
redirect as the baseline lookup — an app-consumer/plugin-contract target
has no `binary_pattern` of its own to resolve either side from).

## `kind: bundle` (S14)

A bundle-scoped check never resolves a single snapshot — `resolve-baseline`
returns the bundle's staged member **binaries** (`binaries-dir`), since
`abicheck/bundle.py`'s cross-library graph reads real ELF binaries, not JSON
snapshots. `check-target` hands that directory to the root Action's
`compare` mode as `old-library` directly — a plain directory-operand
compare, which `compare` already fans out to a per-library comparison
(including cross-library bundle findings) automatically; no separate
"bundle compare" mode or CLI command is invoked. `new-library` is the
caller-provided directory of the candidate build's own member binaries.

## Inputs

| Input | Required | Default | Meaning |
|-------|----------|---------|---------|
| `kind` | no | `target` | `target` or `bundle`. |
| `target-kind` | no | `library` | `library` \| `app-consumer` \| `plugin-contract` (`kind: target` only). |
| `name` | yes | — | This check's own identity (`check_id`/`target_id`) — the target or bundle id. |
| `baseline-target` | no | (= `name`) | Which target's baseline actually resolves — set to the referenced library for `app-consumer`/`plugin-contract`. Ignored for `kind: bundle`. |
| `bundle-members` | when `kind: bundle` | `[]` | JSON array of the bundle's member target ids. |
| `profile` | yes | — | The build `profile.id` this check runs under. |
| `baseline-channel` | yes | — | A channel name, or the literal `none` for a no-baseline audit (S5). |
| `baseline-path` | when channel ≠ `none` | `''` | Forwarded to `resolve-baseline`. |
| `baseline-required` | no | `true` | Forwarded to `resolve-baseline`'s `required`. |
| `candidate-build-output` | no | `''` | Forwarded to `resolve-baseline`'s `incompatible_evidence` check. |
| `requested-depth` | yes | — | `binary` \| `headers` \| `build` \| `source`. |
| `gate-mode` | no | `local` | `local` \| `deferred` \| `advisory`. |
| `project` | no | `${{ github.repository }}` | Recorded in the report envelope. |
| `head-sha` | no | `${{ github.sha }}` | Recorded in the report envelope. |
| `base-ref` | no | `''` | Recorded in the report envelope. |
| `evidence-producer` | no | `''` | `''` (no source evidence needed) \| `replay` \| `wrapper` \| `clang-plugin`. |
| `evidence-pack-path` | no | `abicheck_inputs` | Must match an earlier `collect-facts phase: prepare` step's own output path (`wrapper`/`clang-plugin` only). |
| `new-library` | yes | — | Candidate binary (`kind: target`) or directory of candidate member binaries (`kind: bundle`). |
| `consumer-binary` | when `target-kind: app-consumer` | — | Forwarded as `--used-by`. |
| `verify-runtime` | no | `false` | Forwarded when `target-kind: app-consumer`. |
| `contract-file` | when `target-kind: plugin-contract` | — | Forwarded as `--required-symbols`. |
| `header`, `old-header`, `new-header`, `include`, `old-include`, `new-include`, `lang`, `ast-frontend`, `gcc-path`, `gcc-prefix`, `gcc-options`, `sysroot`, `sources`, `build-info`, `compile-db`, `build-config`, `policy`, `policy-file`, `suppress`, `severity-preset`, `severity-addition`, `extra-args`, `python-version`, `install-deps` | no | (mirror the root Action) | Forwarded straight through to the internal analysis step. |

## Outputs

| Output | Meaning |
|--------|---------|
| `outcome` | The `resolve-baseline` outcome, or `skipped` when `baseline-channel: none`. |
| `check-id` | `target@profile#baseline_channel@requested_depth` — always includes the depth suffix, even in the common single-depth case (ADR-047 §7). |
| `verdict` | The legacy `verdict` field: one of the five `Verdict` values, `ERROR` (operational failure), or `NO_BASELINE` (bootstrap pass — deliberately not a `Verdict` member, never a compatibility verdict). |
| `compatibility-verdict` | Mirrors `verdict`'s casing, empty when unavailable (an operational-failure or bootstrap report). |
| `policy-gate-decision` | `pass` or `fail` — this check's own real gate decision, computed before any `gate-mode: advisory` neutralization. |
| `report-path` | Path to the final, enriched report JSON. |

## Report envelope (ADR-047 §7)

Every run writes a single JSON report at
`check-target-report-<name>-<profile>-<baseline_channel>-<requested_depth>.json`
(the exact path is always available via the `report-path` output — don't
hard-code the filename, since running `check-target` more than once in the
same job, e.g. the same target against two baseline channels, would
otherwise overwrite an earlier run's report), starting from whatever the
underlying `compare`/`scan` run already produced and layering on the
fields below. For a normal single-library `compare` (the common case),
that starting shape is `abicheck/reporter.py`'s existing compare-report
shape (`report_schema_version: "2.13"`). A `baseline-channel: none` audit
instead starts from a `scan` report (its own `scan_schema_version` shape),
and a `kind: bundle` check starts from the CLI's per-library release
fan-out summary (`libraries`/`old_dir`, no schema-version marker of its
own) — neither of those two carries `report_schema_version`.

- `check_id`/`target_id` — always the same, fully-qualified,
  depth-suffixed value, so `abicheck aggregate`'s exact-match lookup lines
  up for every check, not only ones sharing a target with another check.
- `profile_id`, `baseline_channel`, `requested_depth`, `effective_depth`
  (may be shallower than requested when the evidence wasn't actually
  available — `check_evidence_coverage` records why).
- `compatibility_verdict`/`policy_gate_decision` — the new, richer fields —
  **alongside**, never instead of, the legacy `verdict`/`severity` fields
  `abicheck/aggregate.py` already parses (the dual-write requirement).
- `operational_errors` — non-empty exactly when this check hit an
  infrastructure/config problem rather than (or in addition to) a
  compatibility finding.
- `publication` — whether/where this report was actually published.

A `resolve-baseline` failure or a bootstrap ("no baseline published yet")
pass synthesizes this same envelope from scratch — a report always exists,
even when no comparison ever ran.

## Example

```yaml
- name: Check libpvxs against accepted-main
  uses: abicheck/abicheck/actions/check-target@v1
  with:
    name: libpvxs
    profile: linux-x86_64-gcc13-release
    baseline-channel: accepted-main
    baseline-path: ./restored-baseline # staged by an earlier actions/cache step
    requested-depth: headers
    gate-mode: local
    new-library: build/lib/libpvxs.so
    header: headers/pvxs/*.h
```

```yaml
# S5: single-build audit, no baseline.
- name: Audit libpvxs (no baseline)
  uses: abicheck/abicheck/actions/check-target@v1
  with:
    name: libpvxs
    profile: linux-x86_64-gcc13-release
    baseline-channel: none
    requested-depth: headers
    gate-mode: advisory
    new-library: build/lib/libpvxs.so
    header: headers/pvxs/*.h
```
