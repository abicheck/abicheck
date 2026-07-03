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
  --old-header include/v1/ \
  --new-header include/v2/ \
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

The lifecycle flags below turn suppressions into a managed process: auto-generate
candidate rules from diffs, require justification for each one, and force periodic
review through expiry enforcement.

### Typical workflow

```
1. Detect     abicheck compare old.so new.so --format json -o diff.json
2. Generate   abicheck suggest-suppressions diff.json -o candidates.yml
3. Review     Edit candidates.yml: fill in reason fields, adjust expiry dates
4. Enforce    abicheck compare old.so new.so --suppress candidates.yml \
                --strict-suppressions --require-justification
```

### Auto-generating suppression candidates

When a diff produces many changes that need suppression, hand-writing rules is
tedious and error-prone. The `suggest-suppressions` command reads a JSON diff
and generates a candidate YAML file:

```bash
# Step 1: produce a JSON diff
abicheck compare old.so new.so -H include/ --format json -o diff.json

# Step 2: generate candidate rules
abicheck suggest-suppressions diff.json -o candidates.yml
```

The output includes `# TODO` comments on every `reason` field to flag rules that
need human review:

```yaml
# Auto-generated suppression candidates from abicheck compare
# Review each rule and add a justification before using
version: 1
suppressions:
  - symbol: "_ZN3foo6legacyEv"
    change_kind: "func_removed"
    reason: ""  # TODO: add justification
    expires: "2026-09-23"

  - symbol: "_ZN3foo3bazEi"
    change_kind: "func_param_type_changed"
    reason: ""  # TODO: add justification
    expires: "2026-09-23"

  - type_pattern: "MyStruct"
    change_kind: "type_size_changed"
    reason: ""  # TODO: add justification
    expires: "2026-09-23"
```

**Options:**

| Flag | Default | Description |
|------|---------|-------------|
| `-o`, `--output` | stdout | Output file for generated YAML |
| `--expiry-days N` | `180` | Days from today for the `expires` field |

**Design choices:**

- Symbol-level changes use exact `symbol` match (safer than patterns).
- Type-level changes (`type_size_changed`, `enum_member_removed`, etc.) use
  `type_pattern` so a single rule covers all member-level changes for that type.
- The default expiry is 6 months — long enough to not be noisy, short enough
  to force periodic review.

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

This pairs well with `suggest-suppressions`: the generated file has empty `reason`
fields, so it will fail `--require-justification` until every rule is reviewed.

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

For CI pipelines, combine all three features:

```bash
# Generate candidates once during development
abicheck suggest-suppressions diff.json \
  --expiry-days 90 \
  -o suppressions.yaml

# Gate CI with strict lifecycle enforcement
abicheck compare old.so new.so -H include/ \
  --suppress suppressions.yaml \
  --strict-suppressions \
  --require-justification
```

This ensures that:

1. Every suppression has a documented reason (audit trail).
2. No suppression lives forever without review (expiry enforcement).
3. Expired rules are not silently ignored — they break the build, forcing action.
