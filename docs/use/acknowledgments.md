---
doc_type: how-to
audience:
  - library-maintainer
  - ci-owner
level: intermediate
canonical_for:
  - change-acknowledgment
lifecycle: active
generated: false
---

# Change acknowledgment

`checker.compare(acknowledgments=...)` (the typed Python API) accepts a
loaded set of **acknowledgment records** — an explicit, reviewable statement
that a specific, already-detected change was seen and intentionally
accepted, as opposed to a [suppression](suppressions.md), which claims the
finding is a false positive or out of scope.

**Engine-level only today: no native CLI flag yet.** Records are loaded with
`AcknowledgmentList.load(path)` and passed to `checker.compare(acknowledgments=...)`
through the typed Python API. `abicheck compare`/`abicheck scan` do not yet have
a `--acknowledgments PATH` flag to load a document from a run's CLI invocation —
until that front-end wiring lands, this mechanism is reachable only from code
calling the Python API directly.

> Acknowledgment is **not** suppression. A suppressed finding disappears
> from the report and the gate before the verdict is computed. An
> acknowledged finding keeps its verdict class, stays in the report, and
> still contributes to the gate according to policy — acknowledging a
> breaking change never pretends it is compatible. See
> [Disposition audit](disposition-audit.md) for how the two dispositions are
> reported side by side.

---

## Why a separate mechanism from suppression

[`vision.md`](../contribute/vision.md)'s change-governance model draws this
line explicitly: *"Changes can be acknowledged with explicit, reviewable
context bounded to specific findings, components, and release ranges... A
baseline refresh or a broad ignore rule is not an acknowledgment of
everything it happens to cover."*

Concretely, an acknowledgment record:

- must name one **specific finding** (its canonical `finding_id`, or an
  exact `symbol`) — never a pattern, a namespace glob, or a source-location
  glob. A rule using one of those broader selectors is a suppression, not an
  acknowledgment, and the loader rejects it outright rather than silently
  accepting an over-broad "acknowledgment";
- carries a **required, non-empty `reason`** — an acknowledgment with no
  stated reason is exactly the kind of accidental broad acceptance the
  vision's invariant warns against;
- may be scoped to a **component** and a **release range** (`baseline`/
  `candidate` version labels, the same labels
  [longitudinal history](../contribute/adr/066-longitudinal-history-and-versioning-policy.md)
  tracks) — a record that names a component/candidate a run does not supply
  never matches; it is never resolved "to the nearest" acknowledgment.

An **ambiguous match** — more than one loaded record matching the same
change — is a hard error, not a silently-resolved pick: D5 of
[ADR-067](../contribute/adr/067-change-intent-acknowledgment-and-disposition-audit.md)
requires review in that case.

## File format

```yaml
version: 1
acknowledgments:
  - symbol: "_ZN3Foo6removeEv"
    component: libfoo
    candidate: "2.0.0"
    reason: "Deliberate removal — replaced by Foo::erase(); see #482"
    reference: "https://github.com/example/libfoo/issues/482"
    expires: 2026-12-31

  - finding_id: "e99c3be122c2ddf3"
    reason: "Planned public addition for the 2.0 release"
```

Same YAML envelope and loader machinery as
[suppressions](suppressions.md#file-format) (`version: 1`, one top-level
list key) — but a narrower key set: `finding_id`, `symbol`, `change_kind`,
`component`, `baseline`, `candidate`, `reason`, `reference`, `expires`. Any
suppression-only broad-selector key (`symbol_pattern`, `type_pattern`,
`namespace`, `entity_namespace`, `cause_namespace`, `source_location`,
`member_name`, `binding`) is a load error.

## The additions review gate

A project may configure whether an **unacknowledged public addition**
should be flagged, via the `--policy` document's `acknowledgment:` block:

```yaml
acknowledgment:
  unacknowledged_additions: allow   # allow (default) | warn | block
```

- `allow` (the default): no existing run changes.
- `warn`: every unacknowledged public addition is listed in the
  `disposition_audit.unacknowledged_additions_review` report block, but the
  exit code is unaffected.
- `block`: the same list contributes an orthogonal `1` to the exit code —
  raising a clean `0` to `1`, never lowering a real ABI/API-break exit `2`/
  `4` — the same fold [contract coverage](contract-evaluation.md) and
  [analysis assurance](../reference/exit-codes.md) already use. This never
  reclassifies the addition itself: its `ChangeKind` and verdict class are
  untouched either way.

## What the report shows

Every acknowledged finding's record id and the additions-review result
appear in the `disposition_audit` report block — see
[Disposition audit](disposition-audit.md) for the full shape.
