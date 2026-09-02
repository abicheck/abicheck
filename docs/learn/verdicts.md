---
doc_type: explanation
audience:
  - library-maintainer
  - ci-owner
level: intermediate
canonical_for:
  - verdicts
depends_on:
  - abicheck/change_registry.py
  - abicheck/checker_policy.py
lifecycle: active
generated: false
---

# Verdicts

Every `abicheck compare` run produces one of five core verdicts, ordered from
safest to most severe: `NO_CHANGE`, `COMPATIBLE`, `COMPATIBLE_WITH_RISK`,
`API_BREAK`, `BREAKING`. By default the verdict is the *worst* classification
across all detected changes under the active [policy](../use/policies.md).

> **Under `--contract` (opt-in), that's the worst classification
> across *evaluated* changes only.** Every detected change still exists and
> stays in the report, but a change contract relevance proved outside the
> declared contract, or couldn't resolve, never reaches policy at all — see
> "Contract evaluation and the verdict" below. There is still no sixth
> verdict; the five above are unchanged, they're just now computed over a
> possibly-narrower set of findings.

Each change kind is partitioned into exactly one classification set in
`checker_policy.py` — `BREAKING_KINDS`, `API_BREAK_KINDS`, `RISK_KINDS`, or
`COMPATIBLE_KINDS` — and `COMPATIBLE_KINDS` is further split into **additions**
(`ADDITION_KINDS`, new public surface) and **quality** signals
(`QUALITY_KINDS`, hygiene/metadata). The [Examples Encyclopedia](../reference/examples/index.md)
groups every fixture by both verdict and category.

