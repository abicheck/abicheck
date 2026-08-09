---
doc_type: reference
level: advanced
lifecycle: active
summarizes:
  - policies
  - suppressions
---

# Policies, severity, and suppressions

Three different mechanisms, routinely confused. Getting the distinction right
matters because two of them change *what is reported* and one changes *how a
report is graded* — and only one of them can hide a real break.

## Policy profiles — what a change kind means for this project

`--policy strict_abi|sdk_vendor|plugin_abi` selects a built-in profile;
`--policy-file FILE` supplies a project's own YAML profile, which can
override the verdict for specific change kinds and declare internal
namespaces. Policy answers "for a library of this shape, is this kind of
change breaking?" — a plugin ABI and a vendored SDK genuinely disagree.

Owned by [the policy profiles page](../../docs/use/policies.md).

## Severity and the gate — how a report becomes an exit code

`--severity-abi-breaking`, `--severity-potential-breaking`,
`--severity-addition`, `--severity-quality-issues`, and
`--severity-preset default|strict|info-only` set the severity assigned per
category; `--exit-code-scheme auto|legacy|severity` selects which exit-code
contract applies.

In the report, `severity.exit_code`, `severity.blocking`, and
`severity.blocking_categories` express the gate decision — and the whole
`severity` block is present **only when the run passed a `--severity-*` flag**.
Without one there is no gate: the exit code follows the legacy verdict
mapping. (`policy_gate_decision` is an Action `check-target` field, not
something a direct `compare` emits — see
[report-interpretation.md](report-interpretation.md).)

Severity configuration is a **grading** decision. Changing it never removes a
finding; it changes whether that finding blocks. Reporting "no blocking
findings" while a `BREAKING` finding sits in `changes` is only honest if you
say both.

## Suppressions — hiding a known, accepted finding

`--suppress FILE` applies a suppression file (abicheck YAML or ABICC format);
`--strict-suppressions` and `--audit-suppressions` make stale or overbroad
rules visible, and the report's `suppression` / `suppression_audit` blocks
record what was applied.

Owned by [the suppressions page](../../docs/use/suppressions.md).

**This is the mechanism that can hide a real break.** The rules:

- You may point out that an existing rule already covers a finding, and
  explain why it does.
- You may not author, widen, or relax a rule to make output quieter unless
  the user asked for that specific change and can see it
  ([safety-invariants.md](safety-invariants.md) item 6).
- A suppressed finding is still a finding. If a summary would read
  differently without the suppression, say that the suppression is load-bearing.
- Contract-coverage failures are structurally unsuppressible — no
  suppression rule can reach them. That is deliberate; do not present a run
  with `contract_coverage_exit_contribution == 1` as clean.

## Choosing between them when a finding is unwanted

| The finding is... | Right mechanism |
|---|---|
| real, but not breaking for a library of this shape | policy profile |
| real and breaking, but this project accepts it for now | severity/gate configuration, visibly |
| real, breaking, and specifically accepted once, with justification | suppression rule, authored by the user |
| not real — an artifact of scope or toolchain drift | fix the scoping or the profile, not the policy |

The last row is the common case. Reach for
[public-surface-and-scoping.md](public-surface-and-scoping.md) and
[compiler-and-build-profiles.md](compiler-and-build-profiles.md) before
reaching for a suppression.
