# Suppressions

`abicheck compare` and `abicheck compat` support YAML suppressions via `--suppress`.

Use suppressions to silence known/accepted changes while keeping detection enabled.

> Suppressions are step 2 of the CI gating pipeline (classify → suppress →
> severity → exit code): suppressed changes are removed *before* the verdict
> and severity counts are computed. See [CI Gating](ci-gating.md) for how they
> combine with policies, severity, and baselines.

---

## File format

```yaml
version: 1
suppressions:
  - symbol: _ZN3Foo3barEv
    reason: "Known internal API drift"

  - symbol_pattern: "_ZN3Foo.*"
    change_kind: func_added
    label: internal

  - type_pattern: "dnnl_.*"
    change_kind: enum_member_added
    label: oneDNN-enum-growth

  - source_location: "*/internal/*"
    reason: "Do not gate on internal headers"

  - symbol_pattern: "_ZN4dnnl4impl.*"
    source_location: "*/dnnl.h"
    expires: 2026-12-31
    label: temporary
    reason: "Temporary waiver until downstream migration"
```

---

## Supported keys per rule

| Key | Type | Description |
|-----|------|-------------|
| `symbol` | string | Exact symbol match |
| `symbol_pattern` | regex string | Fullmatch regex against symbol |
| `type_pattern` | regex string | Fullmatch regex for type-level changes |
| `change_kind` | string | Restrict suppression to a specific change kind |
| `source_location` | glob string | `fnmatch`-style match against `change.source_location` |
| `label` | string | Optional grouping tag |
| `expires` | date/datetime | Expiry date; expired rule is ignored |
| `reason` | string | Human-readable rationale |

`symbol`, `symbol_pattern`, and `type_pattern` are mutually exclusive.
At least one selector is required (`symbol`/`symbol_pattern`/`type_pattern`/`source_location`).

---

## Matching semantics

Rules are evaluated with **AND** logic:

- if `source_location` is present, location must match;
- if `symbol` or `symbol_pattern` is present, symbol must match;
- if `type_pattern` is present, change must be a type-level change and pattern must match;
- if `change_kind` is present, kind must match.

So `source_location` does **not** bypass symbol/type selectors.

---

## Expiry behavior

- `expires` accepts ISO date (`2026-06-01`) and YAML datetime values.
- Datetime values are normalized to date for safe comparisons.
- Expired rules do not apply.

---

## CLI usage

```bash
abicheck compare old.so new.so \
  --header old=include/v1/ \
  --header new=include/v2/ \
  --suppress suppressions.yaml
```

For ABICC-compatible mode:

```bash
abicheck compat -lib libfoo.so -old old.dump -new new.dump --suppress suppressions.yaml
```

---

## Suppression lifecycle enforcement

Suppression files solve an immediate problem — unblocking CI when a known change is
intentional — but left unmanaged they become a liability. Rules accumulate, reasons
are forgotten, and stale suppressions silently hide real regressions.

The lifecycle flags below turn suppressions into a managed process: require
justification for each rule, and force periodic review through expiry
enforcement.

### Typical workflow

```
1. Detect     abicheck compare old.so new.so --format json -o diff.json
2. Author     Write candidates.yml by hand from the diff (see File format above),
              filling in reason fields and expiry dates
3. Enforce    abicheck compare old.so new.so --suppress candidates.yml \
                --strict-suppressions --require-justification
```

### Requiring justification (`--require-justification`)

In team environments, every suppression should explain *why* a breaking change
is acceptable. The `--require-justification` flag enforces this at load time:

```bash
abicheck compare old.so new.so \
  --suppress suppressions.yaml \
  --require-justification
```

If any rule has an empty or missing `reason` field, the command fails immediately:

```
Error: Invalid value for '--suppress': Suppression rule 3 has no 'reason' field.
All suppression rules must include a justification when --require-justification is set.
```

This pairs well with a hand-authored candidate file that starts with empty
`reason` fields: `--require-justification` will fail the run until every rule
is reviewed and filled in.

### Failing on expired suppressions (`--strict-suppressions`)

The `--strict-suppressions` flag turns expired rules from silent no-ops into hard
failures. Without it, an expired rule simply stops matching (the underlying change
reappears in the report). With it, the command fails before comparison even runs:

```bash
abicheck compare old.so new.so \
  --suppress suppressions.yaml \
  --strict-suppressions
```

If any rule is past its `expires` date:

```
Error: ERROR: 2 expired suppression rule(s) found in suppressions.yaml:
  Rule 2: symbol_pattern="_ZN3foo.*Internal.*" expired on 2026-01-15
  Rule 5: symbol="_ZN3bar6legacyEv" expired on 2026-03-01
Remove or renew expired rules before proceeding.
```

This prevents stale suppressions from accumulating. When a rule expires, the team
must explicitly decide: remove it (the change is no longer expected), or renew it
with an updated expiry and reason.

Both `--strict-suppressions` and `--require-justification` work on `compare`
(single-library and bundle/package inputs).

### Recommended CI configuration

For CI pipelines, combine both features:

```bash
# Author suppressions.yaml by hand (see File format above), then
# gate CI with strict lifecycle enforcement:
abicheck compare old.so new.so -H include/ \
  --suppress suppressions.yaml \
  --strict-suppressions \
  --require-justification
```

This ensures that:

1. Every suppression has a documented reason (audit trail).
2. No suppression lives forever without review (expiry enforcement).
3. Expired rules are not silently ignored — they break the build, forcing action.
