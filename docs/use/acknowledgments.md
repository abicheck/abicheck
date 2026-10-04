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

`compare` reads **acknowledgment records** — explicit, reviewable statements
that a specific, already-detected change was seen and intentionally accepted —
from the file `.abicheck.yml`'s `acknowledgment:` block names. That is unlike
a [suppression](suppressions.md), which claims the finding is a false positive
or out of scope.

```yaml
# .abicheck.yml
acknowledgment:
  file: abi/acknowledgments.yml      # the records (format below)
  unacknowledged_additions: block    # allow (default) | warn | block
```

A relative `file` resolves against the project root.

**The records load only from a config named with `--config`.** A record
accepts a finding, which can turn a `block` exit back into `0`. An
auto-discovered `.abicheck.yml` is one the pull request under review can
edit, so its `file` is noted on stderr and not loaded, the same trust
boundary [`contract.overlays`](../reference/config-file.md#contract) has.
`unacknowledged_additions` applies from any config, because `warn`/`block`
can only add to what a run reports.

This applies to a single-pair `compare` and to a directory/package
comparison, where every library is reviewed against the same records and the
release exits `1` if any library has an unacknowledged addition under `block`.
From the typed Python API, set `CompareRequest(acknowledgments_path=...,
acknowledgment_unacknowledged_additions="block")`; a `policy_file_path`
document's own `acknowledgment:` block outranks the second field, as on the
CLI.

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
  longitudinal history (`abicheck project history`) tracks) — a record that names a component/candidate a run does not supply
  never matches; it is never resolved "to the nearest" acknowledgment.

An **ambiguous match** — more than one loaded record matching the same
change — is a hard error, not a silently-resolved pick: that case requires review.

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

A project configures whether an **unacknowledged public addition** is
flagged with `acknowledgment.unacknowledged_additions` in `.abicheck.yml`
(above). A `--policy` document may state the same setting in its own
`acknowledgment:` block; when it does, it wins over `.abicheck.yml`. With a
gate set and no `file`, nothing is acknowledged, so every public addition
counts.

- `allow` (the default): no existing run changes.
- `warn`: every unacknowledged public addition is listed in the
  `disposition_audit.unacknowledged_additions_review` report block, but the
  exit code is unaffected.
- `block`: the same list contributes an orthogonal `1` to the exit code —
  raising a clean `0` to `1`, never lowering a real ABI/API-break exit `2`/
  `4` — the same fold [contract coverage](contract-evaluation.md) and
  [analysis assurance](../reference/exit-codes.md) already use. The report's
  `exit` block names it as `additions_review_contribution` (reason
  `additions_review`), and the GitHub Action publishes the verdict
  `ADDITIONS_UNACKNOWLEDGED` when it is the only thing that gated the run. This never
  reclassifies the addition itself: its `ChangeKind` and verdict class are
  untouched either way.

## What the report shows

Every acknowledged finding's record id and the additions-review result
appear in the `disposition_audit` report block — see
[Disposition audit](disposition-audit.md) for the full shape.
