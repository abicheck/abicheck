# ADR-078: An Informational Severity Category That Never Gates

**Date:** 2026-10-12
**Status:** Accepted — implemented. Amends
[ADR-009](009-verdict-system-and-exit-codes.md) (severity-driven exit 1) and
the four-category `SeverityConfig` model it references; applies
[ADR-067](067-change-intent-acknowledgment-and-disposition-audit.md)'s
record-before-disposing rule to the severity partition.

## Context

`policy/severity.py` sorts every finding into one of four categories:
`abi_breaking`, `potential_breaking`, `quality_issues` and `addition`. A
`SeverityConfig` gives each one a level (`error`/`warning`/`info`), and a
category set to `error` contributes to the exit code.

`quality_issues` was defined as a residue: `COMPATIBLE_KINDS −
ADDITION_KINDS`. That residue held two groups that mean opposite things:

- real quality problems: a `std` symbol leak, a missing SONAME, toolchain
  flag drift;
- findings that record that **nothing a consumer can act on changed**:
  - `declaration_identity_unchanged` and `declaration_coordinates_shifted`.
    The L5 graph reconciliation emits these to prove that a declaration
    did not change. They explain what would otherwise look like an
    unrelated remove-then-add pair.
  - `public_surface_grew` and `public_surface_shrank`. These are aggregate
    roll-ups. Each addition and removal they count is already reported and
    gated on its own.

`PRESET_STRICT` sets every category to `error`. So a run whose only finding
was "the reconciliation proves this declaration is identical" exited `1`.
Before this change:

```
declaration_coordinates_shifted   strict → exit 1, blocking_categories=[quality_issues]
declaration_identity_unchanged    strict → exit 1, blocking_categories=[quality_issues]
func_added + public_surface_grew  {quality_issues: error, addition: info} → exit 1
```

The third line is the same defect with a different trigger. The user set
additions to `info`, but the roll-up of those additions failed the build
anyway, through the quality bucket. The PR comment then labelled the run
"⛔ Quality policy violation".

Known gap:
[`known-gaps/an-informational-no-material-change-reconciliation-outcome.md`](../known-gaps/an-informational-no-material-change-reconciliation-outcome.md).

## Decision

### D1. Informational is a catalog fact

`ChangeKindMeta` gains a keyword-only field, `is_informational: bool = False`.
`ChangeKindRegistry.informational_kinds()` and
`policy.classification.INFORMATIONAL_KINDS` are derived from it, the same
way `is_addition` gives `ADDITION_KINDS`. There is no hand-kept list in the
gate.

`_validate_entry` accepts `is_informational=True` only when all three hold:

- `default_verdict == COMPATIBLE`;
- `is_addition` is `False`;
- there are no `policy_overrides`.

A kind that is not COMPATIBLE, is an addition, or that a built-in policy
re-verdicts is a claim that something changed. It is not a record that
nothing did.

`COMPATIBLE_KINDS` is now partitioned into three disjoint sets:
`ADDITION_KINDS | INFORMATIONAL_KINDS | QUALITY_KINDS`. Three things assert
this partition:

- `classification.py`, at import time;
- the `changekind-partition` AI-readiness check;
- the regression tests.

### D2. Membership criterion

A kind is informational if and only if its finding records that nothing a
consumer of the compared library can act on changed. Two shapes qualify:

1. **A sameness proof.** A reconciliation that matched an old and a new
   entity and found no identity dimension that differs:
   `declaration_identity_unchanged`, `declaration_coordinates_shifted`.
2. **A pure roll-up.** An aggregate whose every subject is reported, and
   gated, as its own finding: `public_surface_grew`,
   `public_surface_shrank`.

The following kinds stay in `quality_issues` on purpose. Each records an
observed change to the binary, its metadata or its contract, and under
`strict` "gate on any change" is the stated intent:

- hardening improvements (`*_protection_improved`,
  `executable_stack_removed`, …);
