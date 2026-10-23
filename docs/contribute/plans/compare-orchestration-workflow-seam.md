---
doc_type: contributor
level: expert
lifecycle: active
generated: false
---

# Compare orchestration out of the CLI frontend

**Owner ADR:** [ADR-061](../adr/061-responsibility-package-architecture.md) (comparison
coordination belongs in `workflows/`; `frontends -> workflows` is the legal direction).
**Parent plans:** [One comparison product](one-comparison-product.md) (ADR-068, the
`compare` surface and its flag rulings) and
[Duplication & convergence](duplication-and-convergence-assessment.md) (Phase 3,
`ExitDecision` completion). This plan does not change any flag, exit code, schema or
output; it moves *where* the orchestration lives.

**Status:** Proposed. Mapping done (this document); no code moved yet.
**Effort:** L, sliced below · **Risk:** high — `compare` is the product's gate.

## Problem

`run_compare` in `abicheck/cli_compare_helpers.py` (starts at line 222) is about
1000 lines and the most-churned file in the repository. It is one function that
does four things at once, behind Click option shapes:

1. **Flag and config folding** (≈lines 222–625): `--contract` and `--lang`
   resolution, `--used-by-manifest` merge, `.abicheck.yml` load and CLI-over-config
   merge, `--budget` parse, rejection of flag combinations, `policy.overrides`
   validation, the unmatched-exclusion warning.
2. **Dry run and fan-out dispatch** (≈lines 626–798): `emit_dry_run`, then the
   directory/package release fan-out.
3. **Single-pair resolution** (≈lines 799–1118): inline evidence (`--old-sources`,
   `--build-info`), out-of-band packs, dump-manifest, `--use-cases`, post-manifest
   allowlist, acknowledgments, effective gate.
4. **Request assembly, classification and exit** (≈lines 1119–1216): builds the
   typed `CompareRequest`, calls `classify_resolved_pair`, maps
   `ProfileMismatchError`/`ScopeMismatchError` and `DeadlineExceeded` onto exits
   (`report_not_comparable_and_exit`, `_exit_on_budget_overflow` in
   `cli_compare_fold.py`), then renders through `frontends/cli/compare_result.py`.

Seven routing modules sit in front of it and only pass options along:
`frontends/cli/commands/compare.py`, `compare_routing.py`,
`compare_default_format.py`, `compare_no_baseline.py`,
`compare_no_baseline_set.py`, `compare_bundle_operand_dispatch.py`,
`compare_bundle_facts.py`. Three more sit beside them at the root:
`cli_compare_fold.py` (926 lines), `cli_compare_receipt.py`, `cli_compare_options.py`.

The consequence: the Python API cannot call the CLI's compare. Its typed path,
`run_compare_request`, does not run the CLI's config fold, dry run, acknowledgments
or the exit mapping, so the same inputs can produce different outcomes through the
two front ends. That is the cross-front-end divergence ADR-055/049 forbids.

## What already exists (reuse, do not parallel)

The owner is already present. Every slice extends it:

| Need | Existing owner | Gap |
|---|---|---|
| Typed request | `workflows/service_requests.py` `CompareRequest` / `CompareResult` | CLI builds it inline via `dataclasses.replace` at ≈line 1119 |
| Resolve both sides | `workflows/compare/resolve.py` `resolve_compare_request` | Used by the typed path only; CLI reaches the same helpers piecemeal |
| Classify | `workflows/compare/execute.py` `classify_compare_pair`, `workflows/compare_classify.py` `classify_resolved_pair` | CLI calls `classify_resolved_pair` directly with its own `RequestPolicyInputs` |
| Policy inputs | `workflows/compare_request_policy.py`, `compare_policy.py` | Project fold, `policy.overrides`, acknowledgments folded in the CLI |
| Exit code | `policy/exit_decision.py` `ExitDecision` (tie-inclusive `max()` fold) | Mismatch and budget exits are raw `sys.exit` in `cli_compare_fold.py` |
| Release fan-out | `workflows/release_compare.py`, `release_plan.py`, `release_exit_fold.py` | Dispatch and dry-run preview still in `run_compare` |
| Python shim | `workflows/compare/execute.py` `run_compare` (keyword shim) | Exists, but builds a request without the CLI's fold |

## Target shape

```
flags (Click) ──► CompareInvocation (frontends: parsed values, no logic)
                    │
                    ▼
              workflows.compare.invocation.compare(request)
                 ├─ fold   : config + flags -> CompareRequest + PolicyInputs
                 ├─ plan   : dry-run receipt | fan-out | single pair   (one resolved plan)
                 ├─ run    : resolve -> classify -> finalize
                 └─ result : CompareOutcome(result, ExitDecision, not_comparable | budget)
                    │
                    ▼
frontends: render(outcome) -> stdout/-o files; process exit = outcome.exit_decision.code
```