> **A verdict is a fact; the release decision is policy.** For how these verdicts
> map onto SemVer version bumps and product-contract decisions (and why the same
> change can be breaking for one product and a non-event for another), see
> [Compatibility as a Product Contract](abi-series/00-product-contract.md#4-semantic-versioning-turning-the-promise-into-a-number).

> **Beyond the five core verdicts.** `compare` in severity-aware mode (any
> `--severity-*` flag) can also report **`SEVERITY_ERROR`** with exit code `1`
> when an addition/quality finding is promoted to error level — for example to
> block accidental public-API expansion. A package/bundle `compare` (directory
> or package inputs) adds
> **`REMOVED_LIBRARY`** (exit `8`) when a shared object present in the old
> package is absent from the new one. See the
> [GitHub Action](../use/github-action.md#outputs) and
> [Exit Codes](../reference/exit-codes.md) for the full matrix.

---

## The five verdicts

### `NO_CHANGE`
The two snapshots are **identical** — no differences found.

**CI action:** pass.

---

### `COMPATIBLE`
Changes found, but **backwards-compatible** — existing compiled consumers can upgrade without recompiling. abicheck splits this tier into two reportable categories:

**Additions** (`ADDITION_KINDS`) — new public surface:
- New exported symbol or global variable added
- Enum member appended at the end of an enum (no value shift)
- Union field added without growing the union's size
- Inline function outlined into the `.so` (new export, old inlined copies still work)
- `experimental::` graduated to stable while keeping the old alias

**Quality** (`QUALITY_KINDS`) — hygiene/metadata signals, not ABI breaks:
- `GLOBAL` → `WEAK` symbol binding (ELF/Linux; relaxes interposition only)
- GNU IFUNC introduced/removed
- SONAME/visibility/versioning hygiene findings (missing SONAME, RPATH leak, executable stack)

> **Note:** `noexcept` removal is **not** `COMPATIBLE` — it is `COMPATIBLE_WITH_RISK` (see below), because callers compiled assuming `noexcept` omit exception landing pads.

**CI action:** warn; do not fail. Set a severity value (e.g. `severity.addition: error` in `.abicheck.yml`) to promote additions/quality to an error-level `SEVERITY_ERROR` if your policy requires it.

---

### `COMPATIBLE_WITH_RISK`
A change that **does not break** existing compiled consumers (they are already linked and continue to work), but introduces a **deployment risk** that must be verified manually.

The library upgrade may fail on some target environments — for example, if the new library requires a newer glibc version that is absent on the deployment target — or the change is binary-linkable but semantically unsafe for binaries built under the old contract.

Examples (`RISK_KINDS`):
- New symbol version requirement added to `DT_VERNEED` (e.g. `GLIBC_2.17`) — existing binaries are safe, but the new `.so` won't load on systems with older glibc
- `noexcept` removed ([case15](../reference/examples/case15_noexcept_change.md)) — links fine, but callers built assuming `noexcept` omit landing pads, so a real throw calls `std::terminate`
- A CPU-dispatch ISA family dropped ([case83](../reference/examples/case83_cpu_dispatch_isa_dropped.md)) — loads fine, but the optimized path a consumer expected is gone

**CI action:** warn; inspect the specific change kind and verify target environment requirements. Do not fail automatically unless your policy mandates it.

> Use `abicheck compare --format json` to check the exact `verdict` field — `COMPATIBLE_WITH_RISK` exits with code `0`, same as `COMPATIBLE`.

---

### `API_BREAK`
A **source-level API break** — the public header contract changed in a way that breaks downstream source code, but **does not break already-compiled binaries**. Pre-compiled consumers continue to work at runtime. Consumers that **recompile** against new headers may get compile errors or semantic changes.

Examples:
- Field rename (same binary layout, different source name)
- Enum member rename
- Parameter default value removed
- Reduced access level (`public` → `protected`)

**CI action:** fail in API-strict pipelines or pipelines that test building from source; warn in ABI-only gates.

> **Note:** `abicheck compat` *does* emit exit code `2` for `API_BREAK` conditions.
> However, the `compat` HTML/text report uses ABICC-style phrasing
> ("⚠️ API_BREAK — Source-level API change — recompilation required") rather than a bare
> `API_BREAK` verdict string. Use `abicheck compare --format json` for machine-readable
> verdict values.

---

### `BREAKING`
A **binary ABI break** — existing compiled consumers malfunction when the library is updated.

Examples:
- Symbol removed from `.so`
- Function parameter type changed
- Struct field removed or offset shifted
- C++ vtable reordered (virtual method inserted)
- `const` qualifier added to global variable (moves to `.rodata`, breaks writes)

**CI action:** always fail; do not ship.

---

## Contract evaluation and the verdict

`compare --contract` (ADR-049 Phase 7) doesn't add a sixth
verdict — it changes which findings the five verdicts above are computed
*over*. Three separate questions are worth keeping apart, because collapsing
them is the most common source of confusion once this flag is in play:

1. **Finding existence.** Did `compare` detect a change at all? This is
   unaffected by contract evaluation specifically — a finding's contract
   relevance never removes it from the report. (A *different* mechanism,
   `--suppress`, does remove a matching finding from `changes` and lists it
   under `suppression.suppressed_changes` instead — see [Suppressions](../use/suppressions.md)
   — but that's an independent, unrelated waiver step, not something
   contract evaluation does.)
2. **Compatibility evaluation status.** Under `--contract`, each
   finding is either `EVALUATED` (its contract relevance is `IN_CONTRACT` or
   `NOT_APPLICABLE`) or `NOT_EVALUATED` (`PROVEN_OUT_OF_CONTRACT`,
   `UNKNOWN_UNPROVEN`, or `UNKNOWN_UNRESOLVED`). Without the flag, every
   finding is implicitly `EVALUATED` — nothing about this is new for a plain
   `compare`.
3. **Compatibility decision.** Only an `EVALUATED` finding gets one of the
   five verdict-tier classifications above. A `NOT_EVALUATED` finding's
   `compatibility_decision` field is JSON `null` — **`null` is not a sixth,
   compatible-leaning verdict; it means policy never ran on this finding.**
   The finding still carries its `ChangeKind`, its reason code, and its
   evidence references, so a reader can see exactly why it wasn't scored.

Under `--contract`, the run-level `verdict` is the worst
*compatibility decision* among `EVALUATED` findings — a run can detect a
real binary break and still exit clean if that break is
`PROVEN_OUT_OF_CONTRACT` (e.g. a private implementation detail outside your
declared public surface). Separately — not part of the verdict computation
at all — a run can still fail even with the worst evaluated *decision* at
`COMPATIBLE` if the selected domain's evidence was incomplete: contract
coverage (see [Exit Codes → Contract-coverage
contribution](../reference/exit-codes.md#contract-coverage-contribution-adr-049))
folds an independent exit `1` in with `max`, without touching any finding's
`compatibility_decision` or the verdict itself.

Because contract evaluation is strictly opt-in, none of this changes a plain
`compare` invocation's behavior. Full field-by-field detail:
[Compatibility Evaluation Config](../reference/compatibility-evaluation-config.md);
pipeline ordering: [CI Gating](../use/ci-gating.md).

### `BREAKING` doesn't always mean exit `4`

A related simplification worth retiring: in severity-aware mode, the
run-level *verdict* and the configured *severity gate* can deliberately
diverge. A scoped comparison (`compare --used-by`/`--required-symbol`) can
report a `BREAKING` verdict while a severity gate configured to only fail on
`abi_breaking`-category findings elsewhere in the run still exits `0` if
that particular category is set to `info`/`warning` rather than `error` —
the verdict is a fact about what was found, the gate is a separate,
independently-configured policy decision about what blocks CI. Read the
`verdict` field from `--format json` if you need the fact regardless of how
the gate is tuned; see [CI Gating](../use/ci-gating.md) for how the two
interact.

---

## From verdict to exit code

Each detected change is classified into exactly one **`ChangeKind`**, which
belongs to one **severity category** (`abi_breaking`, `potential_breaking`,
`quality_issues`, or `addition`). The run's **verdict** is the worst
classification across all changes, and the verdict (or, in severity-aware mode,
the severity of the findings) becomes the process **exit code** your CI reads:

**Verdict → severity category → exit code → CI action.**

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
raise their category — e.g. `severity.addition: error` makes an
additions-only `COMPATIBLE` exit `1`, and `--severity-preset strict` makes
`API_BREAK`/`COMPATIBLE_WITH_RISK` exit `2`.

> **Exit `0` is not one verdict.** In legacy mode, `NO_CHANGE`, `COMPATIBLE`,
> and `COMPATIBLE_WITH_RISK` all exit `0`. If your pipeline must distinguish
> them — for example to warn on deployment risk — read the `verdict` field from
> `--format json` rather than keying off the exit code alone.

### The two exit-code schemes

`abicheck compare` computes its exit code by one of two mutually-exclusive
paths. They never both run.

**Legacy (verdict-based) — the default.** With no `--severity-*` flags, the
exit code is the verdict itself, mapped `0/2/4`:

- `0` — `NO_CHANGE`, `COMPATIBLE`, or `COMPATIBLE_WITH_RISK` (no binary break)
- `2` — `API_BREAK` (source-level break, recompilation required)
- `4` — `BREAKING` (binary ABI break)

**Severity-aware — opt-in.** The severity-aware path runs when **any**
`--severity-preset` or `--severity-*` flag is passed (or an equivalent
`severity:` block is set in config). The exit code is then computed from the
*severity* of findings, not the verdict — the highest applicable code wins:

- `0` — no error-level findings
- `1` — error-level findings in `addition` or `quality_issues` only
- `2` — error-level findings in `potential_breaking` (but not `abi_breaking`)
- `4` — error-level findings in `abi_breaking`

The severity categories map directly onto the verdict tiers: `abi_breaking`
covers `BREAKING`; `potential_breaking` covers both `API_BREAK` and
`COMPATIBLE_WITH_RISK`; `quality_issues` and `addition` are the two halves of
`COMPATIBLE`. Configuring severity is covered in the
[Severity](../use/severity.md) guide.

> **`64` means "not a verdict at all".** A bad invocation — unknown flags, an
> unreadable or unrecognised input — exits `64`, deliberately outside the
> `0/2/4` space, so a usage error is never mistaken for a compatibility result.

For every other command (`compat`, `scan`, `deps`, multi-library/release
inputs) and the full summary matrix, see the authoritative
[Exit Codes](../reference/exit-codes.md) reference. App- and plugin-scoped
comparisons (`compare --used-by APP` / `compare --required-symbol SYM`) fold
into `compare`'s own exit-code scheme above — the worst app/plugin-scoped
result becomes the primary verdict/exit code.
Worked gate patterns beyond the templates below — strict production gates,
permissive binary-only gates, and severity-driven gates — live in the
[CI Gating](../use/ci-gating.md) guide.

---

## CI policy templates (compare mode)

### Strict production gate
```bash
abicheck compare old.json new.json
ret=$?
[ $ret -eq 4 ] && echo "BREAKING — release blocked" && exit 1
[ $ret -eq 2 ] && echo "API_BREAK — source-level break" && exit 1
[ $ret -ne 0 ] && echo "unexpected exit code $ret — check tool inputs" && exit 1
echo "OK (NO_CHANGE or COMPATIBLE)"
```

### Warning-only gate
```bash
abicheck compare old.json new.json --format json -o result.json
ret=$?
[ $ret -eq 4 ] && echo "::error::BREAKING ABI change" && exit 1
[ $ret -ne 0 ] && [ $ret -ne 2 ] && echo "::error::unexpected exit code $ret" && exit 1
[ $ret -eq 2 ] && echo "::warning::API_BREAK (source-level)"
verdict=$(python3 -c "import json; print(json.load(open('result.json'))['verdict'])" 2>/dev/null || echo "")
[ "$verdict" = "COMPATIBLE" ] && echo "::warning::COMPATIBLE ABI change (new symbols or compatible modifications)"
echo "ABI check passed"
```

### Permissive gate (binary breaks only)
```bash
abicheck compare old.json new.json
ret=$?
[ $ret -eq 4 ] && exit 1                    # BREAKING only; API_BREAK (exit 2) allowed
[ $ret -ne 0 ] && [ $ret -ne 2 ] && exit 1  # unexpected exit code (tool failure)
exit 0
```

> For `compat` mode CI patterns, see [ABICC Compatibility](../use/from-abicc.md).
> Note: in compat mode, exit `1` = BREAKING, exit `2` = API_BREAK.
> Non-verdict failures use extended codes (`3`–`11`) — see [Exit Codes](../reference/exit-codes.md).

---

Full exit code reference: [Exit Codes](../reference/exit-codes.md)

---

**Ladder:** ← [Series overview](abi-api-handling.md) · Concepts c1 · Reading a result · [Contract-Aware Compatibility](contract-aware-compatibility.md) →
