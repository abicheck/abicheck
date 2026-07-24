---
doc_type: reference
audience:
  - library-maintainer
  - ci-owner
level: intermediate
lifecycle: active
generated: false
---

# Exit Codes

`abicheck` uses different exit codes for each command family.

**Why they differ:** `compare` is the native interface — `0/2/4` by verdict (or `0/1/2/4` severity-aware), with invalid invocations exiting `64` so a usage error is never mistaken for an ABI verdict. `compat` mirrors `abi-compliance-checker` exit codes (0/1/2) so existing ABICC CI scripts work without changes. `scan` and `deps` have their own narrower contracts, documented below.

> **Proposed contract-aware extension (not implemented):**
> [ADR-049](../development/adr/049-contract-relevance-and-compatibility-configuration.md)
> reserves an orthogonal contract-coverage contribution for future contract
> evaluation. Complete coverage of the mode-selected evidence domain contributes
> `0`; missing, partial, stale, failed, contradictory, or identity-incomplete
> **required domain evidence** produces `UNKNOWN_UNRESOLVED`,
> `analysis_status=NOT_CHECKABLE`, and contributes `1` by default. Unrelated
> provider failures are advisory. The configured `GateDecision` independently
> contributes `0/1/2/4`: a compatible addition can block, and a breaking finding
> can be demoted. Only legacy output without a gate block falls back from
> compatibility verdict to `2`/`4`. Command aggregation folds gate and coverage
> contributions using its existing rules. Ordinary change suppressions cannot
> clear provider/domain coverage; the explicit proposed
> `unresolved_behavior=warn` control is the permissive override. Existing
> command-specific `5`, `8`, and `64` behavior remains as documented below.
> Reports will distinguish contract coverage exit `1` from severity or
> aggregate required-target coverage. Until ADR-049 is implemented, the tables
> below describe the actual released command behavior.

## Commands removed in the ADR-043 CLI reset

