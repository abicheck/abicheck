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

`abicheck compare` prints `markdown` by default; pass `--format json` for
machine-readable output (CI logic, agents), or `--format sarif`/`html`/`junit`
for Code Scanning, standalone reports, or CI test dashboards respectively:

```bash
abicheck compare libfoo.so.1 libfoo.so.2 -H foo.h --format json -o result.json
```

See [Output Formats](../use/output-formats.md) for the full reference
(field-by-field JSON schema, SARIF/JUnit details, the `review` digest).

## Exit codes and CI

By default, `abicheck compare` exits with the
[verdict](../learn/verdicts.md):

| Exit code | Verdict | Meaning |
|-----------|---------|---------|
| `0` | `NO_CHANGE` / `COMPATIBLE` / `COMPATIBLE_WITH_RISK` | Safe — no binary ABI break |
| `2` | `API_BREAK` | Source-level API break (binary still works) |
| `4` | `BREAKING` | Binary ABI break |
| `64` | — | Invalid invocation (bad args/options, unreadable input) — outside the verdict space |

Passing any `--severity-*` flag switches `compare` to a different,
severity-aware exit-code scheme instead — see
[Severity Configuration](../use/severity.md) for the full mapping and policy
recipes. Other commands add their own codes on top of this space — `scan` can
exit `5` (a `--budget` time guard tripped) and a multi-library release
compare can exit `8` (a library was removed with
`--fail-on-removed-library`). The full per-command matrix, including
`compat` mode, is the [Exit Codes reference](../reference/exit-codes.md).

Suppressions/policies/baselines all interact with the same pipeline before
the exit code is computed — see [CI Gating](../use/ci-gating.md) for how
those pieces fit together, and the
[GitHub Action](../use/github-action.md) for the fastest way to wire this
into CI (it installs Python/castxml/abicheck and runs the comparison in a few
lines of YAML).

## Next

➡️ **[Choose Your Workflow](choose-your-workflow.md)** — map your artifacts
and CI policy to the exact command for ongoing use.
