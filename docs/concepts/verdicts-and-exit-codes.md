# Verdicts and Exit Codes

Every `abicheck compare` run collapses its findings into one **verdict**, and
the verdict (or, in severity-aware mode, the severity of the findings) becomes
the process **exit code** your CI reads. This page bridges the whole chain:

**Verdict → severity category → exit code → CI action.**

It is a map, not the authority. The [Verdicts](verdicts.md) page defines each
verdict in depth; [Exit Codes](../reference/exit-codes.md) is the authoritative,
exhaustive per-command exit-code reference. Where this page and that reference
appear to differ, the reference wins.

---

## The chain

Each detected change is classified into exactly one **`ChangeKind`**, which
belongs to one **severity category** (`abi_breaking`, `potential_breaking`,
`quality_issues`, or `addition`). The run's **verdict** is the worst
classification across all changes. The exit code is then computed one of two
ways depending on whether severity flags are in play:

| Verdict | Meaning | Severity category | Legacy exit code | Severity-aware exit code | Typical CI action |
|---------|---------|-------------------|:----------------:|:------------------------:|-------------------|
| `NO_CHANGE` | Snapshots are identical — no differences. | *(none)* | `0` | `0` | Pass. |
| `COMPATIBLE` | Backwards-compatible change — additions or hygiene signals; existing binaries and source unaffected. | `addition` / `quality_issues` | `0` | `0`–`1`* | Pass (warn). Promote to error only if policy forbids the change. |
| `COMPATIBLE_WITH_RISK` | Binary-compatible, but a deployment risk that needs manual review (e.g. a newer glibc requirement, `noexcept` removed). | `potential_breaking` | `0` | `0`–`2`* | Warn and review; do not fail automatically. |
| `API_BREAK` | Source-level API break — headers changed incompatibly; already-compiled binaries still work, recompilation may fail. | `potential_breaking` | `2` | `0`–`2`* | Fail in source-strict / build-from-source pipelines; warn in ABI-only gates. |
| `BREAKING` | Binary ABI break — existing compiled consumers can crash, fail to load, or misbehave. | `abi_breaking` | `4` | `4` | Always fail; do not ship. |

\* Severity-aware codes depend on the active severity configuration. Under the
**`default`** preset (`abi_breaking=error`, everything else `warning`/`info`),
only `BREAKING` reaches an error level, so the other rows exit `0` unless you
raise their category — e.g. `--severity-addition error` makes an
additions-only `COMPATIBLE` exit `1`, and `--severity-preset strict` makes
`API_BREAK`/`COMPATIBLE_WITH_RISK` exit `2`.

> **Exit `0` is not one verdict.** In legacy mode, `NO_CHANGE`, `COMPATIBLE`,
> and `COMPATIBLE_WITH_RISK` all exit `0`. If your pipeline must distinguish
> them — for example to warn on deployment risk — read the `verdict` field from
> `--format json` rather than keying off the exit code alone.

---

## The two exit-code schemes

`abicheck compare` computes its exit code by one of two mutually-exclusive
paths. They never both run.

### Legacy (verdict-based) — the default

With no `--severity-*` flags, the exit code is the verdict itself, mapped
`0/2/4`:

- `0` — `NO_CHANGE`, `COMPATIBLE`, or `COMPATIBLE_WITH_RISK` (no binary break)
- `2` — `API_BREAK` (source-level break, recompilation required)
- `4` — `BREAKING` (binary ABI break)

### Severity-aware — opt-in

The severity-aware path runs when **any** `--severity-preset` or
`--severity-*` flag is passed (or an equivalent `severity:` block is set in
config). The exit code is then computed from the *severity* of findings, not
the verdict — the highest applicable code wins:

- `0` — no error-level findings
- `1` — error-level findings in `addition` or `quality_issues` only
- `2` — error-level findings in `potential_breaking` (but not `abi_breaking`)
- `4` — error-level findings in `abi_breaking`

The severity categories map directly onto the verdict tiers: `abi_breaking`
covers `BREAKING`; `potential_breaking` covers both `API_BREAK` and
`COMPATIBLE_WITH_RISK`; `quality_issues` and `addition` are the two halves of
`COMPATIBLE`. Configuring severity is covered in the
[Severity](../user-guide/severity.md) guide.

> **`64` means "not a verdict at all".** A bad invocation — unknown flags, an
> unreadable or unrecognised input — exits `64`, deliberately outside the
> `0/2/4` space, so a usage error is never mistaken for a compatibility result.

For every other command (`compat`, `appcompat`, `scan`, `deps`,
`debian-symbols`, multi-library/release inputs) and the full summary matrix,
see the authoritative [Exit Codes](../reference/exit-codes.md) reference.

---

## Wiring it into CI

The verdict-to-exit-code chain is what makes an ABI gate possible: pick the
scheme that matches your policy, then branch on the code. Worked gate patterns —
strict production gates, permissive binary-only gates, and severity-driven
gates that block unexpected API expansion — live in the
[CI Gating](../user-guide/ci-gating.md) guide.

---

## See also

- [Verdicts](verdicts.md) — full definition of each of the five verdicts.
- [Exit Codes](../reference/exit-codes.md) — authoritative per-command exit-code matrix.
- [Severity](../user-guide/severity.md) — presets and per-category overrides that drive the severity-aware scheme.
- [CI Gating](../user-guide/ci-gating.md) — turning exit codes into pass/fail pipeline logic.
