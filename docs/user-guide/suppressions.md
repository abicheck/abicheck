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

  - member_name: "value_type"
    reason: "Nested typedef churn, any container"

  - namespace: "oneapi::dal::**::detail::**"
    reason: "Private implementation details"
    # reachability defaults to "unreachable-only" for a namespace rule — see
    # "Reachability-aware suppression" below. This rule will NOT hide a
    # detail:: change that turns out to be part of the effective public ABI.
```

---

## Supported keys per rule

| Key | Type | Description |
|-----|------|-------------|
| `symbol` | string | Exact symbol match |
| `symbol_pattern` | regex string | Fullmatch regex against symbol |
| `type_pattern` | regex string | Fullmatch regex for type-level changes |
| `member_name` | regex string | Fullmatch regex against the last `::`-segment of the symbol |
| `change_kind` | string | Restrict suppression to a specific change kind |
| `source_location` | glob string | `fnmatch`-style match against `change.source_location` |
| `namespace` (alias: `entity_namespace`) | glob string | Match the change's own `symbol`/qualified name against a `::`-namespace glob (`**` = any depth) |
| `cause_namespace` | glob string | Match the change's `caused_by_type` (its documented *cause*, when different from its own subject) against a namespace glob |
| `reachability` | `unreachable-only` \| `any` \| `public-only` \| `proven-unreachable-only` | Gates whether this rule may match a change that is part of the effective public ABI — see below. Default depends on the selector shape. |
| `allow_public_break` | bool | Required, for a **broad** rule (`namespace`/`source_location`), to suppress a change that is both public-reachable and classified `BREAKING`/`API_BREAK`. Not required for a narrow rule (`symbol`/`symbol_pattern`/`type_pattern`/`member_name`) — naming one exact symbol is already the deliberate, audited action. |
| `allow_unknown_reachability` | bool | Only meaningful with `reachability: proven-unreachable-only` — permits the rule to also match a change whose reachability could not be positively proven or disproven (see below). |
| `label` | string | Optional grouping tag |
| `expires` | date/datetime | Expiry date; expired rule is ignored |
| `reason` | string | Human-readable rationale |

`symbol`, `symbol_pattern`, and `type_pattern` are mutually exclusive.
`namespace` and `entity_namespace` are aliases for the same selector — specify
only one. At least one selector is required (`symbol`/`symbol_pattern`/
`type_pattern`/`member_name`/`source_location`/`namespace`/`cause_namespace`).

---

## Matching semantics

Rules are evaluated with **AND** logic:

- if `source_location` is present, location must match;
- if `member_name` is present, the symbol's last `::`-segment must match;
- if `namespace`/`entity_namespace` is present, the change's own symbol/qualified
  name must lie in that namespace;
- if `cause_namespace` is present, the change's `caused_by_type` must lie in
  that namespace;
- if `symbol` or `symbol_pattern` is present, symbol must match;
- if `type_pattern` is present, change must be a type-level change and pattern must match;
- if `change_kind` is present, kind must match.

So `source_location` does **not** bypass symbol/type selectors.

**`namespace` matches only the change's own identity, never its cause.**
A finding's `symbol` is its own subject; a derived finding's `caused_by_type`
names a *different* entity responsible for it (e.g. a public function whose
signature changed because an internal type it depends on changed). A
`namespace` rule aimed at hiding churn *inside* an internal namespace must
not also hide an unrelated *public* finding merely because its documented
cause happens to live there — use `cause_namespace` for that instead:

```yaml
# Suppresses churn ON internal::Foo itself.
- namespace: "myns::internal::*"

