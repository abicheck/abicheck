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

!!! warning "Resolved and reported — not yet applied"

    Everything on this page is implemented as *configuration resolution*
    (`abicheck.compatibility_evaluation_frontend`) and is fully tested.
    `abicheck compare --contract-evaluation` resolves one such object per run
    and reports it, as the `contract_context.evaluation_context` block of its
    JSON report — but only as an audit record: it changes no verdict, no
    finding, and no exit code. Every other command, and every `compare` run
    without that flag, resolves nothing. Applying the object in the
    authoritative comparison path is the rest of
    [ADR-049](../contribute/adr/049-contract-relevance-and-compatibility-configuration.md)
    Phase 5, and the default flip is Phase 7. Pack manifests (below) have
    no CLI flag yet either — they are reachable from the Python API only.
    Today's live behaviour is documented in
    [Configuration File](config-file.md) and [Exit Codes](exit-codes.md).

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
| `gate` | What blocks CI | `exit_code_scheme`, `preset`, `packs`, `severity.*` |
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
  replace one of them — while a single `--severity-addition info` still refines
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
| `--policy` + `--policy-file` | the file's `base_policy` | D7, verbatim: `--policy-file` "keeps winning as documented and tested today". `--policy`'s own help text already says it is ignored then. |
| `--contract` + `--scope-public-headers`/`--no-` | the explicit `--contract` | The Phase 6 flag documents that "an explicit value outranks those"; the live CLI accepts the pair today. |

## Where each field comes from

| Field | CLI | API (`CompareRequest`) | `.abicheck.yml` | Pack |
|---|---|---|---|---|
| `contract.mode` | `--contract`; legacy `--scope-public-headers/--no-` | `contract_mode`; `scope_public` | `scope.public` | — |
| `contract.unresolved` | — | — | — | `contract` |
| `contract.overlays` | — | — | — | `contract` |
| `contract.packs` | *(pack paths)* | — | — | — |
| `surface.internal_namespaces` | `--policy-file` | — | — | `contract` |
| `surface.explicit_scope` | `--public-symbol`, `--public-symbols-list` | `force_public_symbols` | `scope.public_symbols` | — |
| `assurance.require_evidence` | — | — | — | `contract` |
| `policy.base` | `--policy-file` `base_policy`; legacy `--policy` | `policy` | — | — |
| `policy.overrides` | `--policy-file` `overrides:` | — | — | `policy` |
| `policy.packs` | *(pack paths)* | — | — | — |
| `gate.exit_code_scheme` | `--exit-code-scheme`; `--profile` (see below) | — | `exit_code_scheme` | `gate` |
| `gate.preset` | `--severity-preset` | — | `severity.preset` | — |
| `gate.severity.*` | `--severity-abi-breaking`, … | — | `severity.*` | `gate` |
| `gate.packs` | *(pack paths)* | — | — | — |
| `suppressions` | `--suppress` | `suppress` | — | — |

`surface.explicit_scope` is the one **additive** field: the CLI overlay widens
the project's symbol list rather than replacing it (ADR-037 D4's existing
behaviour), so the resolved value is the union and the receipt lists every
contributor.

`--exit-code-scheme auto` never appears as a resolved *value*: it means "decide
from whether a severity setting is in effect", so it is resolved to `legacy` or
`severity` before the object is built. Selecting it is still a stated choice,
though — an explicit `--exit-code-scheme auto` outranks a project config's
concrete scheme, and the receipt records `auto` as what was selected. (A
project config's own `auto` is indistinguishable from an unset key, since that
is `BuildConfig`'s default for it, so it contributes nothing.)

`--strict-suppressions` and `--require-justification` are real inputs with no
field in ADR-049's typed shape; they stay outside this object.

### The `run_profile` tier and `--profile ci-gate`

`--profile` fills each setting its bundle declares only where you left the
corresponding flag alone, so it sits at D7's `run_profile` tier: below an
explicit flag, above `.abicheck.yml`. Exactly one field of this object is
reachable that way — `gate.exit_code_scheme`, which `ci-gate` sets to
`severity`. The bundle's other keys (`depth`, the report format,
`--recommend`, `--stat`) are execution and report settings with no field
here.

