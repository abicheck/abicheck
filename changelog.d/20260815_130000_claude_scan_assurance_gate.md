<!-- P0.4 analysis-assurance parity for `scan --against`, plus the composite
     Action's own gap for both commands. Found by a fresh main->lab
     end-to-end audit. -->

### Fixed

- **`scan --against` now supports `--require-complete-analysis` (P0.4).**
  `compare` has gated on `analysis_assurance.status` since P0.4 landed;
  `scan --against` computed the identical `analysis_assurance` result on
  every run (`checker.compare` always attaches it) but never accepted the
  flag, never folded its exit-code floor, and never emitted the block in
  its own JSON summary at all — a run whose evidence was genuinely
  incomplete gated on `compare` and passed on `scan` for the identical
  pair. `scan --against --require-complete-analysis` now floors the exit
  code to 1 the same `max`-folded way `compare`'s own flag does (never
  lowering a real 2/4/5/6), and `scan --format json`'s summary always
  carries `analysis_assurance` under `diff`, regardless of the flag —
  mirroring `compare`'s report shape at the root. Rejected as a usage
  error without `--against`, alongside the rest of the baseline-only
  flags (`--policy`, `--severity-preset`, ...).

- **The composite Action now recognizes P0.4's analysis-assurance exit
  and gates on it unconditionally, via a new dedicated
  `require-complete-analysis` input.** `--require-complete-analysis` (on
  either `compare` or `scan --against`) floors the CLI exit code to 1 on
  incomplete evidence, but `action/run.sh`'s exit-1 disambiguation had no
  notion of this axis: it fell through to `SEVERITY_ERROR` on `compare` (a
  severity-policy failure it is not) or the catch-all `ERROR` on `scan`
  (an operational failure it is not), which meant a genuinely
  assurance-gated run could read as something else in the job summary —
  and, on `scan`, was never actually documented as failing the step for
  the right reason. `run.sh` now maps this to a new `ANALYSIS_INCOMPLETE`
  verdict (mirroring ADR-049's own `COVERAGE_INCOMPLETE` orthogonal-axis
  treatment: never rewrites the compatibility verdict, unconditional — no
  `fail-on-*` flag disables it), on both commands, including when it
  coincides with a severity or contract-coverage gate on the same exit 1.
  Gating on this axis is a plain boolean read of the new
  `require-complete-analysis` input — not a scan of `extra-args` or the
  constructed CLI invocation — closing out three rounds of Codex-found
  false positives from inferring the flag's presence from other signals
  (an unanchored stderr grep; a `$CMD`-array token scan colliding with
  another input's own value; an `extra-args`-token scan colliding with an
  adjacent option's own value) with a design that structurally cannot
  reproduce any of them. Two more Codex findings on the new input itself:
  the "Run abicheck" step's own `env:` block never mapped the declared
  `require-complete-analysis` input to `INPUT_REQUIRE_COMPLETE_ANALYSIS`
  at all, so a real Action invocation left it unset and every check in
  `run.sh` read it as `false` regardless of what the workflow requested
  (the existing tests missed this because they inject the env var
  directly rather than going through `action.yml`'s own wiring — closed
  by the pre-existing, now-passing `test_every_action_input_is_wired_to_
  run_sh` contract test, not a new one-off check); and a directory/package
  `compare` operand with the flag explicitly set used to silently drop it
  rather than reject the unsupported combination, running a release
  compare ungated despite the caller's explicit request — `run.sh` now
  fails the step loudly for that shape instead, the same treatment its
  own L2 compile-context and evidence-flag guards already give their own
  release-incompatible inputs. The release-operand rejection is also now
  mirrored in `action/validate-inputs.sh` (fail-fast, before dependency
  install), and wiring it there surfaced two *pre-existing*, unrelated
  gaps of the identical shape in that earlier step's own `env:` block:
  `ast-frontend`/`gcc-path`/`gcc-prefix`/`gcc-options`/`sysroot`/
  `nostdinc` (its own directory/package compile-context guard) and
  `build-info`/`compile-db` (its scan build-info/compile-db conflict
  guard) were never mapped into that step's env either, so both checks
  always read their inputs as unset/false there and only ever fired via
  the identical, correctly-wired check re-running later in `run.sh` — now
  fixed alongside, with a new generalized contract test
  (`test_every_validate_inputs_var_is_set_by_its_own_step`, mirroring the
  existing `run.sh`-scoped ones) so this class of gap can't reopen
  unnoticed for a future input either. `docs/reference/exit-codes.md`'s
  own explanation of the Action-level mapping is corrected to name the
  dedicated input instead of the now-superseded `extra-args` route, and
  the `analysis-assurance` topic in `docs/_meta/topics.yaml` is extended
  with `action.yml` as a `fact_sources` entry and the generated
  `reference/github-action-inputs.md` as a `task_pages` entry, so the new
  input's documentation ownership is registered per `docs/AGENTS.md`'s
  "every new Action input" rule.

- **`abicheck aggregate` now folds P0.4's analysis-assurance axis
  (aggregate schema 1.5).** `scan --against --require-complete-analysis`
  computed and folded its own exit-code floor, but never persisted the
  contribution into the report itself — so a target whose severity gate
  read a clean `0` while the analysis-assurance axis independently floored
  its *real* exit to `1` fed `aggregate` a green result for that target,
  since `GateInfo.from_scan_report`/`from_report_data` read only the
  nested compatibility gate (Codex review). `scan`'s JSON summary now
  always carries `analysis_assurance_exit_contribution` (schema 1.17,
  under `diff`), the exact sibling of the pre-existing
  `contract_coverage_exit_contribution`, and `aggregate` reads and folds
  it the identical way: a new `analysis_assurance_exit`/
  `analysis_assurance_targets` axis on `TargetReport`/`AggregateResult`,
  folded into `exit_code()` with `max` (never lowers a real break), a
  top-level `analysis_assurance` summary block (`aggregate_report.schema.
  json` and its published mirror), a `render_text()` block, and
  `buildsource.check_report._neutralize_gate` zeroing it for
  `gate-mode: advisory` alongside the coverage axis it mirrors.

- **A typed `ScanRequest.depth` pin of the internal-only `full`/`graph`
  rungs could report a false `"complete"` analysis-assurance status
  (Codex review, fresh evidence).** `scan_engine.py` stamped
  `DiffResult.requested_depth` from the raw resolved `EvidenceDepth`
  value, but `analysis_assurance.py`'s public four-rung ladder
  (`binary`/`headers`/`build`/`source`) doesn't recognize `full`/`graph`
  (internal replay-scope variants of `source`, reachable only via a typed
  `ScanRequest.depth` pin or `source_method` `s6`/`s4`, never via the CLI's
  own `--depth`) — its `_DEPTH_RANK.get(value, 0)` read either as rank 0,
  the *shallowest* rung, so `depth_satisfied`/`status` could read as
  satisfied/`"complete"` for a request the ladder never actually
  evaluated. Fixed with a new `scan_levels.public_depth_value()` helper
  (both rungs normalize to `source`, per `EvidenceDepth`'s own docstring)
  applied before the stamp, plus a primitive-level property test covering
  every `EvidenceDepth` member.

- **`compare`'s own JSON report now persists `analysis_assurance_exit_
  contribution` too (report schema 2.40), closing the identical gap on
  the other front end (Codex review).** The `aggregate` fix above closed
  this for `scan --against`; `compare --require-complete-analysis` had
  the same hole — it computed and folded its own exit-code floor but
  never wrote the contribution into the report `reporter.py` builds,
  since that report is generated before the CLI's own post-hoc exit-code
  fold ever runs. A `compare` report whose severity gate read a clean `0`
  while this axis independently floored the *real* exit to `1` fed
  `abicheck aggregate` a green result the same way the scan gap did.
  Fixed by threading `require_complete_analysis` down through
  `reporter.to_json()`'s three JSON paths (full/leaf/root-cause) into
  `reporter_contract_blocks.add_contract_context()`, which now persists
  the contribution alongside `analysis_assurance` itself (present under
  the identical condition — a real `AnalysisAssurance` object attached,
  which every genuine `compare()` call provides; absent for a hand-built
  `DiffResult`, preserving back-compat for a caller reading this report's
  exact key set) — the same conditional shape the `scan`-side fix above
  needed to adopt after an existing regression test caught the
  unconditional version breaking it. `compare_report.schema.json` (and
  its published mirror) documents the new key as the exact sibling of
  `contract_coverage_exit_contribution`.