# Suppresses a finding CAUSED BY something in internal::, regardless of the
# finding's own (possibly public) subject. Use deliberately — see below.
- cause_namespace: "myns::internal::*"
```

---

## Reachability-aware suppression

A broad `namespace`/`source_location` rule can accidentally match an internal
symbol that is not actually private to the library's compatibility contract —
one a public inline/template function, a public type's field or base class,
a public function signature, or (given an embedded L5 source/call graph,
ADR-044 P1) a public inline/template function's own *body* depends on. abicheck
computes this reachability — both the type-layout walk `internal_leak.py`'s
leak detector uses, and, when build/source evidence is present, the L5
call-graph walk described in
[the ABI guide § The L5 graph](../concepts/abi-api-handling.md#the-l5-graph-reachability-not-just-structure)
— *before* suppression runs, and a rule's `reachability` setting decides
whether it may still apply:

| Value | Meaning |
|-------|---------|
| `unreachable-only` | The rule will not match a change that is part of the effective public ABI. **Default** for a rule using only broad selectors (`namespace`/`entity_namespace`/`cause_namespace`/`source_location`). |
| `any` | No reachability filtering — matches regardless. **Default** for a rule using a narrow selector (`symbol`, `symbol_pattern`, `type_pattern`, `member_name`) — naming one exact symbol/type is already an audited decision, so behavior is unchanged from before this feature existed. |
| `public-only` | Inverse of `unreachable-only` — matches only a public-reachable change. Mainly useful for temporarily isolating leak findings while investigating them. |
| `proven-unreachable-only` | A stricter opt-in variant of `unreachable-only` — see "Proven vs. unknown reachability" below. |

### Proven vs. unknown reachability

`unreachable-only`'s default gate keys off a single boolean
(`change.public_reachable`): a change is either public-reachable or it is
not. That collapses two different situations into the same "not reachable"
answer — the walk positively examined this change and found no path to the
public surface, versus no walk (or an incomplete one) ever reached a verdict
on it at all. For the type-layout walk (which enumerates every declaration
the snapshot itself knows about) that distinction rarely matters in
practice, which is why `unreachable-only` keeps its original, simpler
semantics as the default — every existing suppression file behaves exactly
as before.

For the optional embedded L5 source/call graph, the distinction can matter:
its coverage can be narrowed (restricted to a changed-paths subset) or
degraded (a collection pass hit errors but still folded in whatever it
managed to parse) — see [`docs/concepts/graph-coverage.md`](../concepts/graph-coverage.md)
for the concept. An absent edge in that kind of graph is not reliable
negative evidence.

Opt into the stricter check with `reachability: proven-unreachable-only`. It
refuses to match a change whose reachability is `unknown` — i.e. no walk
reached a verdict, or the only walk that could have (the call graph) is
itself flagged narrowed/degraded and the layout walk never examined the
change at all:

```yaml
- namespace: "myns::detail::*"
  reachability: proven-unreachable-only
  reason: "Only suppress detail:: churn once graph coverage actually proves it unreachable"
```

When such a rule's selectors match but the change's reachability is
`unknown`, the change is **not** suppressed and a
`suppression_reachability_unknown` finding is added to the report explaining
why, with the same shape as `suppression_would_hide_public_break` below. Set
`allow_unknown_reachability: true` on the rule to accept the
absence-of-evidence risk explicitly once you've manually confirmed it's
safe.

Independently of `reachability`, a **broad** rule (`namespace`/
`entity_namespace`/`cause_namespace`/`source_location`) that would suppress a
change that is **both** public-reachable **and** classified `BREAKING`/
`API_BREAK` is refused unless the rule also sets `allow_public_break: true` —
making that specific, higher-risk suppression explicit and reviewable rather
than an accident of a broad glob. A narrow rule (`symbol`/`symbol_pattern`/
`type_pattern`/`member_name`) is exempt from this check — naming one exact
symbol/type for suppression is already the deliberate action this mechanism
exists to require, regardless of whether that symbol happens to be public or
an internal type that leaks:

```yaml
- namespace: "oneapi::dal::**::detail::**"
  reason: "Reviewed: descriptor_base growth is safe, wrapper layout unchanged"
  allow_public_break: true
```

When a broad rule's selectors match a change but the match is withheld by
either gate, the change is **not** suppressed, and a
`suppression_would_hide_public_break` finding is added to the report
explaining which rule matched and why it did not apply — for example:

```text
Suppression rule 'oneapi::dal::**::detail::**' matched
'oneapi::dal::kmeans::detail::descriptor_base' (type_size_changed) but was
not applied: the symbol is public-reachable via fn:oneapi::dal::make ->
base:oneapi::dal::kmeans::detail::descriptor_base ->
oneapi::dal::kmeans::detail::descriptor_base. Add `allow_public_break: true`
to this rule to suppress it anyway.
```

This closes a specific correctness gap: without it, a suppression rule could
remove the raw evidence for an internal-type change before abicheck's
internal-leak detector had a chance to see it, silently hiding a genuine
break through the public ABI surface with no trace in the report. See
[ADR-044](../development/adr/044-reachability-aware-suppression.md) for the
full design rationale.

Both walks recognize the same private-implementation namespace convention —
`detail`/`impl`/`internal`/`__detail`/`_impl` by default, configurable per
project via the policy file's
[`internal_namespaces`](policies.md#your-projects-internal-namespace-convention-internal_namespaces)
key.

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
