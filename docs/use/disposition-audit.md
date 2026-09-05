---
doc_type: explanation
audience:
  - library-maintainer
  - ci-owner
level: intermediate
canonical_for:
  - disposition-audit
summarizes:
  - suppressions
  - output-formats
lifecycle: active
generated: false
---

# The Disposition Audit

Every `abicheck compare` run reports two totals, not one:

- **detected** — every change the detectors observed, before any policy ran;
- **effective** — the changes that actually contribute to this run's gate.

Between them sits a per-change *disposition*: the single reason each detected
change is, or is not, in the effective set. The disposition audit is the block
that reconciles the two, so a run that reports "no changes" can still tell you
that 100 removals were detected and every one of them was withheld by a rule
you wrote.

## Why the two totals are separate

A compatibility check has one selector grammar for suppression but several
places where a finding can stop counting: an explicit suppression rule,
deduplication of two views of the same change, a contract that excludes the
finding's surface, evidence that ran out before relevance could be decided, or
a severity setting that scores the finding as a warning. Reporting only the
survivors makes all of those look identical to "nothing happened".

The rule the audit makes checkable is that **policy never changes what was
observed** — it only moves a finding between dispositions. Adding a
suppression rule to a run must leave the detected total exactly where it was.

## The six dispositions

Each detected change ends in exactly one of these, and the counts sum to the
detected total:

| Disposition | What it means |
|---|---|
| `gating` | Contributes to this run's exit code. |
| `non_gating` | Evaluated, and scored as not gating — a compatible addition, or a category your severity settings rate below `error`. |
| `suppressed` | A `--suppress` rule matched it. The audit names the rule. |
| `out_of_contract` | Proven outside the contract the run was told to judge against (`--contract`). |
| `unresolved_relevance` | Relevance could not be decided, because the evidence for deciding it was absent. Not the same as "irrelevant". |
| `deduplicated` | A second view of a change already counted once — a grouped child, a collapsed versioned symbol, a redundant finding. |

`out_of_contract` and `unresolved_relevance` are deliberately distinct: the
first is a positive determination, the second is evidence running out. See
[Contract Evaluation](contract-evaluation.md).

## Where it appears

The one-line summary carries the counts inline:

```console
$ abicheck compare old.so new.so --suppress suppressions.yaml --profile quick
NO_CHANGE: no changes (0 total) [audit: 100 detected, 0 gating, 100 suppressed]
```

The JSON report (`--format json`, and the `--profile quick` JSON summary)
carries a `disposition_audit` block with the same counts plus the rules that produced
them; the review digest renders it as a table; the sticky PR comment carries a
counts row and rule attribution; SARIF, JUnit and HTML each carry the counts in
their own format's idiom. A view may collapse detail — the not-evaluated
detector list, the rule tail — but none of them drops the counts. See
[Output Formats](output-formats.md).

## Detectors that did not run

A detector whose supporting evidence was absent is reported as
`not_evaluated`, not as a detector that ran and found nothing. "No DWARF on
either side, so no layout comparison happened" and "layouts were compared and
are identical" are different statements about a run, and the audit keeps them
apart. `not_evaluated` is a reporting distinction only: it never changes a
verdict, an exit code, or the run's reported confidence.

## What it means for suppression

A suppression records that a finding was *withheld*; it is not evidence the
finding was wrong. Two consequences follow, both visible in the audit:

- the withheld finding stays in the detected total and names the rule that hid
  it, including the suppression document's path, `reason`, `label` and
  `expires`;
- the release recommendation reads the same conserved ledger, so a suppressed
  ABI/API break is reported as a `major`-class finding needing review rather
  than "no version bump required".

Full rule syntax and the audit trail a rule leaves behind:
[Suppressions](suppressions.md).
