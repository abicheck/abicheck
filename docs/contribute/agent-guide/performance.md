# Agent guide: performance investigation

> Moved verbatim out of the root `AGENTS.md` (progressive disclosure: the root file is loaded into every agent session, this one only when its pointer fires). The root `AGENTS.md` remains the primary contract.

## Performance investigation — what runs where, and the manual tools

Performance is guarded by deterministic gates first and timing second (full
table: `docs/contribute/performance.md` § "Complexity and cost gates beyond
wall-clock time"). **CI already runs all of these; you run them by hand when
you touch a hot path, when a gate fails, or when a user reports "slow".**

| Gate | Runs in | Run it yourself |
|---|---|---|
| Call-count complexity (`compare()`, serialization, reports, release by member count, history by release count) | unit lane | `pytest tests/test_compare_call_complexity.py tests/test_pipeline_call_complexity.py tests/test_history_scaling.py -q -n 4` |
| Cost budgets: same-argument repeats of expensive functions, child processes per `compare()` (`tests/perf_call_budgets.json`) | unit lane | `pytest tests/test_compare_cost_budgets.py -q -n 4` |
| Whole-package repeat ratchet: same-argument repeats of *every* first-party function per `compare()`, per function and in total (`tests/perf_repeat_baseline.json`) | unit lane | `pytest tests/test_compare_repeat_audit.py -q` |
| Empirical time exponents of hot paths (compare by symbols/types, add/remove, rename matching, policy classification, history) against per-case ceilings | PR lane (`complexity-bench`) | `python scripts/complexity_bench.py --strict` (`--case`, `--json`) |
| `perf-antipatterns` lint (list membership / `re.compile` / `json.loads` / `deepcopy` / `pickle.loads` / `model_copy(deep=True)` / self-copying accumulation / quadratic self-dedup / `subprocess` / blocking network I/O or `time.sleep` inside loops) | `ai-readiness` | `python scripts/perf_antipatterns.py` (`--all` lists every site) |
| Wall-clock exponents per workload shape, history over 50 releases | `slow` | `pytest tests/test_compare_scaling_shapes.py tests/test_history_scaling.py -m slow -q` |

Manual-only investigation tools (never gates):

- **Missed memoization:** `python scripts/audit_repeated_calls.py --mode <mode> --n 200 --top 30`, once per mode (`default`, `contract`, `patterns_and_metrics`)
  lists functions called repeatedly with the *same* arguments inside one
  `compare()`. Read it with judgement: a cheap string helper hit 400 times on
  `"int"` is the synthetic workload's repetitiveness, not waste; a
  whole-snapshot builder repeated on the same snapshot object is.
- **Backlog snapshot:** `python scripts/perf_report.py --corpus 30 -o perf-report.md` (hot functions, costed repeats via `audit_repeated_calls.py --by-cost`, calls per declaration, lint counts; runs weekly in `performance.yml`). Check and update `docs/contribute/perf-findings.md` before and after chasing a candidate. Real-library gate: `pytest tests/test_extract_call_complexity.py -m integration -q` (needs g++/castxml).
- **Which call counts grow:** `profile_call_counts` + `superlinear_call_sites`
  (`tests/_call_counts.py`) at two sizes. Salt each run's names (the
  workloads' `tag` argument) — demangling and canonical-spelling caches are
  process-wide, so an unsalted second run measures cache state.
- **Where time goes:** `python -m cProfile -o out.prof -m abicheck compare OLD NEW`
  then `python -m pstats out.prof`; `py-spy record -o flame.svg -- abicheck compare OLD NEW`
  for a flame graph (installed ad hoc, not a dependency). Memory:
  `docs/contribute/memory.md` / `ABICHECK_MEMORY_TRACE`.

Use-case reach (weekly `usecase-paths.yml`, never on a PR):
`python scripts/usecase_paths.py ratchet REC --strict` fails when a function
no scenario reached before is still unreached in the baseline's eyes -- i.e.
newly unreached (`--write` re-records `scripts/usecase_unreached_baseline.json`
from a `record --source scenarios` run); `usecase_paths.py adr-reach REC`
classifies each ADR's named `abicheck/` files as reached / reached-elsewhere
/ not-reached / untraced / no-code-refs.

When a gate fails:

- **Superlinear call site** — fix the algorithm (build the index once, hoist
  the set); never widen `exponent_ceiling` or shrink the workload to pass.
  A workload whose entity population stops growing with `n` hides exactly
  this (a fixed type count once made a types x findings scan look linear).
- **Budget above** — remove the repeat, usually with a memo under an
  existing scope with a sound lifetime (`compare.detection_memo` keys on
  snapshot identity and dies with the pass). **Budget below** — an
  improvement: re-record with `python scripts/audit_repeated_calls.py --write-budgets`
  (the whole-package ratchet: `--write-repeat-baseline`).
  Raising a budget needs its reason in the PR.
- **Exponent above its ceiling** (`complexity-bench`) -- rerun the one case
  (`--case NAME`) to rule out runner noise, then profile it at its largest
  size; fix the algorithm, never raise `max_exponent` to pass.
- **New anti-pattern site** — fix it, or, if the shape is the work itself
  (or the collection provably stays tiny), exempt it in place with a
  `# perf-ok: <reason>` comment. Don't grow the (empty) baseline. Moving a memoized helper into a nested function
  also re-keys other per-function registries (`tests/_family_f4_registry.py`),
  so run `verify.py --only repo-scan-tests` after such a refactor.

