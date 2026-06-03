# Severity Configuration

abicheck classifies every detected change into one of four **issue categories**,
each with a configurable severity level that controls exit codes and report
presentation.

---

## Issue categories

Every `ChangeKind` is assigned to exactly one of these categories:

| Category | What it covers | Default severity |
|----------|---------------|------------------|
| **abi_breaking** | Clear ABI/API incompatibilities (e.g. `func_removed`, `type_size_changed`) | `error` |
| **potential_breaking** | Source-level breaks and deployment risk (e.g. `enum_member_renamed`, `symbol_version_required_added`) | `warning` |
| **quality_issues** | Problematic behaviors that don't break compatibility (e.g. `visibility_leak`, `soname_missing`, `func_noexcept_added`) | `warning` |
| **addition** | New public API surface (e.g. `func_added`, `type_added`, `enum_member_added`) | `info` |

The category assignment is based on canonical kind sets defined in
`checker_policy.py` and respects the active `--policy` (e.g. `sdk_vendor`
downgrades `enum_member_renamed` from `potential_breaking` to `quality_issues`).

## Severity levels

Each category can be set to one of three levels:

| Level | Report presentation | Exit code impact |
|-------|-------------------|------------------|
| `error` | Flagged prominently with badge | Contributes to non-zero exit code |
| `warning` | Shown as a warning with badge | Does **not** affect exit code |
| `info` | Informational, neutral | Does **not** affect exit code |

## CLI options

### Presets

```bash
# Use the default preset (explicit)
abicheck compare old.json new.json --severity-preset default

# Strict: everything is an error (exits non-zero on any finding)
abicheck compare old.json new.json --severity-preset strict

# Info-only: purely informational (always exits 0)
abicheck compare old.json new.json --severity-preset info-only
```

### Per-category overrides

Override individual categories on top of a preset:

```bash
# Default preset but fail on API additions too
abicheck compare old.json new.json --severity-addition error

# Strict preset but ignore quality issues
abicheck compare old.json new.json --severity-preset strict --severity-quality-issues info

# Only fail on binary ABI breaks, everything else informational
abicheck compare old.json new.json --severity-preset info-only --severity-abi-breaking error
```

Available flags:
- `--severity-abi-breaking {error,warning,info}`
- `--severity-potential-breaking {error,warning,info}`
- `--severity-quality-issues {error,warning,info}`
- `--severity-addition {error,warning,info}`

### Presets reference

| Preset | `abi_breaking` | `potential_breaking` | `quality_issues` | `addition` |
|--------|---------------|---------------------|------------------|-----------|
| `default` | error | warning | warning | info |
| `strict` | error | error | error | error |
| `info-only` | info | info | info | info |

## Exit codes

When any `--severity-*` flag is provided, the exit code is computed from the
severity configuration instead of the legacy verdict system:

| Exit code | Meaning |
|-----------|---------|
| `0` | No error-level findings |
| `1` | Error-level findings in `addition` or `quality_issues` only |
| `2` | Error-level findings in `potential_breaking` (but not `abi_breaking`) |
| `4` | Error-level findings in `abi_breaking` |

The highest applicable code wins. Without any `--severity-*` flag, the legacy
verdict-based exit codes apply (see [exit codes reference](../reference/exit-codes.md)).

## Report output

### Markdown

When severity is configured, the markdown report includes:

1. **Severity Configuration table** — shows the configured level, finding count,
   and exit impact for each category.
2. **Section badges** — each change section header includes the severity level
   badge (e.g. `## ❌ Breaking Changes ❌ \`ERROR\``).

### JSON

The JSON output includes a `severity` object when severity is configured:

```json
{
  "severity": {
    "config": {
      "abi_breaking": "error",
      "potential_breaking": "warning",
      "quality_issues": "warning",
      "addition": "info"
    },
    "categories": {
      "abi_breaking": {"severity": "error", "count": 2},
      "potential_breaking": {"severity": "warning", "count": 1},
      "quality_issues": {"severity": "warning", "count": 0},
      "addition": {"severity": "info", "count": 3}
    },
    "exit_code": 4
  }
}
```

## Policy interaction

The severity system respects the active policy (`--policy`). For example,
under `sdk_vendor`, kinds that are downgraded from `API_BREAK` to `COMPATIBLE`
are reclassified from `potential_breaking` to `quality_issues` or `addition`
accordingly.

This means `--policy sdk_vendor --severity-preset default` will **not** exit
non-zero for changes that the `sdk_vendor` policy downgrades — for example,
kinds moved from `potential_breaking` to `quality_issues` or `addition` are
demoted to `warning` or `info` under the `default` preset. However, the
`strict` preset maps **all** categories (including `quality_issues` and
`addition`) to `error`, so `--policy sdk_vendor --severity-preset strict` will
still exit non-zero for any detected changes, even those the policy downgrades.

## GitHub Action

The GitHub Action supports severity configuration via inputs:

```yaml
- uses: napetrov/abicheck@v0.3.0
  with:
    old-library: libfoo-v1.json
    new-library: libfoo-v2.json
    severity-addition: error        # fail on new API additions
    # severity-preset: strict       # or use a preset
```

For arbitrary `--severity-*` overrides not exposed as inputs, use `extra-args`:

```yaml
- uses: napetrov/abicheck@v0.3.0
  with:
    old-library: libfoo-v1.json
    new-library: libfoo-v2.json
    extra-args: '--severity-quality-issues error --severity-potential-breaking info'
```
