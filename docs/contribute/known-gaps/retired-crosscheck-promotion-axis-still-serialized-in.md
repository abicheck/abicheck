---
doc_type: contributor
audience:
  - contributor
level: advanced
lifecycle: active
generated: false
---

# Retired `--crosscheck` promotion axis still serialized in ExitDecision and read by action/run.sh

Index: [Known gaps](../known-gaps.md)

**Status (re-verified 2026-10-09 at `456989f`): PARTIAL.** The engine half is gone: scan_engine.py/cli_scan*.py are deleted, policy/exit_decision.py:147-154 marks PROMOTED_CROSSCHECK a read-only retired producer, and resolve_exit_decision no longer takes it. Residue: ExitDecision still serializes crosscheck_promotion_contribution (exit_decision.py:327,442) and action/run.sh still reads promoted_crosscheck (re-verified 2026-10-25).


ADR-068's second 2026-09-09 amendment rules `--crosscheck KEY=error`'s
promotion syntax **(b) — dropped, superseded**: every cross-source check
already reaches `compare` as an ordinary `ChangeKind`, so the underlying
capability (controlling one check's severity) exists as
`policy.overrides.<CHANGE_KIND>: error` in `--policy`/`.abicheck.yml`.

Phase 4's typed-API slice (2026-09-09) deleted the half it owned:
`ScanRequest.severities`/`enabled_checks` are gone with the request type, and
no typed caller can state either any more. **The `scan` CLI flag and its
engine plumbing are still there**, deliberately. Its promotion is not a
`scan`-local detail — it is ADR-064's `crosscheck_promotion_contribution`, a
*published* `ExitDecision` axis:

- `policy/exit_decision.py` carries the field and `resolve_exit_decision`'s
  parameter; it is emitted in every persisted `exit` block.
- `policy/exit_decision_precedence.py` carries it through a prior decision.
- `workflows/scan_abort_result.py` names it as a fold participant for a
  budget/evidence-contract abort.
- `schemas/__init__.py`'s `REPORT_SCHEMA_VERSION` 2.42 entry documents it on
  `compare`'s side of the report contract, and `action/run.sh` reads
  `promoted_crosscheck` out of `blocking_categories` to word its own verdict
  escalation.
- `scan_engine._crosscheck_severity_exit`/`_promote_published_gate` and
  `cli_scan_baseline`'s `info`/`warning` non-gating exclusion implement it.

Removing a published exit axis changes `compare`'s report schema as well as
`scan`'s and needs an ADR-064 amendment deciding what a consumer reading
`crosscheck_promotion_contribution` should see afterwards — a different
change from retiring a request field. It is not done in the typed-API slice,
and the slice does not pretend otherwise.

**What closing it looks like:** delete `--crosscheck` from `cli_scan.py`
(both halves — the `KEY=off` enable/disable side has no separate ruling, but
it is the same flag and `compare` runs every check unconditionally per plan
§3 #3), delete `_parse_crosschecks`/`_CROSSCHECK_LEVELS`/
`_PROMOTABLE_FINDING_KINDS`, drop `severities`/`enabled_checks` from
`run_scan_core`/`_run_baseline_compare`, delete
`_crosscheck_severity_exit`/`_promote_published_gate`/
`CROSSCHECK_BLOCKING_CATEGORY`, retire the `ExitDecision` field under an
ADR-064 amendment with its own `REPORT_SCHEMA_VERSION`/`SCAN_SCHEMA_VERSION`
entries, and update `action/run.sh`'s two `promoted_crosscheck` readers plus
`pr_comment_scan.py`'s `crosscheck_severities` promotion read.
