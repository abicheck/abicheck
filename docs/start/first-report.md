---
doc_type: tutorial
audience:
  - library-maintainer
level: beginner
summarizes:
  - verdicts
lifecycle: active
generated: false
---

# Understand your first report

## Output formats

`abicheck compare` prints `markdown` by default; pass `-o json=-` for
machine-readable output (CI logic, agents), or `-o sarif=...`/`html`/`junit`
for Code Scanning, standalone reports, or CI test dashboards respectively:

```bash
abicheck compare libfoo.so.1 libfoo.so.2 -H foo.h -o json=result.json
```

`-o` is repeatable — every export renders the same one completed analysis,
so asking for more artifacts never changes the result. See
[Output Formats](../use/output-formats.md) for the full reference
(field-by-field JSON schema, SARIF/JUnit details, the `review` digest).

## Read the result before you read the exit code

An exit code is **the configured acceptance decision**, not a measurement of
your library. It answers "did this run pass the gate I set up?" — which
depends on what evidence was available, what scope was compared, and what
policy was in effect, none of which the number itself tells you.

Four questions, answered by four different parts of the report. Ask them in
this order:

| Question | Where the answer is |
|---|---|
| **1. Was a comparison performed at all** — or was this a one-sided audit, or a pair the comparability gate rejected? | `no_baseline` / `old_acquisition_state`; `run_outcome`; a `null` `verdict` with a `reason` |
| **2. What was established?** | `changes` (two-sided findings) and `findings`; `summary` |
| **3. What evidence and scope supported it?** | `analysis_assurance`, `surface_scope`, the evidence-coverage block — plus `comparison_scope` on a directory/package release compare |
| **4. Why did the gate accept or reject?** | `exit` / `exit_axes` and their `reasons`, plus `severity` |

Skipping straight to step 4 is how a run that compared nothing gets read as
a clean bill of health.

### A compact worked example

Two runs over the same library. Both are "not a break". They mean very
different things.

**Run A — a real comparison, nothing changed.**

```bash
abicheck compare libfoo.so.1 libfoo.so.2 \
  --header old=v1/foo.h --header new=v2/foo.h
# exit 0
```

1. A comparison ran: two operands, `old_acquisition_state` absent.
2. `changes: []`, `verdict: NO_CHANGE`.
3. L0/L1/L2 all present; `analysis_assurance.status: complete`.
4. No error-level finding, so the gate accepts.

Read as: this pair was compared with header-level evidence and nothing
changed.

**Run B — a one-sided audit, advisory.**

```bash
abicheck compare --no-baseline build/libfoo.so -H include/ --policy pol.yaml
# exit 0
```

1. **No comparison ran.** `no_baseline: true`,
   `old_acquisition_state: "declared_absent"`, `verdict: null`.
2. `changes: []` — always, by construction. The one finding is in
   `findings`: `exported_not_public`, promoted to `BREAKING` by the policy.
3. One build's evidence only. There is no baseline to have evidence *about*.
4. `exit_axes.audit_gate: 0` — the audit gate was never armed, so a
   `BREAKING` finding contributes nothing.

Read as: an audit found something the policy calls breaking, and this run
was configured not to act on it. Adding `--severity-preset default` makes
the same run exit `3`.

Run B's `changes: []` and Run A's `changes: []` look identical to a consumer
reading only that key. They are not the same statement.

### Things an exit code does not mean

- **No findings is not a complete analysis.** Check `analysis_assurance` and
  the evidence-coverage block. A symbols-only run finds fewer things because
  it sees less, not because less changed.
- **Missing optional DWARF is not a failed analysis.** DWARF, build data and
  source evidence are optional throughout. Their absence narrows what can be
  concluded, and is reported; it is a failure only where a `--depth` was
  pinned that the evidence could not reach (exit `7`).
- **An advisory run is not proof of compatibility.** A gate configured not
  to act is a policy decision, not a result.
- **`NOT_COMPARABLE` is not an ABI break.** It means the pair could not be
  compared under a comparable profile/scope — no verdict was produced.
- **No baseline is not an unchanged baseline.** See Run B.
- **A consumer scope does not replace the library result.** Supplying
  `--used-by` adds a consumer-impact assessment beside the full-library
  verdict; it never narrows the comparison and never becomes the exit code.
  See [Application Compatibility](../use/appcompat.md).

## Exit codes and CI

With no severity setting in effect, `abicheck compare` exits with the
[verdict](../learn/verdicts.md):

| Exit code | Verdict | Meaning |
|-----------|---------|---------|
| `0` | `NO_CHANGE` / `COMPATIBLE` / `COMPATIBLE_WITH_RISK` | No binary ABI break was detected **by this run's evidence and scope** — read the report before treating it as "safe" |
| `2` | `API_BREAK` | Source-level API break (binary still works) |
| `4` | `BREAKING` | Binary ABI break |
| `64` | — | Invalid invocation (bad args/options, unreadable input) — outside the verdict space |

Passing `--severity-preset` (or setting a config `severity:` block) switches
`compare` to the severity-aware scheme — see
[Severity Configuration](../use/severity.md) for the full mapping and policy
recipes.

Three **orthogonal** axes can raise a clean exit on top of either scheme.
They fold with `max`, so they never lower a `2`/`4`:

- `1` — incomplete `--contract` coverage, or incomplete analysis assurance
  under `.abicheck.yml`'s `assurance.require_complete: true`.
- `3` — the `--no-baseline` audit gate, armed only by `--severity-preset`.
- A multi-library release compare can exit `8` when a library was removed
  with `gate.fail_on_removed_library: true`.

To tell these apart in CI, read the JSON report's `exit` block (its
`reasons` and per-axis contributions), not the process exit alone. The full
per-command matrix, including `compat` mode, is the
[Exit Codes reference](../reference/exit-codes.md).

## Audit runs (`--no-baseline`)

A `--no-baseline` run is an **audit of one build**, not a comparison:

- It reports candidate-side facts only — hygiene and cross-source
  observations. Never an addition, a removal, or a compatibility verdict.
- `verdict` is always `null`, meaning "no comparison was performed". That is
  a different null from a `compare` report's, which means "the comparability
  gate rejected this pair" and comes with a `reason`.
- `changes` is always empty. **Read `findings`.**
- It is a separate document with its own `audit_report_schema_version`.
- Its findings are **advisory by default**. `--severity-preset` (any value
  other than `info-only`) is the sole switch that arms its gate, which
  contributes exit `3`. A `--policy` file that promotes a finding to `break`
  does **not** arm it.

Migrating a gating `scan` job? See
[Upgrading from 0.5 to 0.6 → re-arming an audit gate](upgrading-to-0.6.md#a2-re-arming-an-audit-gate).

---

Suppressions/policies/baselines all interact with the same pipeline before
the exit code is computed — see [CI Gating](../use/ci-gating.md) for how
those pieces fit together, and the
[GitHub Action](../use/github-action.md) for the fastest way to wire this
into CI (it installs Python/castxml/abicheck and runs the comparison in a few
lines of YAML).

## Next

➡️ **[Choose Your Workflow](choose-your-workflow.md)** — map your artifacts
and CI policy to the exact command for ongoing use.
