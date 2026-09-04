---
doc_type: reference
audience:
  - library-maintainer
  - ci-owner
level: advanced
canonical_for:
  - compatibility-evaluation-config
lifecycle: active
generated: false
---

# Compatibility evaluation configuration (ADR-049)

!!! info "Status: resolved *and* applied — opt-in via `--contract`"

    This page is the reference for one typed object
    (`abicheck.compatibility_evaluation_frontend`) that resolves every
    setting deciding what a comparison promises, how a change to it is
    classified, and what blocks CI — resolved once, identically, whichever
    front end asked for the run. `abicheck compare --contract public`
    resolves one such object per run and reports it, as the
    `contract_context.evaluation_context` block of its JSON report.

    **The object is not merely reported — under `--contract` it is
    authoritative** (ADR-049 Phase 7, `contract_pipeline.py`). Two
    independent things follow from a resolved `contract.mode`:

    - **Per-finding contract relevance.** Each finding's relevance is
      classified *before* compatibility policy runs, and policy scores only
      the `EVALUATED` findings (`IN_CONTRACT` / `NOT_APPLICABLE`). A
      `PROVEN_OUT_OF_CONTRACT`, `UNKNOWN_UNPROVEN`, or `UNKNOWN_UNRESOLVED`
      finding is `NOT_EVALUATED`: its `compatibility_decision` is JSON
      `null`, and it moves neither the *compatibility* verdict nor the
      ordinary gate's exit contribution — but it stays in the report, with
      the reason code that says why. This is a separate question from the
      next bullet: a `NOT_EVALUATED` finding by itself never raises the
      exit code, but the *reason* it's unresolved (missing evidence) can
      still do so, orthogonally, via contract coverage below.
    - **Run-level contract coverage.** An orthogonal axis, independent of
      any single finding's decision: by default, if the selected domain's
      required evidence is incomplete, `compare`/`scan --against` contribute
      an exit `1`, folded with `max` against the ordinary gate — it can
      raise a clean `0` to `1`, never lower a `2`/`4`, and it never rewrites
      any finding's `compatibility_decision`. The one exception is a
      selected `kind: contract` pack setting `contract.unresolved: warn`
      (see "Pack manifests" below) — that zeroes this contribution
      specifically, while the failures stay listed in
      `contract_coverage_failures` regardless. So, absent that override, a
      run can exit `0` on compatibility (every evaluated finding is fine,
      and every `NOT_EVALUATED` finding is silent) while still exiting `1`
      overall because the evidence needed to trust that picture was
      incomplete. See [Exit Codes](exit-codes.md).

    Outside both `--contract` and a selected `--pack`, a
    `compare`/`scan` run resolves nothing from this object at all and every
    other command is unaffected — contract evaluation is still an opt-in
    feature, not a default-on one. A selected `--pack` **alone** (no
    `--contract`) still resolves and applies its own fields,
    though: a `kind: policy` pack overriding a `ChangeKind` moves the
    verdict and the exit code the same way an equivalent `--policy`
    override would (see "Selecting a pack" below), independent of contract
    evaluation. (A `kind: contract` pack's `contract.unresolved` is the one
    field that specifically needs `--contract` too, since nothing
    consumes it otherwise — see "Pack manifests" below.)

    The historical shadow/advisory design this feature shipped with first —
    where relevance was computed but never consulted by policy — is recorded
    in
    [ADR-049](../contribute/adr/049-contract-relevance-and-compatibility-configuration.md)
    itself, not repeated here. Today's live behaviour is also documented in
    [Configuration File](config-file.md), [Exit Codes](exit-codes.md), and
    [CI Gating](../use/ci-gating.md).

ADR-049 D7 requires that **one** typed object carry every setting that decides
what a comparison promises, how a change to it is classified, and what blocks
CI — resolved once, identically, whichever front end asked for the run. This
page is the reference for that object: which real input feeds each field, in
what precedence order, and what the resolution receipt records.

