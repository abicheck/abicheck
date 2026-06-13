# Exit Codes

`abicheck` uses different exit codes for each command family.

**Why they differ:** `compare` is the native interface with a wider exit code range (0/1/2/4) that distinguishes tool errors from API breaks from binary breaks. `compat` mirrors `abi-compliance-checker` exit codes (0/1/2) so existing ABICC CI scripts work without changes.

---

## `abicheck compare`

### Legacy exit codes (default, no `--severity-*` flags)

| Exit code | Meaning |
|-----------|---------|
| `0` | `NO_CHANGE`, `COMPATIBLE`, or `COMPATIBLE_WITH_RISK` — no binary ABI break |
| `2` | `API_BREAK` — source-level API break — recompilation required |
| `4` | `BREAKING` — binary ABI break |

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

## `abicheck compare-release`

Aggregates the worst per-library verdict across the release; exit codes mirror
`compare`, plus a dedicated code for removed libraries:

| Exit code | Meaning |
|-----------|---------|
| `0` | All libraries compatible (no API/ABI break) |
| `2` | Worst verdict is `API_BREAK` |
| `4` | Worst verdict is `BREAKING`, **or** an operational `ERROR` (a library failed to dump/extract/compare) |
| `8` | A library was removed between releases and `--fail-on-removed-library` is set — takes precedence over every other code |

With any `--severity-*` flag, the severity-aware code (`0/1/2/4`) replaces the
verdict-based `2/4` mapping — except exit `8` still wins, and an operational
`ERROR` still floors the exit at `4`.

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

## `abicheck deps`

| Exit code | Meaning |
|-----------|---------|
| `0` | All dependencies resolved, all required symbols bound |
| `1` | Missing dependencies or unresolved symbols (binary would fail to load) |

---

## `abicheck stack-check`

| Exit code | Verdict | Meaning |
|-----------|---------|---------|
| `0` | `PASS` | Binary loads and no harmful ABI changes |
| `1` | `WARN` | Binary loads but ABI risk detected in dependencies |
| `4` | `FAIL` | Load failure or binary ABI break in dependencies |

### CI gate patterns

```bash
# Full-stack check: fail on FAIL, warn on WARN
abicheck stack-check usr/bin/myapp --baseline /old-root --candidate /new-root
ret=$?
[ $ret -eq 4 ] && echo "FAIL — load failure or ABI break" && exit 1
[ $ret -eq 1 ] && echo "WARN — ABI risk detected" && exit 1
echo "PASS"

# Permissive: only fail on load failure / ABI break
abicheck stack-check usr/bin/myapp --baseline /old-root --candidate /new-root
ret=$?
[ $ret -eq 4 ] && exit 1   # FAIL only; WARN (exit 1) treated as OK
exit 0
```

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

| Verdict / State | `compare` exit (legacy) | `compare` exit (severity) | `appcompat` exit | `deps` exit | `stack-check` exit | `debian-symbols validate` exit | `compat` exit |
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
| Tool error | `2`† | `2`† | `1` | — | — | `1` | `3/4/5/6/7/8/10/11` |

\* Severity exit codes depend on the configuration. For example, with
`--severity-addition error`, additions exit `1`; with `--severity-preset
info-only`, everything exits `0`.

† Click uses exit code `2` for argument/usage errors. To reliably distinguish
verdicts from tool errors, use `--format json` and read the `verdict` field.

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
