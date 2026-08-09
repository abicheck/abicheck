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
evidence_tier       elf_only | dwarf_aware | header_aware  — always present
evidence_tiers      raw sources available                  — always present
coverage_warnings   where coverage fell short — present only when non-empty
scope.resolved      false → fell back to the full export table
contract_coverage   "partial" → only one side had a fingerprint
```

`evidence_tier` is the one to key trust decisions off. There is no
"depth actually achieved" field to read on this path: `requested_depth` and
`effective_depth` exist in the report schema but are populated only by the
GitHub Action's `check-target` envelope, never by a direct `abicheck compare`.
You do not need them — `compare` **fails the run** when an explicitly
requested `--depth` cannot be reached, so a report that exists at all already
reached the depth you asked for. Verify the tier, not a depth echo.

## 3. The verdict, and the gate when one was configured

```
verdict             NO_CHANGE | COMPATIBLE | COMPATIBLE_WITH_RISK | API_BREAK | BREAKING
                    — always present; this is the compatibility answer
severity.exit_code  the exit code the gate computed
severity.blocking   whether anything blocks
severity.blocking_categories / categories / config
```

The `severity` block appears whenever severity-aware grading was **resolved**
— which is not only from a `--severity-*` flag. A `.abicheck.yml` carrying a
`severity:` map or `exit_code_scheme: severity`, a selected run profile, or a
gate pack all activate it just as well, so a repository can have a gate you
did not ask for on the command line. Verified: a `compare` run with no
severity flag at all still emits the block when the project config supplies
one.

So do not infer "there is no gate" from the absence of a flag in the command
you typed. Read the block: present means a gate graded this run; absent means
none was resolved from any source, the exit code follows the legacy verdict
mapping, and `verdict` is the entire answer.

`verdict` is the compatibility answer; `severity.*` is the *grading* of it
under this project's configuration. They can legitimately disagree; report
both when both exist
([policies-and-suppressions.md](policies-and-suppressions.md)).

`compatibility_verdict` and `policy_gate_decision` are in the schema but are
**integration-only** — the GitHub Action's `check-target` envelope emits them,
a direct `compare` never does. Do not look for them in a report you produced
yourself, and do not read their absence as an old schema version.

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

## Absent is not empty

A field missing from the document means **this run did not produce it**, not
that its value is empty. Several blocks are conditional on how the run was
invoked:

| Block | Present when |
|---|---|
| `severity` | severity-aware grading was resolved from *any* source — a `--severity-*` flag, a `.abicheck.yml` `severity:` map or `exit_code_scheme: severity`, a run profile, or a gate pack |
| `scope` | `--scope-public-headers` was requested |
| `contract_coverage_failures`, `contract_coverage_exit_contribution`, `contract_context` | `--contract-evaluation` was passed |
| `root_causes`, `root_cause_count` | `--report-mode root-cause` |
| `reason` | the comparison was refused (`verdict: null`) |
| `coverage_warnings` | the run actually had coverage gaps — a clean run omits the key entirely, which is good news, not a malformed report |
| `requested_depth`, `effective_depth`, `compatibility_verdict`, `policy_gate_decision` | never on a direct `compare` — Action `check-target` envelope only |

If you need one of the conditional blocks, re-run with the flag that produces
it rather than reporting the question as unanswerable — and never treat an
absent block as a clean one.

`report_schema_version` and `tool_version` identify the contract the document
was written against. Check them only after ruling out the table above; an
older abicheck predating a field is the rarer explanation.