## The seven namespaces

| Namespace | Answers | Fields resolved today |
|---|---|---|
| `contract` | What is promised to consumers | `mode`, `unresolved`, `overlays`, `packs` |
| `evidence` | Which providers must complete | *(none — no front end selects one yet)* |
| `surface` | Explicit scope and surface hints | `explicit_scope`, `internal_namespaces` |
| `assurance` | Required evidence/coverage | `require_evidence` |
| `policy` | What a change to an in-contract entity means | `base`, `packs`, `overrides` |
| `gate` | What blocks CI | `exit_code_scheme` (purely derived — see below), `preset`, `packs`, `severity.*` |
| `suppressions` | Which findings are explicitly silenced | `rules`, `sha256` |

Keeping them separate is the point: a gate pack tightening what fails CI never
changes whether an entity is inside the contract, and a contract pack widening
the FFI boundary never moves a severity.

## Precedence

Each field is resolved **independently** (ADR-049 D7):

```text
explicit CLI flag / explicit API request field
> legacy CLI alias for that field
> selected run recipe
> selected run profile (execution fields only)
> project config (.abicheck.yml)
> selected pack
> built-in default
```

Rules that fall out of it:

- **Contradictory values in the same tier are a usage error** — e.g. two
  explicit values for one field.
- **Equivalent duplicates are fine** — two layers stating the same value
  resolve without complaint, and the receipt reports the winning chain.
- **An untouched option contributes nothing.** A flag left at its click
  default is not a stated value, so the next layer down wins. This matters for
  `--policy` and `--scope-public-headers`, whose defaults are not `None`.
- **A selected pack never silently overrides a stated value.** Packs sit
  directly above the built-in default: a pack fills a field only when neither
  the user nor the project stated it (ADR-049 D8's "explicit override >
  selected packs > base", read conservatively). "Stated" includes a value
  *derived* from a statement: choosing `--severity-preset strict` states every
  `gate.severity.*` category the preset expands into, so a gate pack cannot
  replace one of them — while a single `severity.addition: info` still refines
  the preset, which is not a conflict. A field stated this way is also exempt
  from pack-vs-pack conflict detection: two packs disagreeing about a value the
  resolution takes from neither of them decides nothing.
- **Order never decides anything.** Two invocations naming the same packs, the
  same symbols, or the same namespaces in a different order resolve to an equal
  object — including the receipt: two paths holding byte-identical manifest
  content collapse to one identity in the value and are both named, in sorted
  order, in `selected_by`, and naming one path twice selects it once. Two packs
  spelling one value differently (`contract.overlays: ffi` and
  `[ffi]`) agree rather than conflict — values are compared after routing.

### Two documented exceptions

Both keep an existing, tested behaviour rather than turning a working
invocation into an error. In each, the winning value is the explicit one and
the shadowed input is retained in the receipt
(`provenance[field].shadowed_legacy`), so a decision stays exactly replayable:

| Pair | Winner | Why |
|---|---|---|
| `--policy` + `--policy` | the file's `base_policy` | D7, verbatim: `--policy` "keeps winning as documented and tested today". `--policy`'s own help text already says it is ignored then. |
| `--contract` + `--scope-public-headers`/`--no-` | the explicit `--contract` | The Phase 6 flag documents that "an explicit value outranks those"; the live CLI accepts the pair today. |

## Where each field comes from

| Field | CLI | API (`CompareRequest`) | `.abicheck.yml` | Pack |
|---|---|---|---|---|
| `contract.mode` | `--contract`; legacy `--scope-public-headers/--no-` | `contract_mode`; `scope_public` | `scope.public` | — |
| `contract.unresolved` | — | — | — | `contract` |
| `contract.overlays` | — | — | — | `contract` |
| `contract.packs` | *(pack paths)* | — | — | — |
| `surface.internal_namespaces` | `--policy` | — | — | `contract` |
| `surface.explicit_scope` | `scope.public_symbols`, `scope.public_symbols` | `force_public_symbols` | `scope.public_symbols` | — |
| `assurance.require_evidence` | — | — | — | `contract` |
| `policy.base` | `--policy` `base_policy`; legacy `--policy` | `policy` | — | — |
| `policy.overrides` | `--policy` `overrides:` | — | — | `policy` |
| `policy.packs` | *(pack paths)* | — | — | — |
| `gate.preset` | `--severity-preset`; `--profile` (see below) | — | `severity.preset` | — |
| `gate.severity.*` | `severity.abi_breaking: error`, … | — | `severity.*` | `gate` |
| `gate.packs` | *(pack paths)* | — | — | — |
| `suppressions` | `--suppress` | `suppress` | — | — |

`surface.explicit_scope` is the one **additive** field: the CLI overlay widens
the project's symbol list rather than replacing it (ADR-037 D4's existing
behaviour), so the resolved value is the union and the receipt lists every
contributor.

