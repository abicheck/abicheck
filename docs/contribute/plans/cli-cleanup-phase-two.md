---
doc_type: contributor
level: advanced
lifecycle: historical
---

# CLI cleanup, phase two — completed work record

**Status: closed (2026-09-06).** This plan finished the interface-hygiene
work [#770](https://github.com/abicheck/abicheck/pull/770) started, and its
remaining scope moved to
[`one-comparison-product.md`](one-comparison-product.md)
([ADR-068](../adr/068-one-comparison-product-and-scan-retirement.md)). It is
kept as a **record of what landed and which decisions must not be
re-litigated** — not as an active plan, and not as a second source of truth
for anything.

> **Where the detail went.** This file was ~5,900 lines of accumulated review
> checkpoints, each recording the state of the tree on the day it was written.
> That history is in git
> (`git log -p --follow -- docs/contribute/plans/cli-cleanup-phase-two.md`;
> the last full-length revision is `a92a4a89`), and the durable technical
> findings it accumulated — every reverted fix attempt and the reasoning
> behind each — were already mirrored into
> [`known-gaps.md`](../known-gaps.md), which `AGENTS.md` names as their
> canonical home. Nothing was lost in the compression; a second copy was
> removed. **Read `known-gaps.md`, not git history, before re-attempting a
> fix in an area this plan touched.**
>
> Other documents (notably
> [ADR-064](../adr/064-canonical-gate-algorithm-and-exit-decision.md) and
> [G41](g41-baseline-consumer-context-and-declarative-assurance.md)) cite
> section names from the full-length revision — "PR 4", "New since the plan
> was written", and similar. Those sections are gone; what they asserted is
> in "What landed" below, and the reasoning is at `a92a4a89`. No link breaks,
> since none of them were anchor links.

## Why it closed

The plan's own final checkpoints had already demoted it: it stopped being the
owner of CLI direction, and "remove five more flags" stopped being the next
milestone. The audit behind ADR-068 then found what the remaining sequence
could not see from inside its own frame — **`compare` cannot reach `scan`'s
checks at all** (the only production callers of `run_crosschecks`,
`pattern_scan.scan_files` and `run_preprocessor_scan` under `abicheck/` are
three lines in `scan_engine.py`). Tidying two commands in parallel was the
wrong shape of work; retiring one and moving its capabilities into the other
is the right one.

## What landed

| PR | What shipped |
|---|---|
| **0** | Green-CI baseline prerequisite |
| **0B / A** | Repository governance (required checks / Ruleset) — **closed by maintainer decision**, not by implementation: merge-blocking CI was applied and then deliberately reversed |
| **1** | Presentation removals (`--stat`, `--recommend`) |
| **1b / E** | Annotations moved out of the CLI into the composite Action (`action.yml`'s `annotate`/`annotate-additions`) |
| **2** | `aggregate` policy folded into the manifest schema |
| **B** | Effective-configuration parity: `--pack`'s `policy.overrides`/`surface.internal_namespaces` **and** `gate.*` fields reach `compare`, `scan --against` and the release fan-out alike; effective-config digest on the native compare/release JSON, `--stat` JSON and `scan --against` JSON |
| **C** | `dump`'s real run migrated onto `execute_dump_request` — ELF first, then PE/Mach-O (that half verified by mock-based CLI/unit tests only; no PE/Mach-O toolchain was available). Tail: explicit-`--config` dry-run/execution parity, closed by the `InputSpec.build_config` seam |
| **D** | Build execution moved into trusted config |
| **F** | `dump --allow-build-query` retired in favour of an explicit `--config` as the only authorizer |
| **G1** | Canonical `ExitDecision` + report block (additive; landed early, no prerequisites) |
| **G2** | `--exit-code-scheme`, `.abicheck.yml`'s `exit_code_scheme:`, the `kind: gate` pack field and the typed API's fields all deleted — the gate algorithm is fully automatic ([ADR-064](../adr/064-canonical-gate-algorithm-and-exit-decision.md) stage 2) |
| **H1** | Six hidden inert shims and duplicate spellings deleted in two slices — `--allow-build-query`, `--header-graph`, `--header-graph-includes` (#1080); `--btf`, `--ctf`, `--dwarf` (#1087). Each now exits `64` with `No such option` |
| **I** (part) | Stored-`BundleFacts` operand classification; `compare --old-bundle-facts` deleted; stored/stored execution |
| **J** (part) | `--manifest` → `--instantiation-manifest`; `--bundle-system-providers`/`--bundle-cohort` moved to `.abicheck.yml` |

Also recorded here at the time, but owned by
[`vision-api-abi-evolution.md`](vision-api-abi-evolution.md) and tracked
there: workstream slices A-S1, A-S2, C-S1, D-S1, E-S1 and E-S2.

## Where the remaining scope went

| Item | Disposition |
|---|---|
| **PR H** — `scan --artifact-set` member-identity manifest | **Cancelled.** The mode is deleted, not extended; the declared-provider capability becomes `.abicheck.yml` configuration read by the canonical multi-component path ([ADR-056](../adr/056-multi-artifact-library-set-scan.md) superseded) |
| **PR I** — full `BundleCompareRequest` unification (one evaluation/gate/report/dry-run path across every operand shape) | [`one-comparison-product.md`](one-comparison-product.md) Phase 7d, narrowed — with `scan` gone there are fewer operand shapes to unify |
| **PR J** — per-library header/compile-context topology in `BundleSpec`; `--max-json-object-nodes` → a calibrated resource limit | Same plan, Phases 7d and 7g; still blocked on G42 provider resolution and a real bytes-per-node calibration |
| `scan --artifact-set` bundle-topology config read | Cancelled with the mode |
| `contract=public` default flip | Same plan, Phase 9 — still gated on `EntityId`-based public closure ([ADR-063](../adr/063-one-semantic-pipeline.md) Phase 2), **not** a string heuristic |
| **Item 2** — `scan`'s L4 source-extractor default diverging from `dump`/`compare` | **Dissolves with `scan`.** Its explicit-request half closed 2026-09-02; the unflagged-default half disappears when `scan_engine` does |

## Decisions that must not be re-litigated

These are the conclusions the deleted checkpoints were protecting. Each cost
real review rounds; re-deriving them from scratch is how they get reversed by
accident.

- **Everything in "What landed" stays landed.** In particular
  `--exit-code-scheme`, `--old-bundle-facts`, the compare provider/cohort
  switches, bare compare `--manifest`, the release fan-out's `GateOptions`
  and the shared gate-pack fold are closed. Do not re-open them.
- **`allow_build_query` is not a CLI concern.** The *flag* was a deprecated
  no-op and is gone, but the engine-side parameters are a live programmatic
  permission gate and must stay: `service_dump_pipeline.execute_dump_request`
  and `workflows/artifact/execute.py` use it as one
  (`frontends/cli/dump_execute.py` passes `True` deliberately, precisely
  because the CLI flag was never a trust signal), and
  `cli_scan_helpers.resolve_effective_allow_query`'s parameter is load-bearing
  as the ADR-037 D4 level-implies-query guard.
- **A gate application reads the resolved gate config; it never re-derives
  one.** Re-deriving once let a severity-only gate pack silently override an
  explicit scheme. The flag that exposed this is gone; the principle governs
  the surviving `gate.severity.*` fold.
- **Pack-field routing is complete-or-rejected.** A routable field with no
  engine consumer, a consumer that only runs under contract evaluation, an
  inert value, or an empty `assignments` mapping is a usage error — never a
  silently inert assignment (`UNAPPLIED_PACK_FIELDS` and its three siblings).
- **`--used-by` keeps its input and does not replace the gate** — consumer
  impact enriches the global result (vision D-S1, landed).
- **`--fail-on-removed-library` is not deleted**; its *input* is fixed first,
  so it consumes proven removals rather than filename-stem set differences
  ([ADR-065](../adr/065-comparison-scope-selection-and-completeness.md)).
- **`--require-complete-analysis` means "the required applicable capabilities
  for the selected task were checked"**, not "every evidence layer was
  available". Stripped-binary and header-only tasks stay first-class.
- **No deprecation aliases.** A removed spelling exits `64` with `No such
  option`; hidden-but-accepted is not a deprecation window this repository
  runs.

## Merge criteria for a removal PR

Written here, still applied by
[`one-comparison-product.md`](one-comparison-product.md) Phase 7 — the one
operational section worth carrying forward rather than rewriting:

- **CLI** — the old spelling errors with `No such option`, exit `64`, no
  hidden alias; `--help`/`--help-all` regenerated; the root command tree
  unchanged (`tests/test_cli_root_surface.py`).
- **Front-end parity, in the same PR** — CLI, typed Python API, the composite
  Action, reusable workflows, Agent Skills (`skills-src/` plus regenerated
  trees), and the generated CLI/Action references.
- **Machine contracts** — when a manifest or report changes: schema version
  bump, packaged *and* documented schema copies, JSON Schema validation, an
  explicit backward-reading decision, effective values in provenance.
- **Semantics** — assert the compatibility verdict, the gate decision, the
  process exit, contract coverage and analysis assurance *separately*.
- **CI** — Linux, Windows, macOS, Action tests, the `cli-contract` gate and
  the docs/schema gates all green.
