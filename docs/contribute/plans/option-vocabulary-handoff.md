# ADR-091 migration — handoff (2026-10-31 ~05:30 UTC)

Orchestrator session: https://claude.ai/code/session_01Wg6N1ao6UcRXBsEc1ZuaXQ
Source of truth for decisions: `docs/contribute/adr/091-one-option-vocabulary.md` and
`docs/contribute/plans/option-vocabulary-unification.md` on `main`.

## Merged to main (14 PRs)
- #1849 ADR-091 + plan + Phase 0 (inventory `architecture/option-vocabulary-inventory.json`, registry `architecture/option-vocabulary.yaml`, `tests/test_option_vocabulary.py`)
- #1850 Phase 1 parity gate (name rule, value sets, no-growth budget, exceptions)
- #1851 `compare --set PATH=VALUE` + `CompareRequest.config_overrides`
- #1852 CLI renames (`--version`→`--label`, `--abi3`→`--abi3-floor`, `--old-root/--new-root/--sysroot`→`--root`)
- #1854 Action `set:` input
- #1856 removed CLI aliases; D7 amended: NO transition period
- #1857 `--set` on `dump`, `DumpRequest`, `project capture-variants`
- #1860 Action input renames (`frontend`, `compiler`, `compile-options`, `require-complete`, `public-header-dirs`, `old-/new-label`, `old-/new-debug-info`, one `policy`; old names = RETIRED tombstones)
- #1861 `compile.*` via `--set` on compare/dump; Action compile overlay in run.sh removed
- #1862 typed Python API renames (`old_label`, `policy: str|Path`, `acknowledgments`, `public_symbols`, `follow_deps`, `include_system_declarations`, `debuginfod`, one `contract`, `run_dump(frontend=)`)
- #1863 config renames (`acknowledgment:`→`acknowledgments:`, `source.method`→`sources.method`, semantics preserved)
- #1866 cleanup: reusable-workflow inputs renamed + inventoried, doc leftovers, R19–R21 accepted exceptions, stricter `accepted` check
(+ unrelated #1853 on main)

Status: Phases 0, 1, 2, 4 done. Phase 6 collapsed into each rename (no transition).

## Maintainer decisions (keep applying)
1. No transition period: old CLI/Python/config spellings deleted outright; old Action/workflow inputs become fail-closed RETIRED tombstones.
2. Balance over minimalism: budgets (compare --help 15, Action 25) are soft targets; no-growth on recorded counts. Option stays a flag/input if per-side, per-run/per-matrix, needed on first run, or commonly used. Only project-stable rare options move to config (`.abicheck.yml` + `--set`/`set:`). Same rule for CLI and Action.
3. `--set` is the escape hatch, not the primary UX. It cannot set a core concept (exit 64 names the flag).
4. R11 dropped: Action `format` + `output-file` stay (accepted deviation).
5. R15: `fail-on-breaking`/`fail-on-api-break` stay as a pure exit-code mapping in the Action (exit 4 / exit 2); no shell re-derivation.
6. Familiar spellings stay (`--include`, `--used-by`, `old-library`/`new-library`, `changed-path`, `search-path`) — R19/R20/R21 accepted.

## In flight (child sessions, model claude-haiku-5-5, not merged)
| Work | Session | Branch | State |
|---|---|---|---|
| Phase 3: R15 + R11 pinning tests + R11 "kept" amendment | session_01Wg6N1aRJHe25fPZAWJqjHm | feat/adr091-phase3-action-output-gate | PR #1867 open (see its "State / remaining work") |
| Phase 5: balanced surface cut | session_01Wg6N1Zr3YmNyAFWmUkbnwW | feat/adr091-phase5-surface-budget | told to push + open PR |

Phase 5 approved scope:
- CLI to config tier ONLY: `--used-by-manifest`→`consumers.manifests`, `--required-symbol`→`consumers.required_symbols` (@FILE kept), `--view`→`output.view` (new keys). Keep `--define`, `--severity-preset`, `--sources`, `--build-info`.
- Action to `set:` tombstones ONLY: `dso-only`, `include-private-dso`, `fail-on-removed-library`, `public-header-dirs`, `frontend`, `compile-options`, `nostdinc`; `jobs`/`allow-build-query` → fail-closed tombstones. Keep `compiler`, `lang`, `sysroot`, `severity-preset`, `build-info`, `compile-db`.
- Fix: stale `--view show=…` text in report code; provenance label for `consumers.required_symbols`; skill docs + `skills-src/.../graders/evidence.py` for new spellings (scoring unchanged); split oversized test files.
- Record new counts as no-growth budget; add dated D4 soft-budget amendment to ADR-091.

## Remaining after those two
1. Review + merge the Phase 3 and Phase 5 PRs (squash, after CI green; tell the other to merge main).
2. Phase 7: generate "same option on other surfaces" columns in cli/action/config references from the registry (optional in plan).
3. Open items recorded in plan: typed API does not apply `compile.*` from `config_overrides`; `run_dump(public_headers)` review.
4. Flip ADR-091 status to Accepted/implemented in the ADR, `adr/index.md`, `adr-surface-registry.yaml`.

## Resume prompt (paste into a new orchestrator session)

```text
You are the orchestrator of the ADR-091 option-vocabulary migration in github.com/abicheck/abicheck.
Read first: docs/contribute/plans/option-vocabulary-handoff.md (if present), docs/contribute/adr/091-one-option-vocabulary.md,
docs/contribute/plans/option-vocabulary-unification.md, architecture/option-vocabulary.yaml,
tests/test_option_vocabulary.py, AGENTS.md.

Maintainer decisions (binding): (1) no transition period — old CLI/Python/config spellings are
deleted; old Action/workflow inputs become fail-closed RETIRED tombstones; (2) balance over
minimalism — budgets are soft, no-growth on recorded counts; keep an option as a flag/input if it is
per-side, varies per run/CI matrix, is needed on a first run, or is commonly used; only
project-stable rare options go to .abicheck.yml + --set/set:; same rule for CLI and Action;
(3) --set is the escape hatch, not primary UX; (4) R11 dropped (format/output-file stay);
(5) R15 = fail-on-* as a pure exit-code mapping; (6) familiar spellings stay (R19-R21 accepted).

State: PRs #1849-#1852, #1854, #1856, #1857, #1860-#1863, #1866 merged (Phases 0,1,2,4 done).
In flight:
- Phase 3 (R15 + R11 pins + R11 "kept" amendment): branch feat/adr091-phase3-action-output-gate,
  child session session_01Wg6N1aRJHe25fPZAWJqjHm.
- Phase 5 (balanced surface cut, scope in docs/contribute/plans/option-vocabulary-handoff.md): branch
  feat/adr091-phase5-surface-budget, child session session_01Wg6N1Zr3YmNyAFWmUkbnwW.

Your job:
1. Find those branches' PRs (or ask the child sessions via send_message to open them). Review each
   diff against the decisions above (use a read-only subagent for an independent review), send
   fixes back to the child session, and merge (squash, expectedHeadSha) only when CI is fully
   green. After each merge, tell the other open branch to merge origin/main.
2. Then launch child sessions (claude-haiku-5-5, source_url the repo, own outcome_branch) for
   Phase 7 (registry-driven "same option on other surfaces" columns in the generated
   CLI/Action/config references) and for the final step: flip ADR-091 to Accepted/implemented in
   the ADR, docs/contribute/adr/index.md and adr-surface-registry.yaml, and close the plan.
   Give each child the full rules: read ADR/plan/registry, regenerate inventory, run
   check_ai_readiness/check_architecture/check_docs_contract/check_adr_surfaces/
   gen_option_vocabulary --check + touched tests, PR via the repo's `pr` skill, drive CI green,
   do NOT merge, report to "@parent".
3. Keep a send_later check-in (~45 min) while work is in flight; check child status with
   get_session/list_events and CI with
   curl -s https://api.github.com/repos/abicheck/abicheck/commits/<sha>/check-runs?per_page=100.
4. Never skip/disable/weaken tests; deleting tests needs a MECHANISM-vs-BEHAVIOUR table.
   Ask the maintainer only for real product decisions, with options and a recommendation.
Report progress to the maintainer in Russian.
```

## How to resume
- New Claude session on this repo: read this file, the ADR, the plan, then `get_session` / `list_events` on the two child sessions above (or check their branches/PRs on GitHub).
- CI status helper: `curl -s https://api.github.com/repos/abicheck/abicheck/commits/<sha>/check-runs?per_page=100`.
