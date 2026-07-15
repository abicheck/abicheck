# Exit Codes

`abicheck` uses different exit codes for each command family.

**Why they differ:** `compare` is the native interface — `0/2/4` by verdict (or `0/1/2/4` severity-aware), with invalid invocations exiting `64` so a usage error is never mistaken for an ABI verdict. `compat` mirrors `abi-compliance-checker` exit codes (0/1/2) so existing ABICC CI scripts work without changes.

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
`compare-release` command (folded into `compare` per ADR-037 D7; still selectable
as the GitHub Action's `mode: compare-release`). By default a set/release
comparison uses the verdict-based scheme below, plus a dedicated code for
removed libraries:

| Exit code | Meaning |
|-----------|---------|
| `0` | All libraries compatible (no API/ABI break) |
| `2` | Worst verdict is `API_BREAK` |
| `4` | Worst verdict is `BREAKING`, **or** an operational `ERROR` (a library failed to dump/extract/compare) |
| `8` | A library was removed between releases and `--fail-on-removed-library` is set — takes precedence over every other code |

On the release path the severity-aware code (`0/1/2/4`) replaces the
verdict-based `2/4` mapping only when a severity *map* is actually in effect —
that is, any `--severity-*` flag is passed **or** `.abicheck.yml` carries a
`severity:` block (a preset or per-category levels). Setting `exit_code_scheme:
severity` on its own is **not** enough for directory/package inputs: with no
severity values to apply, the fan-out has nothing to score against and falls
back to the legacy verdict mapping. Exit `8` still wins, and an operational
`ERROR` still floors the exit at `4`. (`--exit-code-scheme` is rejected on
directory/package inputs; pin the legacy scheme in config with
`exit_code_scheme: legacy` if you want to force it.) One consequence worth
gating on: with an effective severity map, a release whose worst verdict is
`BREAKING` can still exit `0` if that map downgrades ABI breaks (e.g.
`abi_breaking: warning`) — parse the `verdict` from JSON output if you need
scheme-independent CI behaviour.

---

## `abicheck scan`

The one-shot source-intelligence scan has its own contract (it may compare
against a `--baseline` and adds a budget guard):

| Exit code | Meaning |
|-----------|---------|
| `0` | Compatible (or advisory-only findings) |
| `2` | Source-level / API break (incl. `API_BREAK` cross-source findings) |
| `4` | ABI break (from the `--baseline` comparison) |
| `5` | `--budget` overflow — the time guard tripped (scope is never silently shrunk) |

> Exit `5` is unique to `scan`: `--budget 15m` **fails** the run rather than
> quietly dropping evidence. With `--estimate` (dry-run cost probe) `scan` always
> exits `0`.

---

## `abicheck appcompat`

Uses the same exit codes as `compare`:

| Exit code | Meaning |
|-----------|---------|
| `0` | `COMPATIBLE` or `NO_CHANGE` — application is safe with the new library |
| `1` | Tool/runtime error (tool failure, invalid input, or unexpected exception) |
| `2` | `API_BREAK` — source-level break affecting app's symbols |
| `4` | `BREAKING` — binary ABI break or missing symbols |

> **`BREAKING` (exit 4)** is also returned when the application requires symbols or
> ELF version tags that are absent from the new library — even if the library
> diff itself is compatible — because the application would fail to load.

---

## `abicheck deps tree`

| Exit code | Meaning |
|-----------|---------|
| `0` | All dependencies resolved, all required symbols bound |
| `1` | Missing dependencies or unresolved symbols (binary would fail to load) |

---

## `abicheck deps compare`

| Exit code | Verdict | Meaning |
|-----------|---------|---------|
| `0` | `PASS` | Binary loads and no harmful ABI changes |
| `1` | `WARN` | Binary loads but ABI risk detected in dependencies |
| `4` | `FAIL` | Load failure or binary ABI break in dependencies |

### CI gate patterns

```bash
# Full-stack check: fail on FAIL, warn on WARN
abicheck deps compare usr/bin/myapp --baseline /old-root --candidate /new-root
ret=$?
[ $ret -eq 4 ] && echo "FAIL — load failure or ABI break" && exit 1
[ $ret -eq 1 ] && echo "WARN — ABI risk detected" && exit 1
[ $ret -ne 0 ] && echo "ERROR — unexpected non-verdict exit code: $ret" && exit 1
echo "PASS"

# Permissive: only fail on load failure / ABI break
abicheck deps compare usr/bin/myapp --baseline /old-root --candidate /new-root
ret=$?
[ $ret -eq 4 ] && exit 1   # FAIL only; WARN (exit 1) treated as OK
[ $ret -ne 0 ] && [ $ret -ne 1 ] && exit 1   # fail closed on non-verdict errors
exit 0
```

---

## `abicheck inputs validate`

Validates a Flow-2 `abicheck_inputs/` pack (ADR-038 C.8) before it is folded
into an authoritative baseline.

| Exit code | Meaning |
|-----------|---------|
| `0` | Clean — no issues found |
| `1` | Warnings only (e.g. an incomplete mandatory fact family, no fact-set identity reported) |
| `2` | Validation errors (e.g. a fact-set version mismatch, duplicate TU identities) |
| `64` | `PACK` is not a readable Flow-2 pack (usage error) |

---

## `abicheck inputs compact`

Merges a Flow-2 `abicheck_inputs/` pack's many per-TU `source_facts/*.jsonl`
files into one, optionally gzip-compressed (ADR-038 C.9). A post-build size/
transfer optimization; never changes the decoded facts a later `merge`/
`inputs validate` sees.

| Exit code | Meaning |
|-----------|---------|
| `0` | Success |
| `64` | `PACK` is not a readable Flow-2 pack (usage error) |

---

## `abicheck debian-symbols`

### `debian-symbols generate`

| Exit code | Meaning |
|-----------|---------|
| `0` | Symbols file generated successfully |
| `1` | Error (binary not found, ELF parse error, I/O failure) |

### `debian-symbols validate`

| Exit code | Meaning |
|-----------|---------|
| `0` | Symbols file matches the binary (all required symbols present) |
| `2` | Mismatch — one or more required symbols are missing from the binary |

> Symbols tagged `(optional)` are not required — their absence does not cause
> exit code `2`. This matches `dpkg-gensymbols` behaviour.

New symbols found in the binary but not listed in the symbols file are reported
in the output but do **not** change the exit code.

### `debian-symbols diff`

| Exit code | Meaning |
|-----------|---------|
| `0` | Diff computed successfully (regardless of whether changes were found) |
| `1` | Error (file not found, parse error) |

### CI gate patterns

```bash
# Update symbols file when library changes
abicheck debian-symbols generate ./build/libfoo.so \
    --package libfoo1 --version "$(dpkg-parsechangelog -SVersion)" \
    -o debian/libfoo1.symbols

# Validate symbols file in CI (fail on missing symbols)
abicheck debian-symbols validate ./build/libfoo.so debian/libfoo1.symbols
ret=$?
[ $ret -eq 2 ] && echo "FAIL — symbols file needs updating" && exit 1
echo "OK — symbols file matches binary"

# Diff before/after to see what changed
abicheck debian-symbols diff old.symbols new.symbols
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

## Summary table

| Verdict / State | `compare` exit (legacy) | `compare` exit (severity) | `appcompat` exit | `deps tree` exit | `deps compare` exit | `debian-symbols validate` exit | `compat` exit |
|-----------------|------------------------|--------------------------|-----------------|-------------|-------------------|-------------------------------|---------------|
| `NO_CHANGE` / `PASS` | `0` | `0` | `0` | `0` | `0` | `0` | `0` |
| `COMPATIBLE` | `0` | `0` | `0` | — | — | — | `0` |
| `COMPATIBLE_WITH_RISK` | `0` | `0`–`2`* | `0` | — | — | — | `0` |
| Additions only | `0` | `0`–`1`* | n/a | — | — | — | n/a |
| Quality issues only | `0` | `0`–`1`* | n/a | — | — | — | n/a |
| `WARN` (ABI risk) | — | — | — | — | `1` | — | — |
| `API_BREAK` | `2` | `0`–`2`* | `2` | — | — | — | `2` |
| `BREAKING` / `FAIL` | `4` | `4` | `4` | — | `4` | — | `1` |
| Missing symbols | — | — | — | — | — | `2` | — |
| Load failure | — | — | — | `1` | `4` | — | — |
| Invalid invocation / tool error | `64`† | `64`† | `1` | — | — | `1` | `3/4/5/6/7/8/10/11` |

\* Severity exit codes depend on the configuration. For example, with
`--severity-addition error`, additions exit `1`; with `--severity-preset
info-only`, everything exits `0`.

† `compare` (and `appcompat`) exit `64` for an invalid invocation — bad
arguments/options or an unreadable/unrecognised input — deliberately outside the
`0/2/4` verdict space so a usage error is never mistaken for an ABI verdict. To
reliably distinguish verdicts from errors in a script, use `--format json` and
read the `verdict` field.

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