`gate.exit_code_scheme` is absent from the table above on purpose: it is not
resolved from any CLI flag, API field, config key, or pack — there is no
input for it at all (CLI cleanup phase two PR G2 deleted the
`--exit-code-scheme` flag, the `.abicheck.yml` `exit_code_scheme:` key, and
the `gate.exit_code_scheme` pack field, the three ways it used to be
independently stated and could disagree with the automatic derivation). It
is a **purely derived** value, computed once every other field is resolved:
`"severity"` when `gate.severity` ended up non-default/in effect (any
`gate.preset`/`gate.severity.*` source above), otherwise `"legacy"`. It
carries no `field_provenance` receipt entry either, for the same
reason — there is no selector whose source layer it could name.

`suppression.strict: true` and `suppression.require_justification: true` are real inputs with no
field in ADR-049's typed shape; they stay outside this object.

### The `run_profile` tier and `--profile ci-gate`

`--profile` fills each setting its bundle declares only where you left the
corresponding flag alone, so it sits at D7's `run_profile` tier: below an
explicit flag, above `.abicheck.yml`. Exactly one field of this object is
reachable that way — `gate.preset`, which `ci-gate` sets to `default`. The
bundle's other keys (`depth`, the report format) are execution and report
settings with no field here. (Before CLI cleanup phase two PR G2, `ci-gate`
reached this tier through `gate.exit_code_scheme` instead, set to
`"severity"` directly — that field no longer exists at all, so `ci-gate` was
migrated to state `severity.preset: default` instead: the identical
`SeverityConfig` the old forced scheme paired with, expressed as an actual
severity setting, which is what now drives the purely-automatic algorithm to
`"severity"` and preserves the profile's exact prior behavior.)

That one field is a **known deviation** from D7, which scopes the
`run_profile` tier to execution fields and puts severity in the `gate`
namespace. It is recorded as what it is rather than smoothed over: `ci-gate`
predates ADR-049 and really does select a severity preset, so resolving the
field without the profile would report a value the run was not scored with.
Removing the deviation means either moving the key out of `ci-gate` into a
gate pack or amending D7 — both user-visible changes in their own right. Any
*other* field a future profile tried to assign is rejected.

## Pack manifests

A pack is a small versioned YAML file that assigns effective-config fields.
Which fields it *may* assign depends on its `kind`, and an assignment naming
anything else is a hard load error:

```yaml
id: security_hardening
version: 1
kind: gate                 # contract | policy | gate
assignments:
  gate.severity.addition: error
```

| `kind` | Assignable fields | Applied today |
|---|---|---|
| `contract` | `contract.unresolved`, `contract.overlays`, `surface.internal_namespaces`, `assurance.require_evidence` | `surface.internal_namespaces` and `contract.unresolved` |
| `policy` | any `ChangeKind` slug → `break` / `warn` / `risk` / `ignore` | all |
| `gate` | `gate.severity.abi_breaking`, `gate.severity.potential_breaking`, `gate.severity.quality_issues`, `gate.severity.addition` | all (`compare`, single-pair or directory/package; not `scan`) |

Deliberately **not** assignable: `contract.mode` (which evidence domain a run
judges against stays the user's own per-run choice — ADR-049 D3 forbids a
hidden preset that switches it), `policy.base` (packs compose over a base, they
do not replace it), `gate.exit_code_scheme` (CLI cleanup phase two PR G2
deleted the manual algorithm selector entirely — a gate pack asserting it is
a hard load error, `may not assign gate.exit_code_scheme`, the same as any
other out-of-namespace field below), and any `*.packs` field
(self-reference).

Conflicts are checked **within one kind only** (ADR-049 D8): two selected gate
packs assigning different values to the same field are a usage error until an
explicit value resolves it; a contract pack and a gate pack can never conflict
with each other. Load order is never a tiebreak.

An unknown `ChangeKind` slug in a `kind: policy` pack is a hard error, exactly
as in a `--policy`, so a renamed kind cannot silently disable a rule.

### Selecting a pack

`compare --pack PATH` and `scan --against ... --pack PATH` select one
(repeatable). A selected pack **configures the run**: a `kind: policy` pack
overriding `func_removed` changes the verdict and the exit code, and a
`kind: gate` pack's severity moves what blocks CI. That is worth stating
plainly, because the opposite would be worse than having no flag — a first
version of `--pack` reached the receipt and never the engine, and was reverted
before merge for exactly that reason.

The "Applied today" column above is enforced, not documentation:
a manifest assigning a field this build resolves but does not yet act on is a
usage error naming the field and the reason, rather than an assignment silently
recorded as active configuration (`abicheck.pack_application`). The same rule
rejects a `kind: gate` pack on `scan`, whose exit code follows its
compatibility verdict directly and so has no gate to move.

A further restriction, for the same "configure or reject" reason: `--pack`
needs `--against` on `scan` (a pack's only application there is the baseline
comparison's policy). On a directory/package (release) `compare`, a `kind:
policy`/`kind: contract`/`kind: gate` pack's `policy.overrides`/`surface.
internal_namespaces`/`gate.severity.<category>` all
apply to every library uniformly (CLI cleanup phase two, "PR B" slices 1
and 2) — the gate half is folded into the release fan-out's own resolved
`GateOptions` object (ADR-064, landed 2026-09-02). `contract.unresolved` is
still rejected there, with or without `--contract` (pending verification
that lifting it is safe — see the "7B's release-fan-out investigation
landed" section of
[the ADR-063 implementation plan](../contribute/plans/one-semantic-pipeline.md)).

## The resolution receipt

Every resolved field gets one `provenance` entry keyed by its dotted name:

```python
from abicheck.compatibility_evaluation_frontend import (
    ExplicitCompatibilityInputs,
    resolve_compatibility_evaluation_config,
)

cfg = resolve_compatibility_evaluation_config(
    explicit=ExplicitCompatibilityInputs(contract_mode="exports", policy_base="sdk_vendor")
)
cfg.contract.mode                          # ContractMode.EXPORTS
cfg.provenance["contract.mode"].layer      # SelectorLayer.EXPLICIT_CLI
cfg.provenance["policy.base"].reference    # "sdk_vendor"
```

An entry records the winning layer, the kind of source, its reference/path/
digest, the field location inside that source, the full `selected_by` selection
chain, and any shadowed legacy input. A file-derived entry carries that file's
own content digest — the `--policy` document's bytes, or the selected pack
manifest's `id`/`version`/`sha256` — so a receipt naming a since-edited file can
still prove which content produced the value. For a composed field
(`policy.overrides`, `surface.explicit_scope`), `selected_by` lists only the
sources that actually contributed: a policy pack whose every assignment the
policy file overrode is not credited. Bases, presets, and packs are identified
by `id` + `version` + `sha256` — for a built-in base or preset (which is code,
not a file) the digest is taken over what it resolves to, so a registry change
that moves a `ChangeKind` between buckets changes the identity too.

## Front-end equality

Phase 1's own gate is that equivalent semantic input resolves identically
whichever front end asked. It is checked, not asserted:

```python
from abicheck.compatibility_evaluation_frontend import cross_front_end_differences

cross_front_end_differences(cli_config, api_config)   # [] when equivalent
```

The only permitted difference is *which* front end stated a value
(`explicit_cli` vs. `api_request`, and the option spelling recorded with it) —
D7 puts both in the same precedence tier. Any difference in a resolved value,
or in any other part of the receipt, is reported.

The native `compare` CLI (`cli_compare_receipt.py`) resolves through it
today; the typed Python API (`FrontEnd.API`) resolves through the same
canonical function.

A receipt only ever names inputs its front end actually has. (Contrast
`CompareRequest.scope_public`, whose dataclass default *is* a caller's
choice and is recorded as one.)

!!! warning "Known gap: consumer scope is not in the resolved config"

    `compare --used-by`/`--required-symbol` are authoritative: the scoping
    pass rewrites the verdict and exit code from them. But no field of
    `CompatibilityEvaluationConfig` models a consumer scope, so a scoped run
    resolves the *same* object an unscoped one does, with
    `surface.explicit_scope: null` at `built_in_default`. The gap is wider
    than "which scope": `scoped` is not a value `GateConfig` accepts, so the
    *underlying* scheme is recorded instead — nothing in the resolved
    config indicates a consumer scope was in effect at all.

    `--required-symbol` is a partial exception: a required-symbol contract
    switches an untouched `--policy` to `plugin_abi`, and *that* is
    recorded via `policy_base_option`, leaving an indirect trace.
    `--used-by` has no such switch, so it leaves no trace at all.

    Closing this needs a new typed field plus an identity scheme for
    application binaries (path + content digest, resolved once and shared
    with the comparison that read them) — a scoped design, not a parameter
    to thread through. Until then, treat a scoped run's `resolved_config` as
    describing the *policy* configuration only.

The complement of that rule — a receipt must not name an input its front end
*cannot* have — needs its own check, because the equality gate above
deliberately normalizes option spellings away and so is blind to it:

```python
from abicheck.compatibility_evaluation_frontend import unstatable_selectors

unstatable_selectors(api_config)                            # no CLI flag at an API tier
unstatable_selectors(api_config, request_type=ScanRequest)  # ...and every name is a real field
```

Without `request_type` it reports any `api_request` hop labelled with a CLI
flag — a candidate built with a hard-coded `"--flag"` instead of going
through the resolver's front-end-aware spelling. That check alone is not
enough, because "not a flag" passes for any plausible-looking identifier:
**"the API" is not one namespace.** The default API spelling is
`CompareRequest`'s, and `ScanRequest` names three of the same inputs
differently (`scope_to_public_surface`, `policy_file`, `suppression`), so a
front end resolving at `FrontEnd.API` can still record fields its own request
type does not have. Pass `api_spellings=` to remap them per request type, and
`request_type=` to have the check verify it.

The field check covers the `api_request` **and** `legacy_alias` tiers, since
`--policy`/`scope_public` are D7 aliases and a hop for one sits at the latter.
Layers describing a *file* (`project_config`, `run_recipe`, `run_profile`) are
excluded — those correctly name config keys like `severity.preset`, which are
not request fields. The flag check stays one-directional: a CLI hop carrying a
bare field name is fine, since several CLI inputs (a project-config key, a
composed scope) genuinely have no flag.

## See also

- [ADR-049](../contribute/adr/049-contract-relevance-and-compatibility-configuration.md) — the normative decision
- [Configuration File](config-file.md) — the `.abicheck.yml` keys in effect today
- [Exit Codes](exit-codes.md) — the live exit contract