`appcompat` and `plugin-check` are gone as standalone commands; their scoping
folded into `compare` itself — see
[Application- and plugin-scoped comparisons](#application-and-plugin-scoped-comparisons-compare-used-by-required-symbol)
below. `baseline` (the push/pull/list/delete registry), `debian-symbols`,
`collect`, `merge`, `inputs validate`, and `inputs compact` were removed
outright with no CLI replacement — validating a build-emitted
`abicheck_inputs/` pack now happens automatically whenever the pack is
consumed, and the `debian-symbols`/`collect`/`merge` library functions remain
available for programmatic (Python API) use only. None of these have their
own exit codes in the current CLI, so they no longer appear in the tables
below.

---

## `abicheck compare`

### Legacy exit codes (default, no `--severity-*` flags)

| Exit code | Meaning |
|-----------|---------|
| `0` | `NO_CHANGE`, `COMPATIBLE`, or `COMPATIBLE_WITH_RISK` — no binary ABI break |
| `2` | `API_BREAK` — source-level API break — recompilation required |
| `4` | `BREAKING` — binary ABI break |
| `64` | Invalid invocation — bad arguments/options or an unreadable/unrecognised input, deliberately outside the `0/2/4` verdict space |

> **⚠️ Exit `0` covers `NO_CHANGE`, `COMPATIBLE`, and `COMPATIBLE_WITH_RISK`.** If your pipeline needs
> to distinguish them (e.g. warn on deployment risk), use `--format json` and
> read the `verdict` field — exit code alone is not sufficient.

### Severity-aware exit codes (with any `--severity-*` flag)

When any `--severity-preset` or `--severity-*` option is provided, the exit code
is computed from the severity configuration rather than the verdict:

| Exit code | Meaning |
|-----------|---------|
| `0` | No error-level findings |
| `1` | Error-level findings in `addition` or `quality_issues` only |
| `2` | Error-level findings in `potential_breaking` (but not `abi_breaking`) |
| `4` | Error-level findings in `abi_breaking` |

The highest applicable code wins. For example, if both `abi_breaking=error` and
`quality_issues=error` have findings, the exit code is `4`.

> **ℹ️ The two exit code paths are mutually exclusive.** Without `--severity-*`
> flags, the legacy verdict-based path runs. With any `--severity-*` flag, the
> severity-aware path runs. They never both execute.

### Severity presets

| Preset | `abi_breaking` | `potential_breaking` | `quality_issues` | `addition` |
|--------|---------------|---------------------|------------------|-----------|
| `default` | error | warning | warning | info |
| `strict` | error | error | error | error |
| `info-only` | info | info | info | info |

Per-category overrides (`--severity-abi-breaking`, `--severity-potential-breaking`,
`--severity-quality-issues`, `--severity-addition`) take precedence over the preset.

### CI gate patterns

```bash
# Production gate: fail on any break (legacy exit codes)
abicheck compare old.json new.json
ret=$?
[ $ret -eq 4 ] && echo "BREAKING — release blocked" && exit 1
[ $ret -eq 2 ] && echo "API_BREAK — source-level break" && exit 1
echo "OK (NO_CHANGE or COMPATIBLE)"

# Block unexpected API expansion (severity-aware)
abicheck compare old.json new.json --severity-addition error
ret=$?
[ $ret -eq 1 ] && echo "ADDITIONS — unexpected API expansion" && exit 1
[ $ret -eq 4 ] && echo "BREAKING — release blocked" && exit 1
[ $ret -eq 2 ] && echo "API_BREAK — source-level break" && exit 1
echo "OK"

# Strict mode: all categories at error level
abicheck compare old.json new.json --severity-preset strict

# Permissive gate: fail only on binary breaks
abicheck compare old.json new.json
ret=$?
[ $ret -eq 4 ] && exit 1   # BREAKING only; API_BREAK (exit 2) allowed
exit 0

# Parse exact verdict from JSON (with severity info)
abicheck compare old.json new.json --format json --severity-preset default -o result.json
verdict=$(python3 -c "import json,sys; d=json.load(open('result.json')); print(d['verdict'])" \
  || { echo "ERROR parsing result.json"; exit 1; })
[ "$verdict" = "BREAKING" ] && exit 1
```

---

## `abicheck compare` (multi-library / release inputs)

When `compare` is handed directory or package inputs (RPM/deb/tar/conda/wheel),
it fans out to per-library pairs and aggregates the worst per-library verdict
across the release — the behaviour formerly exposed as the standalone
`compare-release` command (folded into `compare` per ADR-037 D7; the GitHub
Action's own `compare-release`/`stack-check` mode aliases were removed the
same way, per ADR-043 — `mode: compare` handles directory/package operands
directly). By default a set/release comparison uses the verdict-based scheme
below, plus a dedicated code for removed libraries:

| Exit code | Meaning |
|-----------|---------|
| `0` | All libraries compatible (no API/ABI break) |
| `2` | Worst verdict is `API_BREAK` |
| `4` | Worst verdict is `BREAKING`, **or** an operational `ERROR` (a library failed to dump/extract/compare) |
| `8` | A library was removed between releases and `--fail-on-removed-library` is set. In the legacy scheme this is emitted only when no API/ABI verdict exit 2/4 **and no operational `ERROR` exit 4** already applies; in the severity-aware scheme it takes precedence over 0/1/2/4. |

On the release path the severity-aware code (`0/1/2/4`) replaces the
verdict-based `2/4` mapping only when a severity *map* is actually in effect —
that is, any `--severity-*` flag is passed **or** `.abicheck.yml` carries a
`severity:` block (a preset or per-category levels). Setting `exit_code_scheme:
severity` on its own is **not** enough for directory/package inputs: with no
severity values to apply, the fan-out has nothing to score against and falls
back to the legacy verdict mapping. Under the legacy mapping, an operational `ERROR` exit 4 or nonzero API/ABI
verdict exit (`2`/`4`) wins before the removed-library check; under an effective
severity map, removed-library exit `8` wins over the aggregated `0/1/2/4` code.
An operational `ERROR` without a higher-priority removed-library result still
floors the severity-aware exit at `4`. (`--exit-code-scheme` is rejected on
directory/package inputs; pin the legacy scheme in config with
`exit_code_scheme: legacy` if you want to force it.) One consequence worth
gating on: with an effective severity map, a release whose worst verdict is
`BREAKING` can still exit `0` if that map downgrades ABI breaks (e.g.
`abi_breaking: warning`) — parse the `verdict` from JSON output if you need
scheme-independent CI behaviour.

---

## `abicheck scan`

The one-shot source-intelligence scan has its own contract (it may compare
`ARTIFACT` against `--against` and adds a budget guard). `--against` is the
only thing that selects the mode: omit it and `scan` runs a one-build
audit/hygiene/source-consistency scan only; pass it and `scan` also compares
`ARTIFACT` against it — there is no separate `--audit` flag:

| Exit code | Meaning |
|-----------|---------|
| `0` | Compatible (or advisory-only findings) |
| `2` | Source-level / API break (incl. `API_BREAK` cross-source findings) |
| `4` | ABI break (from the `--against` comparison) |
| `5` | `--budget` overflow — the time guard tripped (scope is never silently shrunk) |
| `64` | Invalid invocation (bad arguments/options) |

> Exit `5` is unique to `scan`: `--budget 15m` **fails** the run rather than
> quietly dropping evidence. Use `--dry-run` to preview the audit checks and
> (if `--against` is given) the comparison that would run, plus the projected
> per-layer cost, without scanning — like every command's `--dry-run` it only
> ever exits `0`/`1`/`64`, never a verdict code; see
> [`--dry-run`](#-dry-run-dump-compare-scan-deps-tree-deps-compare) below.

---

## `abicheck aggregate`

The multi-target fan-in gate folds the per-target `compare`/`scan` JSON reports
a CI build matrix produces (one `abi-report-<target>.json` per leg) into one
gate decision. Three axes stay **orthogonal** (ADR-042), and the exit code is
the worst contribution across them:

- **gate** — each report already carries its own severity gate decision
  (`severity.{exit_code,blocking,blocking_categories}`); `aggregate` *combines*
  those, it never recomputes a gate from the compatibility verdict. So a
  `COMPATIBLE` report with an `addition=error` policy still contributes exit
  `1`, and a `BREAKING` report under a demoted preset can contribute `0`. A
  `scan` report is read via its own top-level `exit_code` (keyed on
  `scan_schema_version`). Reports produced without any gate block fall back to
  the legacy verdict→exit mapping (`0`/`2`/`4`). Reading is **fail-closed**: a
  report whose gate block is *present but corrupt* (an out-of-range or
  non-integer `exit_code`, a `blocking` flag that contradicts it, non-string
  categories) makes that target *unavailable* — never silently reverting to the
  greener legacy path.
- **coverage** — did every *required* expected target actually report? An
  incomplete required coverage is a *coverage* failure at exit `1`; it is
  **never** promoted to an ABI-break exit `4`.
- **compatibility** — the worst verdict over the analyzed targets, reported for
  context; it does not by itself drive the exit code.

| Exit code | Meaning |
|-----------|---------|
| `0` | Every required target analyzed, no blocking findings |
| `1` | A required target was unavailable (coverage gap, default `--on-missing-required fail`); an analyzed target's gate blocks on an `addition`/`quality` finding only; **or** a non-verdict per-report failure folds here (e.g. a `scan` report's budget-overflow exit `5`) |
| `2` | An analyzed target's gate is a source-level / API break |
| `4` | An analyzed target's gate is an ABI break |
| `64` | Invalid invocation (bad arguments/options, malformed manifest, duplicate target id, or no expected-target set given) |

The highest applicable code wins: a run with both an ABI break and a coverage
gap exits `4`; a run whose *only* problem is a missing required target exits
`1`, never `4`.

> **A required target with no report is _unavailable_ (unknown), never counted
> as compatible.** This is the whole point of the command: a matrix leg that
> failed before uploading its report is reported as a coverage gap and fails
> the gate at exit `1` — it is never silently folded into the verdict as an
> empty, compatible ABI, and a build that simply never ran is never handed an
> ABI-break exit `4`.

**Declaring the expected-target set (required — one of):**

- `--manifest abi-targets.json` (recommended) — the single source of truth for
  which targets the matrix must produce: `{"targets": [{"id": "linux-x86_64",
  "required": true}, ...]}`. Generate it once in the plan job and feed the same
  file to both the matrix and this gate so they never drift.
- `--expect <ids>` (repeatable / comma-separated), with optional `--optional
  <ids>` — an inline alternative to a manifest file.
- `--discovered-only` — explicitly aggregate whatever reports are present with
  **no** coverage gate (pure worst-of the gate decisions). Required to run
  without a manifest/`--expect`: with no declared target set the gate cannot
  tell a missing required target from an intentionally absent one, so a bare
  `aggregate reports/` is a usage error (exit `64`), not a silent pass.

`--on-missing-required warn` downgrades a coverage gap to advisory (the
per-target gate decisions alone then decide the exit code). `--on-unexpected-target`
(`include`/`warn`/`fail`/`ignore`, default `include`) controls a report whose
target is not in the expected set: `include` counts its real findings in the
gate but not in required coverage. The `--format json` output is versioned
(`aggregate_schema_version`) and carries the three axes separately under
`gate` / `coverage` / `compatibility`.

---

## Application- and plugin-scoped comparisons (`compare --used-by`/`--required-symbol`)

The standalone `appcompat` and `plugin-check` commands are gone (ADR-043).
Their scoping now folds into `compare` itself:

- **`compare --used-by APP`** (repeatable) — folds `appcompat`. `APP` is a
  real application binary; its actual imports/required symbol versions scope
  the comparison. `OLD`/`NEW` may be real library binaries or JSON snapshots
  that carry binary evidence (a `dump` of a real library, not headers-only).
  Mutually exclusive with `--required-symbol`/`--required-symbols`.
- **`compare --required-symbol SYM`** (repeatable) / **`--required-symbols
  FILE`** — folds `plugin-check`. Scopes the comparison to an explicit
  dlopen/dlsym entrypoint contract instead of the full diff. Mutually
  exclusive with `--used-by`.

The full library comparison still runs once; **the worst app/plugin-scoped
result becomes the primary verdict/exit code**, with the full verdict and
unrelated changes kept as informational context. There is no separate
exit-code scheme for this scoping — it uses exactly the `compare` codes
documented above (legacy `0/2/4`, severity-aware `0/1/2/4`, `64` for a usage
error). In particular, exit `4`/`BREAKING` is also the result when the
application requires symbols or ELF version tags absent from the new
library — even if the unscoped library diff is otherwise compatible —
because the application would fail to load.

---

## `abicheck deps tree`

| Exit code | Meaning |
|-----------|---------|
| `0` | All dependencies resolved, all required symbols bound |
| `1` | Missing dependencies or unresolved symbols (binary would fail to load) |
| `64` | Invalid invocation (bad arguments/options) |

`--dry-run` shows the resolved binary path and search order without
resolving the dependency tree — see
[`--dry-run`](#-dry-run-dump-compare-scan-deps-tree-deps-compare) below.

---

## `abicheck deps compare`

Sysroot flags are **`--old-root`/`--new-root`** (default `/` for each —
renamed from the old `--baseline`/`--candidate`).

| Exit code | Verdict | Meaning |
|-----------|---------|---------|
| `0` | `PASS` | Binary loads and no harmful ABI changes |
| `1` | `WARN` | Binary loads but ABI risk detected in dependencies |
| `4` | `FAIL` | Load failure or binary ABI break in dependencies |
| `64` | — | Invalid invocation (bad arguments/options) |

`--dry-run` shows the old/new roots, resolved binary paths, and search order
without running per-library ABI diffs — see
[`--dry-run`](#-dry-run-dump-compare-scan-deps-tree-deps-compare) below.

### CI gate patterns

```bash
# Full-stack check: fail on FAIL, warn on WARN
abicheck deps compare usr/bin/myapp --old-root /old-root --new-root /new-root
ret=$?
[ $ret -eq 4 ] && echo "FAIL — load failure or ABI break" && exit 1
[ $ret -eq 1 ] && echo "WARN — ABI risk detected" && exit 1
[ $ret -ne 0 ] && echo "ERROR — unexpected non-verdict exit code: $ret" && exit 1
echo "PASS"

# Permissive: only fail on load failure / ABI break
abicheck deps compare usr/bin/myapp --old-root /old-root --new-root /new-root
ret=$?
[ $ret -eq 4 ] && exit 1   # FAIL only; WARN (exit 1) treated as OK
[ $ret -ne 0 ] && [ $ret -ne 1 ] && exit 1   # fail closed on non-verdict errors
exit 0
```

---

## `abicheck compat`

Matches `abi-compliance-checker` exit codes (ABICC drop-in):

| Exit code | Meaning |
|-----------|---------|
| `0` | No breaking changes (`NO_CHANGE` or `COMPATIBLE`) |
| `1` | `BREAKING` (mirrors ABICC) |
| `2` | `API_BREAK` (source-level break; non-verdict failures use extended codes below) |

> Non-verdict/tool failures are classified via **Extended compat error codes (ABICC-style)** below (`3`, `4`, `5`, `6`, `7`, `8`, `10`, `11`).

---


### Extended compat error codes (ABICC-style)

In `abicheck compat`, non-verdict failures are further classified where possible:

| Exit code | Typical cause |
|-----------|---------------|
| `3` | Required external command/tool is missing (for example `castxml`) |
| `4` | Cannot access input files (missing or permission denied) |
| `5` | Header compile/parsing failure during dump |
| `6` | Invalid compat configuration/input (descriptor, suppression, regex flags) |
| `7` | Failed to write report/output artifact |
| `8` | Dump/analysis pipeline failure |
| `10` | Generic internal/tool failure fallback |
| `11` | Interrupted run |

> Note: classification is best-effort and context-dependent; `API_BREAK` remains `2`.

---

## `--dry-run` (`dump`, `compare`, `scan`, `deps tree`, `deps compare`)

Every one of these five commands accepts `--dry-run`: it resolves and
validates the invocation — classifies inputs, discovers config, and (per
command) shows which data layers (L0–L5) are available, the audit checks and
comparison that would run, or the resolved binary path/search order — and
prints a report **without** doing the real work. It is cheap and read-only:
no compiler invocation, no build-system query, no network access, and it
writes nothing — passing `-o`/`--output` together with `--dry-run` is a
usage error.

| Exit code | Meaning |
|-----------|---------|
| `0` | Resolved cleanly — ok to proceed |
| `1` | Blocked — the invocation would fail once actually run |
| `64` | Usage error (e.g. `-o`/`--output` passed together with `--dry-run`) |

**`--dry-run` never returns a verdict code.** It exits `0`/`1`/`64` only —
never `2`, `4`, `5`, or `8`, even on a command whose real run could produce
one of those.

---

## Summary table

| Verdict / State | `compare` exit (legacy) | `compare` exit (severity) | `scan` exit | `deps tree` exit | `deps compare` exit | `compat` exit |
|-----------------|------------------------|--------------------------|-------------|-------------------|----------------------|---------------|
| `NO_CHANGE` / `PASS` / compatible | `0` | `0` | `0` | `0` | `0` | `0` |
| `COMPATIBLE` | `0` | `0` | `0`‡ | — | — | `0` |
| `COMPATIBLE_WITH_RISK` | `0` | `0`–`2`* | `0`‡ | — | — | `0` |
| Additions only | `0` | `0`–`1`* | `0`‡ | — | — | n/a |
| Quality issues only | `0` | `0`–`1`* | `0`‡ | — | — | n/a |
| `WARN` (ABI risk) | — | — | — | — | `1` | — |
| `API_BREAK` | `2` | `0`–`2`* | `2` | — | — | `2` |
| `BREAKING` / `FAIL` | `4` | `4` | `4` | — | `4` | `1` |
| `--budget` overflow | — | — | `5` | — | — | — |
| Missing dependencies/symbols | — | — | — | `1` | — | — |
| Load failure | — | — | — | — | `4` | — |
| Invalid invocation / tool error | `64`† | `64`† | `64`† | `64`† | `64`† | `3/4/5/6/7/8/10/11` |

App/plugin-scoped comparisons (`compare --used-by`/`--required-symbol`) reuse
the `compare` columns above — see
[Application- and plugin-scoped comparisons](#application-and-plugin-scoped-comparisons-compare-used-by-required-symbol).
`aggregate` combines each report's own severity gate (`0`/`1`/`2`/`4`) over its
analyzed targets and adds a coverage gate (a required gap exits `1`, never `4`) —
see [`abicheck aggregate`](#abicheck-aggregate).
`--dry-run` (on `dump`/`compare`/`scan`/`deps tree`/`deps compare`) reuses
none of these rows — it always exits `0`/`1`/`64`; see
[`--dry-run`](#-dry-run-dump-compare-scan-deps-tree-deps-compare) above.

\* Severity exit codes depend on the configuration. For example, with
`--severity-addition error`, additions exit `1`; with `--severity-preset
info-only`, everything exits `0`.

† Every command exits `64` for an invalid invocation — bad arguments/options
or an unreadable/unrecognised input — deliberately outside the verdict/result
space so a usage error is never mistaken for a compatibility result. To
reliably distinguish verdicts from errors in a script, use `--format json` and
read the `verdict` field where available.

‡ `scan`'s own scheme collapses every compatible/advisory-only state (no
break, deployment risk, additions, quality signals) to exit `0` — read
`--format json` if your pipeline needs to distinguish them.

---

## Strict mode (`-s` / `-strict`)

`compat` (and only `compat`) supports strict mode to promote lesser verdicts:

```bash
# Strict mode: COMPATIBLE + API_BREAK → exit 1 (BREAKING)
abicheck compat -lib foo -old OLD.xml -new NEW.xml -s

# Strict API-only: only API_BREAK → exit 1; COMPATIBLE stays exit 0
abicheck compat -lib foo -old OLD.xml -new NEW.xml -s --strict-mode api
```

`--strict-mode` values:
- `full` (default when `-s` is set): `COMPATIBLE` + `API_BREAK` → BREAKING
- `api`: only `API_BREAK` → BREAKING; `COMPATIBLE` unchanged

`--strict-mode` has no effect unless `-s` is also passed.

> Note: `abicheck compare` does not have `-s` / `--strict` flags.
> For compare-mode strict pipelines, use CI exit code logic (check exit `2` as a failure).
