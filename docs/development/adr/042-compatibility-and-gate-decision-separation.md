# ADR-042: Formal separation of CompatibilityDecision and GateDecision

## Status

Accepted — implemented for the JSON/SARIF/`compare-release` gate summaries
(`abicheck.severity.GateDecision`/`compute_gate_decision`). Other renderers
(HTML's CI Gate card, the MCP server, JUnit) still compute an exit code
inline via `compute_exit_code` — see "Rollout" below.

## Context

A post-#549/#551 reporting review found the same pattern recurring:
independent renderers computed a report's "does this block CI?" answer from
one code path and its "which category is actually responsible?" answer from
a *different* code path, and the two could disagree:

- JSON's `severity.blocking_categories` was derived from the (possibly
  `--show-only`-filtered) *display* change set, while `severity.exit_code`
  was correctly derived from the unfiltered gate set — hiding the one
  category actually responsible for a nonzero exit code reported
  `blocking: true` next to `blocking_categories: []`.
- `compare-release --format json`'s per-library `findings` list only ever
  walked the three legacy verdict buckets (breaking/api_break/risk), so a
  library gated by `--severity-addition error` reported a nonzero
  `severity.exit_code` with an empty `findings` list.

Both bugs had the same root cause: "is this compatible?" and "does this
block CI?" are two different questions, and the codebase had no single type
for the second one — every caller re-derived it by categorizing changes and
checking `severity_config.level_for(category) == ERROR` inline, with enough
copies (`reporter._build_severity_json`, `sarif._severity_gate_properties`,
`cli_compare_release._release_gating_buckets`) that they drifted apart.

This is the same shape of problem ADR-036 solved for the *verdict axis*
(`ReportModel`, `DiffResult._effective_verdict_for_change`) — but ADR-036's
"canonical report severity = the verdict axis" is exactly the assumption
this review found broken: once `SeverityConfig` is active, "blocks CI" is
no longer a function of the verdict axis alone (an addition, verdict
`COMPATIBLE`, can block; a breaking kind, verdict `BREAKING`, can pass under
a demoted preset). ADR-036 remains correct for the *display/bucketing*
question it addresses; this ADR is scoped to the *gate* question layered on
top of it once severity configuration is in play.

## Decision

1. **`CompatibilityDecision` is a name, not a new type.** It is a plain
   alias for the existing `Verdict` enum
   (`abicheck.severity.CompatibilityDecision = Verdict`). `Verdict` already
   answers exactly "is this ABI/API compatible?" and nothing else — ADR-036
   already established it as the canonical axis for that question.
   Introducing a second enum with the same five members would just be
   another thing to keep in sync; the alias exists purely so call sites that
   want to say "compatibility decision" explicitly can, without touching any
   existing `Verdict` usage anywhere in the codebase (zero behavior change).

2. **`GateDecision` is new** (`abicheck.severity.GateDecision`, a frozen
   dataclass): `scheme` (`"legacy"` | `"severity"`), `exit_code`, `blocking`
   (`exit_code != 0`), `blocking_categories` (the `IssueCategory` names
   actually responsible — always empty under `"legacy"`, which has no
   per-category configuration to single one out).

3. **One computation function, `compute_gate_decision`**, replaces every
   hand-rolled "categorize, then filter to `level == ERROR`" call site.
   `exit_code` (via the existing `compute_exit_code`) and
   `blocking_categories` (via the existing `categorize_changes`) are derived
   from the *same* `changes`/`kind_sets`/`policy_file` arguments in one call,
   so they cannot independently drift the way two separate call sites could.
   `reporter._build_severity_json`, `sarif._severity_gate_properties`, and
   `cli_compare_release._release_gating_buckets` all now call it instead of
   reimplementing the categorize-and-filter loop.

4. **Renderers should read gate status from `GateDecision`, never infer it
   from `CompatibilityDecision`/`Verdict` wording** — a `COMPATIBLE` verdict
   does not imply `blocking=False` once severity configuration promotes an
   addition to `error`, and a `BREAKING` verdict does not imply
   `blocking=True` under a demoted preset. This is the same discipline
   ADR-036 established for the *display* axis, extended to the *gate* axis.

5. **No public-API break.** `Verdict`, `DiffResult`, `compute_exit_code`,
   and `categorize_changes` are all unchanged and still directly usable —
   `GateDecision`/`compute_gate_decision` are additive, and `reporter.py`'s
   `d["severity"]` / `sarif.py`'s `severityGate` JSON/SARIF shapes are
   byte-for-byte unchanged (schema stays 2.3; this is an internal
   implementation refactor, not a schema bump).

## Consequences

- The class of bug that motivated this ADR (gate exit code and blocking
  category list computed from different inputs) is now structurally
  prevented at the three sites that were actually affected, rather than
  fixed one-off each time a new renderer reimplements the pattern.
- `html_report.py`'s CI Gate card, `mcp_server.py`'s exit-code computation,
  and `junit_report.py`'s per-change `_is_failure` still call
  `compute_exit_code`/`classify_effective_change` directly rather than
  `compute_gate_decision` — they only ever needed the exit code (or a
  per-change category, not a whole-report `blocking_categories` list), so
  there was no duplicated-computation bug to fix there. They are candidates
  to adopt `GateDecision` for API consistency in a future pass, not because
  they are currently wrong.
- `compare-release`'s per-library `findings` projection
  (`_release_gating_buckets`) needs the actual `Change` objects per blocking
  category, not just `GateDecision`'s category names, so it calls
  `categorize_changes` a second time to look up the change lists for the
  categories `compute_gate_decision` names as blocking. This is a
  deliberate, small duplication traded for keeping `GateDecision` itself
  lean (names and counts, not full object references) — see
  `cli_compare_release._release_gating_buckets`.

## Rollout

Not a phased rollout in the ADR-036 sense (no golden-snapshot risk — the
JSON/SARIF/release-JSON shapes are unchanged). Remaining candidates to adopt
`GateDecision` are opportunistic follow-ups, not required work:

- `html_report.py`'s CI Gate card could expose `GateDecision.blocking`
  instead of comparing `exit_code != 0` inline.
- `mcp_server.py`'s severity-aware exit-code computation could return a
  `GateDecision` instead of a bare int, if a future MCP tool surface wants
  `blocking_categories` too.