- dependency or metadata drift (`needed_removed`, `build_context_changed`,
  `sycl_runtime_version_changed`);
- `typedef_version_sentinel`, because a typedef name did change;
- `abi_surface_explosion`, a packaging signal in its own right;
- `glibcxx_dual_abi_flip_detected`, a diagnosis of a build-configuration
  change.

A new kind joins `INFORMATIONAL_KINDS` only by meeting D2's criterion.

### D3. `IssueCategory.INFORMATIONAL`: fixed `info`, never gates

`IssueCategory` gains `INFORMATIONAL = "informational"`.

- `SeverityConfig` gets **no** field for it, so no preset, `.abicheck.yml`
  `severity:` key or typed-API override can raise it.
  `severity: {informational: …}` is rejected as an unknown key (exit 64).
- `level_for(INFORMATIONAL)` always returns `INFO`, and its exit-code
  contribution is `0`.
- `CONFIGURABLE_CATEGORIES` names the four configurable categories, for
  callers that list what a config sets.

`classify_effective_change` returns `INFORMATIONAL` for a COMPATIBLE verdict
on an informational kind, whatever rule produced that verdict. An override
that raises the kind to RISK, API_BREAK or BREAKING is still honoured: the
verdict is checked first. Policy can still promote such a finding. What
policy cannot do is gate on it *as a quality issue*. ADR-066 holds: policy
decides acceptance, not facts.

### D4. Record before disposing

Informational findings stay in every report:

- the `changes` array;
- `summary.informational` (new);
- `severity.categories.informational` (new; level `info`, absent from
  `severity.config`, never in `blocking_categories`);
- the per-finding `category`;
- the release `libraries[].informational` count and finding `bucket`;
- Markdown ("ℹ️ Informational (no material change)" section and summary
  row);
- the review digest;
- JUnit (a passing test case);
- SARIF (`note` under a severity setting);
- GitHub annotations (`notice`, behind the existing opt-in);
- the PR comment, where it is never a "policy violation".

`recommended_action` is `no_action_required`.

### D5. Schema versions

`summary.quality_issues` narrows: it no longer counts informational
outcomes. When a required field changes meaning, that is a breaking change
under the report schema's own policy (the 4.0 `compatible_additions`
precedent). So:

- `REPORT_SCHEMA_VERSION` 5.13 → **6.0**;
- `RELEASE_SCHEMA_VERSION` 1.13 → **2.0** (the per-library `quality_issues`).

The pre-6.0 value is `quality_issues + informational`. Also,
`compatible_additions + quality_issues + informational` equals the number of
COMPATIBLE findings.

### D6. Exit codes

The legacy verdict scheme is unchanged: a COMPATIBLE finding was already
exit `0`. Under the severity scheme, an informational finding no longer
contributes `1` when `quality_issues` is `error`. That covers `strict`, and
any config setting `quality_issues: error`. Nothing else moves.

## Alternatives rejected

- **Exempt the kinds from the gate but keep them in `quality_issues`.**
  Rejected for two reasons. The bucket would still claim to hold a quality
  problem. And it puts a per-kind exception list inside the gate, which
  ADR-049 D8 works to avoid.
- **A configurable `informational` level.** Rejected because "never gates
  under any preset" is the point. A level that can be set to `error`
  re-opens the defect for anyone who copies a strict config.
- **Additive-only schema (6.0 avoided).** Keep `quality_issues` at its old
  meaning and publish `informational` as a subset of it. Rejected because
  the summary would then disagree with `severity.categories` about what
  `quality_issues` holds.

## Consequences

- `--severity-preset strict` no longer fails a run whose only findings say
  nothing changed.
- JSON consumers keyed on report major `5` must accept `6`. The one meaning
  change is documented on the field itself.
- Python callers iterating `IssueCategory` and calling
  `getattr(config, cat.value)` must use `level_for` or
  `CONFIGURABLE_CATEGORIES`. `CategorizedChanges.informational` defaults to
  empty, so four-field construction keeps working.