Rules this must keep (from `workflows/AGENTS.md`):

- The dry run and the real run consume **the same resolved plan**. No second
  estimator.
- A workflow **returns** achieved facts and decisions; it never prints, never calls
  `sys.exit`, never raises `click.UsageError`. Usage errors map to a typed
  `ValidationError` that the adapter renders as exit 64.
- Errors keep their contract: `SnapshotError` is operational (exit 1), `ValidationError`
  is usage (exit 64), `ProfileMismatchError`/`ScopeMismatchError` become a
  `not_comparable` outcome (exit 16, `_EXIT_NOT_COMPARABLE` in
  `frontends/cli/runtime.py`), `DeadlineExceeded` becomes a `budget` outcome (exit 5).
  `PlanningError` (raised through `resolve_compare_request` when no collector/backend
  combination can satisfy the request) takes the same route as `ValidationError`: the
  current compare adapter (`cli_resolve.py`) maps it to `click.UsageError`, exit 64.
  The compare adapter keeps that mapping in S6.
  Characterization tests pin each before it moves.

## Slices

Each slice is behavior-preserving, lands with its own characterization tests, and
lowers the matching `architecture/debt.yaml` baseline when its source file shrinks.

| # | Slice | Moves | Lands in |
|---|---|---|---|
| S0 | **This plan** (mapping, owners, slice order) | docs only | this PR |
| S1 | Policy-input fold | `pf` fold, `policy.overrides` validation, acknowledgments, post-manifest allowlist, effective gate → one `workflows` function returning `RequestPolicyInputs` | `workflows/compare_configuration.py` (or a new `compare_inputs_fold.py`) |
| S2 | Typed outcome for the exit tail | `ProfileMismatchError`/`ScopeMismatchError`/`DeadlineExceeded` → `CompareOutcome` variants; exit codes derived from `ExitDecision`; `sys.exit` removed from `cli_compare_fold.py` | `workflows/compare_outcome.py` (new), `policy/exit_decision.py` |
| S3 | Request assembly | the `dataclasses.replace(resolution_request, ...)` block (≈1119–1160) → one builder beside `resolve_compare_request` | `workflows/compare/resolve.py` |
| S4 | Release dispatch and dry-run out of `run_compare` | directory/package branch (≈688–798) and `emit_dry_run` call → `workflows.release_compare` planning entry | `workflows/release_*` |
| S5 | Single-pair resolution | inline evidence, packs, dump-manifest, `--use-cases` (≈799–1118) → `resolve_compare_request` callers | `workflows/compare/resolve.py` and `workflows/compare_inline_evidence*` |
| S6 | Public entry and adapter | `compare(request)` (the typed Python API); `run_compare` (CLI) becomes flags -> request -> `render`; delete the seven routing modules' pass-through functions | `workflows/compare/invocation.py` (new); `frontends/cli/commands/compare*.py` |
| S7 | Delete | `cli_compare_helpers.run_compare` and dead helpers; shrink `cli_compare_fold.py`/`cli_compare_receipt.py`/`cli_compare_options.py` | — |

S0–S2 are the first PR's scope. S1 is the smallest slice that moves a real
decision out of the frontend without touching output, so it is the proposed first
code slice. S3–S7 depend on S1–S2 and must not be started in the same PR.

## Tests

- **Characterization first.** Before S1 moves a fold, a test drives the current
  `run_compare` (CLI, via `CliRunner`) and the typed `run_compare_request` over the
  same inputs and pins the exit code and output. Each slice must keep those green
  unchanged.
- **Exit-code matrix against the new interface** (S2 onward). Tests call
  `workflows.compare.invocation.compare` (or its outcome builder), not Click, and
  cover at least: compatible, source break, ABI break, `not_comparable` (profile
  mismatch), `budget`, zero comparisons (must never read as a clean pass), and a
  directory/package release. Each asserts `ExitDecision.code` and its `reasons`.
- Patch the owner, never a facade (`workflows/AGENTS.md`, `frontends/AGENTS.md`).

## Out of scope

- Changing any flag, `--help` text, exit code, report schema or output format. Those
  need their own ADR and migration (`vision.md`: direction is not permission).
- `CompareRequest.scope_public` retirement (a deferred Python-API decision, per
  `one-comparison-product.md`).
- The release fan-out's internal member model (owned by `release_members`).

## Risks

- **Import cycles.** `cli_compare_helpers` is in the registration cycle. Moving
  code out of it must not add a `workflows -> frontends` edge. Check with
  `python scripts/check_architecture.py` after every slice.
- **File size.** New `workflows` modules stay under the 800-line production cap. No
  `debt.yaml` baseline is raised to make room.
- **Silent divergence.** The CLI and the typed API must agree on decisions after S1.
  A cross-front-end test on identical resolved requests guards this.