That one field is a **known deviation** from D7, which scopes the
`run_profile` tier to execution fields and puts the exit-code scheme in the
`gate` namespace. It is recorded as what it is rather than smoothed over:
`ci-gate` predates ADR-049 and really does select the scheme, so resolving
the field without the profile would report a value the run was not scored
with. Removing the deviation means either moving the key out of `ci-gate`
into a gate pack or amending D7 — both user-visible changes in their own
right. Any *other* field a future profile tried to assign is rejected.

## Pack manifests

A pack is a small versioned YAML file that assigns effective-config fields.
Which fields it *may* assign depends on its `kind`, and an assignment naming
anything else is a hard load error:

```yaml
id: security_hardening
version: 1
kind: gate                 # contract | policy | gate
assignments:
  gate.exit_code_scheme: severity
  gate.severity.addition: error
```

| `kind` | Assignable fields |
|---|---|
| `contract` | `contract.unresolved`, `contract.overlays`, `surface.internal_namespaces`, `assurance.require_evidence` |
| `policy` | any `ChangeKind` slug → `break` / `warn` / `risk` / `ignore` |
| `gate` | `gate.exit_code_scheme`, `gate.severity.abi_breaking`, `gate.severity.potential_breaking`, `gate.severity.quality_issues`, `gate.severity.addition` |

Deliberately **not** assignable: `contract.mode` (which evidence domain a run
judges against stays the user's own per-run choice — ADR-049 D3 forbids a
hidden preset that switches it), `policy.base` (packs compose over a base, they
do not replace it), and any `*.packs` field (self-reference).

Conflicts are checked **within one kind only** (ADR-049 D8): two selected gate
packs assigning different values to the same field are a usage error until an
explicit value resolves it; a contract pack and a gate pack can never conflict
with each other. Load order is never a tiebreak.

An unknown `ChangeKind` slug in a `kind: policy` pack is a hard error, exactly
as in a `--policy-file`, so a renamed kind cannot silently disable a rule.

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
own content digest — the `--policy-file` document's bytes, or the selected pack
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

Both live front ends resolve through it: the native `compare` CLI
(`cli_compare_receipt.py`) and the MCP `abi_compare` tool
(`mcp_compare_receipt.py`), and
`tests/test_mcp_compare_config_receipt.py` runs the check above across the
two on equivalent input.

A receipt only ever names inputs its front end actually has. `abi_compare`
takes no scope, contract-mode, public-symbol, or exit-code-scheme parameter,
so those fields resolve as `built_in_default` there rather than as an API
request — which is the accurate answer, not a missing claim: there is
nothing for a caller to have chosen. (Contrast `CompareRequest.scope_public`,
whose dataclass default *is* a caller's choice and is recorded as one.)

The complement of that rule — a receipt must not name an input its front end
*cannot* have — needs its own check, because the equality gate above
deliberately normalizes option spellings away and so is blind to it:

```python
from abicheck.compatibility_evaluation_frontend import unstatable_selectors

unstatable_selectors(api_config)   # [] when no hop names a flag nobody typed
```

It reports any `api_request` hop labelled with a CLI flag. That is the one
shape this failure takes: a candidate built with a hard-coded `"--flag"`
instead of going through the resolver's front-end-aware spelling. The check
is one-directional — a CLI hop carrying a bare field name is fine, since
several CLI inputs (a project-config key, a composed scope) genuinely have no
flag.

## See also

- [ADR-049](../contribute/adr/049-contract-relevance-and-compatibility-configuration.md) — the normative decision
- [Configuration File](config-file.md) — the `.abicheck.yml` keys in effect today
- [Exit Codes](exit-codes.md) — the live exit contract
