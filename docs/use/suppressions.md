---
doc_type: how-to
audience:
  - library-maintainer
  - ci-owner
level: intermediate
canonical_for:
  - suppressions
lifecycle: active
generated: false
---

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
| `binding` | `global` \| `weak` \| `local` \| `unique` \| `other` | Match the removed symbol's ELF linkage. Only set on `func_removed`/`func_removed_elf_only`/`var_removed`/`func_deleted_elf_fallback` findings. Never matches a change whose binding wasn't captured. **Conjunctive only — not a standalone selector**: like `member_name`, `binding` alone does not satisfy the "at least one selector" requirement below, so it must be combined with `symbol`/`symbol_pattern`/`type_pattern`/`source_location`/`namespace`/`cause_namespace`. **Provider-side evidence only — not proof a removal is safe.** `WEAK` linkage means the *library's own build* used vague/COMDAT linkage for this symbol (e.g. an in-class-defined/`inline` member), which usually — but not always — means every consumer already carries its own copy. The known counterexample: a public header declaring `extern template struct Box<int>;` tells consumer TUs *not* to instantiate, while the library's own explicit instantiation still emits a `WEAK`/COMDAT definition, so a consumer can hold an undefined reference to a symbol still reported as `WEAK` here. Confirm the removed symbol isn't `extern template`/explicit-instantiation surface (or otherwise known to have real out-of-library callers) before suppressing on `binding: weak` alone. |
| `finding_id` | string | Exact match against a change's `canonical_finding_id` (report schema 2.36) — copy the value straight out of a `compare --format json`/`scan --against --format json` report. **Backend-independent**, unlike `symbol`/`type_pattern`: it's `finding_identity.resolve_change_identity()`'s producer-agnostic identity (mangled-symbol tier when available), which always excludes `source_location` and normalizes any embedded type spelling (e.g. `char const*` vs. `char const *`) before hashing rather than folding it in raw — fields/spellings CastXML and Clang aren't guaranteed to produce identically — so a rule minted from one header backend's report reliably matches the equivalent finding reported by the other. Not the same value as the report's own `finding_id` field, which folds in raw `source_location`/`description` and is meant for correlating two runs of the *same* comparison, not for surviving an `--ast-frontend` switch. A **standalone-sufficient** selector — like `symbol`, it alone satisfies the "at least one selector" requirement, and counts as narrow for `allow_public_break`'s gate (naming a specific finding by id is already the deliberate, audited action). |
| `reachability` | `unreachable-only` \| `any` \| `public-only` \| `proven-unreachable-only` | Gates whether this rule may match a change that is part of the effective public ABI — see below. Default depends on the selector shape. |
| `allow_public_break` | bool | Required, for a **broad** rule (`namespace`/`source_location`), to suppress a change that is both public-reachable and classified `BREAKING`/`API_BREAK`. Not required for a narrow rule (`symbol`/`symbol_pattern`/`type_pattern`/`member_name`/`finding_id`) — naming one exact symbol (or finding) is already the deliberate, audited action. |
| `allow_unknown_reachability` | bool | Only meaningful with `reachability: proven-unreachable-only` — permits the rule to also match a change whose reachability could not be positively proven or disproven (see below). |
| `label` | string | Optional grouping tag |
| `expires` | date/datetime | Expiry date; expired rule is ignored |
| `reason` | string | Human-readable rationale |

`symbol`, `symbol_pattern`, and `type_pattern` are mutually exclusive.
`namespace` and `entity_namespace` are aliases for the same selector — specify
only one. At least one selector is required (`symbol`/`symbol_pattern`/
`type_pattern`/`member_name`/`source_location`/`namespace`/`cause_namespace`/
`finding_id`).

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
[Unified Impact Assessment](../learn/impact-analysis.md)
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
managed to parse) — see [`docs/learn/graph-coverage.md`](../learn/graph-coverage.md)
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
[ADR-044](../contribute/adr/044-reachability-aware-suppression.md) for the
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
                suppression.strict: true suppression.require_justification: true
```

### Requiring justification (`suppression.require_justification: true`)

In team environments, every suppression should explain *why* a breaking change
is acceptable. `.abicheck.yml`'s `suppression.require_justification: true`
enforces this at load time:

```yaml
# .abicheck.yml
suppression:
  require_justification: true
```

```bash
abicheck compare old.so new.so --suppress suppressions.yaml
```

If any rule has an empty or missing `reason` field, the command fails immediately:

```
Error: Invalid value for '--suppress': Suppression rule 3 has no 'reason' field.
All suppression rules must include a justification when suppression.require_justification: true is set.
```

This pairs well with a hand-authored candidate file that starts with empty
`reason` fields: `suppression.require_justification: true` will fail the run until every rule
is reviewed and filled in.

### Failing on expired suppressions (`suppression.strict: true`)

The `suppression.strict: true` flag turns expired rules from silent no-ops into hard
failures. Without it, an expired rule simply stops matching (the underlying change
reappears in the report). With it, the command fails before comparison even runs:

```yaml
# .abicheck.yml
suppression:
  strict: true
```

```bash
abicheck compare old.so new.so --suppress suppressions.yaml
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

Both `suppression.strict: true` and `suppression.require_justification: true` work on `compare`
(single-library and bundle/package inputs).

### Recommended CI configuration

For CI pipelines, combine both features:

```yaml
# .abicheck.yml — strict lifecycle enforcement
suppression:
  strict: true
  require_justification: true
```

```bash
# Author suppressions.yaml by hand (see File format above), then gate CI:
abicheck compare old.so new.so -H include/ --suppress suppressions.yaml
```

This ensures that:

1. Every suppression has a documented reason (audit trail).
2. No suppression lives forever without review (expiry enforcement).
3. Expired rules are not silently ignored — they break the build, forcing action.

## What the report says about a suppressed finding

A suppression never removes a finding from the run's own accounting. Every
report projection carries a **disposition audit** (report schema 2.50): the
*detected* total (every change the detectors found, before any rule applied),
the *effective* (gating) total, and the count in each terminal disposition —
`gating`, `non_gating`, `suppressed`, `out_of_contract`,
`unresolved_relevance`, `deduplicated`. The counts sum to the detected total,
so "100 removals detected, 100 suppressed by rule X" stays visible on a
passing run:

```console
$ abicheck compare old.so new.so --suppress suppressions.yaml --profile quick
NO_CHANGE: no changes (0 total) [audit: 100 detected, 0 gating, 100 suppressed]
```

The JSON report's `disposition_audit` block carries the same counts plus the
rules that produced them, and each `suppression.suppressed_changes[]` entry
records the rule that actually hid it — its selector identity, the suppression
document's path, its `reason`, `label` and `expires`. The audit is derived
from the run's conserved change ledger rather than from the post-suppression
change list, so a rule cannot hide its own audit record.

One consequence worth knowing before writing a broad
`allow_public_break: true` rule: the release recommendation
(`abicheck compare`'s "Recommended release" line) reads that conserved ledger
too. A
suppressed ABI/API break is reported as a `major`-class finding needing
review — *"suppressed (intent: unspecified), not compatible"* — never as "no
version bump required". A suppression records that a finding was withheld; it
is not evidence that the finding was wrong.
