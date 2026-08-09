---
doc_type: reference
level: advanced
lifecycle: active
summarizes:
  - output-formats
---

# Reading an abicheck JSON report

Always drive `--format json` for analysis and reserve the rendered formats
for what the user sees. The available formats and when each is appropriate
are owned by [the output formats page](../../docs/use/output-formats.md).

Read the blocks in this order. Stopping early is how a false green happens.

## 1. Did the comparison happen at all?

```
verdict            null  → not comparable; read `reason` and stop
reason.kind        "profile_mismatch" | "scope_mismatch"
reason.message     the specific mismatch, in prose
```

`verdict: null` is not a pass. See
[baseline-and-comparability.md](baseline-and-comparability.md) and
[safety-invariants.md](safety-invariants.md) item 2.

## 2. What evidence backed it?

```
requested_depth / effective_depth   asked-for vs. achieved
evidence_tier                       elf_only | dwarf_aware | header_aware
evidence_tiers                      raw sources available
coverage_warnings                   where coverage fell short
layer_coverage                      per-layer coverage detail
scope.resolved                      false → fell back to the full export table
contract_coverage                   "partial" → only one side had a fingerprint
```

## 3. The verdict and the gate — two different things

```
verdict                 NO_CHANGE | COMPATIBLE | COMPATIBLE_WITH_RISK | API_BREAK | BREAKING
compatibility_verdict   the compatibility-axis verdict
severity.exit_code      the process exit code the gate computed
severity.blocking       whether anything blocks
policy_gate_decision    "pass" | "fail" — this check's own gate outcome
```

`verdict` is the compatibility answer. `policy_gate_decision` /
`severity.*` are the *grading* of that answer under this project's
configuration. They can legitimately disagree; report both when they do
([policies-and-suppressions.md](policies-and-suppressions.md)).

## 4. Contract coverage — the orthogonal axis

Present under `--contract-evaluation`:

```
contract_coverage_failures              unsuppressible coverage failures
contract_coverage_exit_contribution     0 or 1, folded into the exit code with max
contract_context                        the persisted evaluation context
```

A `1` here raises a clean `0` to `1` and never lowers a `2`/`4`. Never
compress it out of a summary
([safety-invariants.md](safety-invariants.md) item 5).

## 5. The findings

```
summary.total_changes / breaking / source_breaks / risk_changes / compatible_additions
summary.binary_compatibility_pct, summary.affected_pct
changes[]  one finding each
```

Per finding, the fields that carry the reasoning:

| Field | Meaning |
|---|---|
| `kind` | the `ChangeKind` — the fact owner for what this finding *is* |
| `severity` | its graded severity |
| `symbol`, `source_location` | where |
| `description`, `impact` | what and why it matters |
| `old_value`, `new_value` | the concrete delta |
| `evidence_status` | what evidence backed this specific finding |
| `caused_by_type`, `caused_count` | root-cause grouping key |
| `contract_relevance`, `contract_reason_code`, `contract_assurance` | contract-relevance decision (under `--contract-evaluation`) |
| `compatibility_decision` | `null` means policy did not score it — not a sixth verdict |
| `gate_contribution` | what it contributed to the gate |
| `recommended_action`, `reviewer_action` | suggested next step |

The exhaustive change-kind catalogue is
[the change kinds reference](../../docs/reference/change-kinds.md).

## 6. Grouping before summarizing

Do not read `changes` linearly for anything larger than a handful of
findings — use `--report-mode root-cause` and read `root_causes` /
`root_cause_count` instead. See
[root-cause-grouping.md](root-cause-grouping.md).

## Report identity

`report_schema_version` and `tool_version` identify the contract the document
was written against. If a field this workflow expects is absent, check these
before assuming the field is empty — an older abicheck may simply predate it.
