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
that a specific, already-detected change was seen and intentionally accepted.
That is unlike a [suppression](suppressions.md), which claims the finding is a
false positive or out of scope.

Records come from one of two places:

- `--acknowledgments FILE` on the command line, or the Action's
  `acknowledgments` input. This names the records for one run.
- `.abicheck.yml`'s `acknowledgments.file`, which names the records a project
  commits.

```yaml
# .abicheck.yml
acknowledgments:
  file: abi/acknowledgments.yml      # the records (format below)
  unacknowledged_additions: block    # allow (default) | warn | block
  breaks: gate                       # gate (default) | accept
```

A relative `file` resolves against the project root. `--acknowledgments`
overrides `acknowledgments.file` when both are given.

**Trust boundary.** A record can accept a finding, and `breaks: accept` can
lower a gating exit, so both are trusted only from input an operator named:

- the `--acknowledgments` flag and the Action's `acknowledgments` input are
  operator input, so they supply records even with no config at all;
- `acknowledgments.file` and `acknowledgments.breaks` apply only from an
  explicitly named `--config` (or the Action's `build-config`). An
  auto-discovered `.abicheck.yml` is one the pull request under review can edit,
  so its `file` and `breaks` are noted on stderr and not applied;
- `unacknowledged_additions` applies from any config, because `warn`/`block`
  can only add to what a run reports.

This applies to a single-pair `compare` and to a directory/package comparison,
where every library is reviewed against the same records and the release exits
`1` if any library has an unacknowledged addition under `block`.

From the typed Python API, set `CompareRequest(acknowledgments_path=...,
acknowledgment_unacknowledged_additions="block", acknowledgment_breaks="accept")`.
A `policy_file_path` document's own `acknowledgment:` block outranks the
configured settings as one whole block, on the CLI and in the API alike.

> Acknowledgment is **not** suppression. A suppressed finding disappears
> from the report and the gate before the verdict is computed. An
> acknowledged finding keeps its verdict class and stays in the report.
> Whether an acknowledged *break* still gates the exit code is the separate
> `breaks` setting below — the verdict still reads as a break either way. See
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

- must name one **specific finding** — its canonical `finding_id`, or an exact
  `symbol` together with the `change_kind` it accepts. It never takes a
  pattern, a namespace glob, or a source-location glob. A rule using one of
  those broader selectors is a suppression, not an acknowledgment, and the
  loader rejects it outright;
- carries a **required, non-empty `reason`** — an acknowledgment with no
  stated reason is exactly the kind of accidental broad acceptance the
  vision's invariant warns against;
- may be scoped to a **component** and a **release range** (`baseline`/
  `candidate` version labels, the same labels longitudinal history
  (`abicheck project history`) tracks). A record that names a component or
  candidate a run does not supply never matches; it is never resolved "to the
  nearest" acknowledgment.

Matching is strict, so an acknowledgment accepts exactly what it names:

- **A `symbol` record must name a `change_kind`.** Without one it would accept
  every change on that symbol — a return-type change and a parameter change,
  say — including ones the author never reviewed. The loader refuses it. Use
  `finding_id` to name one finding without a kind.
- **A base `change_kind` also accepts its `*_elf_only` variant.** A record
  naming `func_removed` accepts a `func_removed_elf_only` finding too: the
  variant is chosen by whether headers were supplied, not by the change. A
  record naming the `*_elf_only` kind accepts only that kind. See
  [`change_kind` and ELF-only variants](suppressions.md#change_kind-and-elf-only-variants).
- **A record matches one finding per run.** A `finding_id` is not unique per
  finding by design, so one record can, in principle, match two distinct
  findings in the same run. That is a hard error, never a silent double
  acceptance: narrow the record with `change_kind`, `component` or `candidate`.
- **An unscoped record warns.** A record with no `component`, `baseline` or
  `candidate` matches that finding in every release. That is legal, and the
  warning says so; scope it, or give it an `expires` date.
- **An expired record never matches.** Its finding is no longer acknowledged
  and gates as usual, and the run prints a warning naming the record and its
  expiry date.

An **ambiguous match** — more than one loaded record matching the same change
— is a hard error too, not a silently-resolved pick: that case requires review.

## File format

```yaml
version: 1
acknowledgments:
  - symbol: "_ZN3Foo6removeEv"
    change_kind: func_removed          # required with symbol
    component: libfoo
    candidate: "2.0.0"
    reason: "Deliberate removal — replaced by Foo::erase(); see #482"
    reference: "https://github.com/example/libfoo/issues/482"
    expires: 2026-12-31

  - finding_id: "e99c3be122c2ddf3"     # changes[].canonical_finding_id in the JSON report
    reason: "Planned public addition for the 2.0 release"
```

Same YAML envelope and loader machinery as
[suppressions](suppressions.md#file-format) (`version: 1`, one top-level
list key) — but a narrower key set: `finding_id`, `symbol`, `change_kind`,
`component`, `baseline`, `candidate`, `reason`, `reference`, `expires`. Any
suppression-only broad-selector key (`symbol_pattern`, `type_pattern`,
`namespace`, `entity_namespace`, `cause_namespace`, `source_location`,
`member_name`, `binding`) is a load error.

Copy a `finding_id` from the report's `changes[].canonical_finding_id` in
`-o json=…` output: it is backend-independent, so the record still matches
after an `--ast-frontend` switch.

## Breaks: `breaks: gate | accept`

An acknowledged **break** — a change classified `BREAKING` or `API_BREAK` — is
handled by `acknowledgments.breaks`:

- `gate` (the default) keeps the break's gate contribution. Acknowledging it
  changes the record, not the exit code: the run still exits `4` (or `2`).
  This is what every run did before the setting existed.
- `accept` lets an acknowledged break stop contributing to the exit code. Its
  disposition moves from `gating` to `non_gating`, recorded under the
  `acknowledgment` application point, with the record that accepted it. Only
  that break is affected: an unacknowledged sibling break on the same run still
  exits `4`.

Under `accept` the **verdict is still `BREAKING`**, the finding stays in the
report, and the release recommendation still reads it as a major-class break.
Accepting a break changes what gates the run, never what the comparison found.

`breaks` is a gate setting, so it has the same trust boundary as the records:
it applies from an explicit `--config`, from a `--policy` document's own
`acknowledgment:` block, or from a typed `CompareRequest`. An auto-discovered
`.abicheck.yml` cannot set `accept`.

## The additions review gate

A project configures whether an **unacknowledged public addition** is
flagged with `acknowledgments.unacknowledged_additions` in `.abicheck.yml`
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

## Protecting the records on pull requests

On a `pull_request` trigger the checkout is the pull request's own tree, so a
pull request can edit the records file and accept its own findings. Gate that
file with the same reusable check that protects committed baselines
([`protect-committed-baseline`](../reference/protect-committed-baseline.md)):
a pull request that touches the records fails unless it carries the bypass
label, which a reviewer adds only after reading the change.

```yaml
jobs:
  protect-acknowledgments:
    uses: abicheck/abicheck/.github/workflows/protect-committed-baseline.yml@<PINNED_COMMIT_OR_RELEASE>
    with:
      protected-paths: |
        abi/acknowledgments.yml
      bypass-label: acknowledgments-reviewed
```

Add a `paths:` filter naming the records file to the triggering workflow, as the
baseline reference shows, so the check runs when the file changes. The check
protects only the path it is given: list every records file a project uses.

## What the report shows

Every acknowledged finding's record id and the additions-review result
appear in the `disposition_audit` report block — see
[Disposition audit](disposition-audit.md) for the full shape. The record id is
`component::baseline::candidate::subject`, where a symbol record's subject is
`symbol[change_kind]`.
