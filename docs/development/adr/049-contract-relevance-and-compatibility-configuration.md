# ADR-049: Contract Relevance and Compatibility Configuration

**Date:** 2026-07-21
**Status:** Proposed — not implemented.
**Decision maker:** pending

## Context

A detector fact and a release promise are not the same thing. Today the built-in
`strict_abi`, `sdk_vendor`, and `plugin_abi` policies map `ChangeKind` to a
compatibility verdict. They cannot decide whether the entity named by a finding
belongs to the contract promised to consumers. In particular,
`func_removed_elf_only` remains a real binary removal under every compatibility
policy. Changing its policy severity cannot distinguish a public removal from a
proven implementation-only export.

The current L0 reconciliation path also performs an unscoped symbols-only
comparison and injects removals from its breaking bucket after public-header
scoping. That preserves the detector fact needed by case97, but makes every
exported removal block, including the pvxs private-export shape.

This ADR separates the questions in this order:

```text
detector fact
→ contract relevance
→ compatibility decision
→ gate decision
```

It extends, and does not collapse, these existing decisions:

- ADR-010: compatibility policies map kinds to `Verdict`;
- ADR-013: explicit change suppressions are auditable;
- ADR-024: public-surface evidence and traceability;
- ADR-040: execution bundles are run profiles;
- ADR-042: `CompatibilityDecision` and `GateDecision` are independent;
- ADR-048: matching must use canonical, ambiguity-safe entity identity.

The decision is specification-only. The implementation and rollout are tracked
in [the public-contract plan](../plans/public-contract-default.md).

## Decision

### D1. Contract relevance is an independent machine axis

Every normalized finding is classified before compatibility policy is applied:

```text
ContractRelevance := IN_CONTRACT
                   | PROVEN_OUT_OF_CONTRACT
                   | UNKNOWN_UNPROVEN
                   | UNKNOWN_UNRESOLVED
                   | NOT_APPLICABLE
```

| Value | Meaning | Default gate treatment |
|---|---|---|
| `IN_CONTRACT` | Authoritative evidence ties the entity to the declared consumer contract. | Apply compatibility policy, suppressions, and gate severity. |
| `PROVEN_OUT_OF_CONTRACT` | Authoritative evidence proves the entity is outside the declared contract, and no stronger in-contract evidence contradicts it. | Keep in the audit ledger; do not gate. |
| `UNKNOWN_UNPROVEN` | A closed, declared evidence domain was searched completely, but no commitment or authoritative exclusion was found. | Keep in the unresolved audit ledger; contributes exit 0. |
| `UNKNOWN_UNRESOLVED` | Required evidence is missing, failed, stale, partial, contradictory, or cannot be joined reliably. | Keep in the unresolved ledger; `analysis_status=NOT_CHECKABLE`, exit contribution 1 by default. |
| `NOT_APPLICABLE` | The finding is not entity-surface scoped, for example SONAME, loader, architecture, deployment, or security state. | Continue through compatibility and gate policy without contract filtering. |

`PROVEN_OUT_OF_CONTRACT` deliberately replaces the machine value `PRIVATE`.
The tool proves only that an entity is outside the **declared** contract; it
cannot prove that no unknown consumer calls an accidental export through
`dlsym`. UI prose may say “private under the declared contract.”

Contract classification never rewrites `ChangeKind` and never deletes a
detector fact. Compatibility policy computes its decision over applicable
in-contract findings; the later gate independently decides what blocks.
Contract assurance is a separate result:

```text
ContractAssurance := complete | partial | unavailable
AnalysisStatus     := CHECKABLE | NOT_CHECKABLE
CompatibilityEvaluationStatus := EVALUATED | NOT_EVALUATED
```

`compatibility_decision` contains the existing `Verdict` when
`compatibility_evaluation_status=EVALUATED`. It is JSON `null` when the status
is `NOT_EVALUATED`; this is not a new compatibility verdict. `IN_CONTRACT` and
`NOT_APPLICABLE` findings are evaluated. `PROVEN_OUT_OF_CONTRACT`,
`UNKNOWN_UNPROVEN`, and `UNKNOWN_UNRESOLVED` findings are not evaluated by
compatibility policy and have no change-gate contribution. Their canonical
per-finding report shape includes:

```json
{
  "contract_relevance": "UNKNOWN_UNRESOLVED",
  "compatibility_evaluation_status": "NOT_EVALUATED",
  "compatibility_decision": null,
  "gate_contribution": 0
}
```

The unresolved coverage ledger still contributes its independent exit `1`
where required; it does not rewrite `gate_contribution` or the null decision.

Contract relevance and compatibility do not determine the command exit directly.
The configured gate computes its own `0/1/2/4` contribution, while incomplete
required contract coverage contributes an orthogonal `1`; command aggregation
folds those contributions using the existing command-specific rules. Thus a
`COMPATIBLE` addition may block and a `BREAKING` finding may be demoted to exit
`0`. Only legacy mode without a gate block falls back to verdict-to-exit
mapping. Uncertainty itself never becomes an ABI break.

### D2. There are exactly three contract modes

```text
ContractMode := public | exports | all
```

#### `public`

Its evidence domain is the selected declared-public providers: exact manifests,
package symbol metadata, required symbols, real consumer imports, public
declarations, and roots selected by explicit overlays. The evaluator computes
their ABI type closure from the observed raw type graph. Failures are required
only when their capability is needed to close this selected public domain;
those failures yield `UNKNOWN_UNRESOLVED`. Unrelated provider failures remain
advisory.

#### `exports`

Its evidence domain consists only of exported function/variable roots plus the
ABI closure computed from the observed raw type graph. Complete export-root and
type-graph extraction are required. Public-header, manifest, consumer, and
other surface-provider failures are unrelated and advisory. Unreachable
private types remain outside the export domain.

#### `all`

Its domain is the normalized detector-fact set itself. Every detector finding
is gate-eligible and no public-surface/export-root evidence is required for
contract relevance. Surface-provider failures are advisory (while failures
needed to produce detector facts retain their normal detector-coverage
semantics). This is the forensic/debugging mode and the exact semantic
replacement for legacy unscoped behavior.

The mode-to-relevance mapping is normative:

| Mode | Entity finding inside selected roots/closure | Entity finding outside selected roots/closure | Incomplete required domain evidence | Non-entity finding |
|---|---|---|---|---|
| `public` | `IN_CONTRACT` | `PROVEN_OUT_OF_CONTRACT` only with complete authoritative exclusion; otherwise `UNKNOWN_UNPROVEN` after complete search | `UNKNOWN_UNRESOLVED` | `NOT_APPLICABLE` |
| `exports` | `IN_CONTRACT` | `PROVEN_OUT_OF_CONTRACT` only after complete export-root/type-graph traversal proves it unreachable | `UNKNOWN_UNRESOLVED` | `NOT_APPLICABLE` |
| `all` | `IN_CONTRACT`, including an identity-ambiguous normalized entity finding | Not possible: every normalized entity finding is in the selected domain | Contract-surface evidence is not required; detector-production coverage remains independent | `NOT_APPLICABLE` |

In `public` or `exports`, identity ambiguity that prevents root/closure
membership from being decided maps to `UNKNOWN_UNRESOLVED`, not exclusion. In
`all`, membership needs no evidence join: every entity finding that reached
normalization is `IN_CONTRACT`, while ambiguity remains detector-production
coverage. If ambiguity prevents normalization entirely, there is no entity
finding to classify and detector-production coverage reports the failure.

Legacy migration has deliberately asymmetric guarantees:

```text
--no-scope-public-headers  → contract=all     # exact alias
--scope-public-headers     → contract=public  # intentional stricter migration alias
```

The positive legacy flag did not have the new fail-closed provider-completeness
contract, so its `public` mapping is intentionally stricter rather than exact.
`--no-scope-public-headers` must never alias `exports`: the legacy option also
gates non-export, source/debug-only findings such as an unreachable private
type layout change. `contract=exports` is new semantics, not a compatibility
alias.

### D3. No hidden `public_contract` profile or preset

The future effective default is documented as three ordinary values:

```text
contract.mode       = public
policy.base         = strict_abi
contract.unresolved = not_checkable
```

`public_contract` is not a fourth mode, a compatibility policy, a profile, or
a persistent source layer. If a temporary rollout alias is needed before the
default flip, it must expand transparently to those fields, be reported as a
`run_recipe` source with its reference, and be removed after the migration
window. It must not be serialized as a semantic value.

The word **profile** is reserved for execution bundles. The existing
`--profile ci-gate|release-cut|quick` is therefore called a **run profile** in
documentation and configuration. A future CLI may introduce
`--run-profile`; if so, `--profile` remains a deprecated alias for one release
cycle. Run profiles select depth, report format, budget, and workflow defaults;
they do not create a hidden contract or compatibility axis.

Documented user recipes are transparent compositions, not engines or new
semantic enums. A report always expands a recipe field by field. Recommended
recipes include:

| Recipe | Expanded composition |
|---|---|
| `public-library` | `public` + `strict_abi` + `not_checkable` |
| `exported-library` | `exports` + `strict_abi` |
| `source-sdk` | `public` + `strict_abi` + required header/build matrix |
| `stable-plugin` | exact entrypoints/consumers + `strict_abi` + deployment gate pack |
| `co-built-plugin` | current-bundle contract + relaxed co-build policy + deployment gate pack |
| `ffi-boundary` | exact C/FFI roots + `strict_abi` + language/contract pack |
| `forensic` | `all` + `strict_abi` + full report |

### D4. Side authority is temporal

- Removal and modification of an existing obligation use old-side evidence.
- Addition and a new commitment use new-side evidence.
- New headers cannot retroactively hide an old public obligation.
- New public evidence cannot retroactively turn an old out-of-contract
  modification into a break; it may produce a separate new commitment.
- If the authoritative side is unresolved, evidence from the other side cannot
  manufacture confidence.

Explicit required symbols, exact manifests, and concrete consumer imports are
stronger than inferred header provenance. Authoritative in-contract evidence
wins over out-of-contract evidence. Unresolvable contradiction yields
`UNKNOWN_UNRESOLVED` rather than a guess.

### D5. Negative contract conclusions require provider-specific completeness

A successful parser invocation is not sufficient. The evaluator may emit
`UNKNOWN_UNPROVEN` only when all of the following hold for the affected entity
class and authoritative side. `PROVEN_OUT_OF_CONTRACT` has its own equally
strict completeness rule:

```text
closed_world_complete :=
    declared_domain_is_closed_and_enumerable
    AND every provider required for that domain completed
    AND requested_scope == searched_scope
    AND identity_coverage_is_complete
    AND required variant/configuration coverage completed
    AND no unresolved contradiction remains

out_of_contract_proof_complete :=
    identity_coverage_is_complete
    AND no authoritative in-contract evidence exists
    AND no unresolved contradiction remains
    AND (
        a terminal authoritative exclusion directly identifies the entity
        OR (
            positive out-of-contract provenance exists
            AND every selected provider capable of stronger-or-equal
                in-contract evidence completed for that entity/domain
        )
    )
```

A **terminal authoritative exclusion** is an exact declaration whose contract
semantics close membership for that entity. If a configured manifest, consumer,
required-symbol overlay, guarded declaration index, or other stronger/equal
provider can override the exclusion, that provider must complete before the
exclusion is terminal. Private-header provenance alone is never terminal while
such a provider is missing, failed, stale, partial, or identity-ambiguous.

Each provider publishes a completeness contract. The persisted ledger has at
least:

```text
EvidenceSearchRecord :=
  id + provider + side + entity_class + entity_scope
  + domain_kind + domain_identity
  + requested_scope + searched_scope
  + status + completeness + identity_coverage
  + configuration_coverage
  + reason_code + input_identity

status       := available | unavailable | failed | unsupported | stale
completeness := complete | partial | not_started
```

Examples:

- An exact manifest or exact export map has a closed, enumerable name domain;
  complete parsing can prove “searched, no commitment.”
- Public headers with conditional declarations require a guarded/token
  declaration index (or another declared equivalent) for the relevant language
  domain. If conditional declarations are possible and that provider is
  unavailable, active-AST success is only `partial`.
- Generated headers are complete only when generation is known to have run and
  their identities are included in the requested and searched scopes.
- If declarations vary by compile configuration, the project must declare the
  configuration set; all required variants must complete.
- A provider configured as optional may enrich facts, but it cannot be optional
  if its capability is necessary to close the declared domain. Requiredness is
  capability- and domain-specific, not a global provider label.
- Ambiguous symbol/type identity makes `identity_coverage` partial for affected
  entities.

A complete required search with no commitment gives `UNKNOWN_UNPROVEN`. Any
missing condition gives `UNKNOWN_UNRESOLVED`. This preserves case97: an active
AST that omits a macro-conditioned public declaration cannot produce a green
unknown when guarded-declaration coverage was required but missing.

Likewise, `PROVEN_OUT_OF_CONTRACT` is legal only after
`out_of_contract_proof_complete`. Positive private/system provenance plus an
unavailable public manifest or consumer provider yields `UNKNOWN_UNRESOLVED`,
not a non-gating result. A complete search with neither commitment nor complete
exclusion remains `UNKNOWN_UNPROVEN`.

### D6. Snapshots separate observed facts from evaluation context

Snapshots persist two independent, versioned blocks:

```yaml
contract_evidence:
  schema_version: 1
  identity_algorithm_version: 1
  providers:
    - provider: public_header
      observed_status: available
      domain_kind: public_headers
      requested_scope: [include/]
      searched_scope: [include/]
      input_identity: {sha256: "..."}
      declarations: []
      manifests: []
      type_graph:
        nodes: []
        edges: []
      completeness: complete

evaluation_context:
  schema_version: 1
  evaluator_version: 1
  identity_algorithm_version: 1
  resolved_config:
    contract: {mode: public, unresolved: not_checkable, overlays: []}
    evidence:
      providers:
        - capability: active_ast
          required: true
          implementation: {id: clang_ast, version: 1, sha256: "..."}
        - capability: guarded_declaration_index
          required: true
          implementation: {id: guarded_index, version: 1, sha256: "..."}
      variants: {items: [], sha256: "..."}
    surface:
      explicit_scope: {items: [], sha256: "..."}
      hints: {internal_namespaces: []}
    assurance: {require_evidence: true}
    policy:
      base: {id: strict_abi, version: 1, sha256: "..."}
      packs: []
      overrides: {}
    gate:
      exit_code_scheme: severity
      preset: {id: default, version: 1, sha256: "..."}
      packs: []
      severity_overrides: {}
    suppressions: {rules: [], sha256: "..."}
  field_provenance:
    contract.mode:
      layer: run_recipe
      reference: public-library
      selected_by: [{layer: explicit_cli, option: --recipe}]
    policy.base:
      layer: run_recipe
      reference: public-library
      selected_by: [{layer: explicit_cli, option: --recipe}]
    gate.exit_code_scheme:
      layer: project_config
      reference: .abicheck.yml
      selected_by: [{layer: project_config, path: .abicheck.yml}]

decision_receipt:
  evaluated_contract_roots: []
  evaluated_type_closure: []
  relevance_by_finding: {}
```

`contract_evidence` contains policy-independent observations and provider
coverage. Type observations are persisted as raw nodes and edges; a mode- and
root-dependent `type_closure` is an evaluation result and therefore belongs in
the decision receipt, never in observed evidence. Evidence does not encode
whether a provider happened to be required by one past decision.
`evaluation_context` serializes the complete resolved immutable configuration
used for a particular decision: contract/evidence/surface/assurance settings,
policy and gate bases/packs/overrides, the resolved gate/exit scheme,
suppressions, explicit scope, immutable manifest/pack identities and digests,
plus field-level provenance. The YAML
above abbreviates `field_provenance`; a persisted context has one provenance
entry for every resolved leaf, and every selected provider/base/preset/pack or
rule set carries an immutable identity/version/digest. The decision receipt
records evaluated roots, closure, and results.

- **Replay original decision** uses both persisted blocks and their versions.
- **Re-evaluate with a new policy/configuration** reuses observed facts but
  creates a new `evaluation_context`.
- Changing current required/optional rules never changes the stored original
  decision.
- A new evaluator or identity/join algorithm is explicit through version
  fields. It cannot silently reinterpret an old snapshot as though the same
  matching algorithm had been used.
- Unknown future evidence/evaluator/identity versions fail closed.

Per-finding final relevance may be stored as a decision receipt, but never in
place of observed facts.

### D7. One typed effective configuration and field-level provenance

All verdict-emitting comparison paths consume the same immutable object:

```python
@dataclass(frozen=True)
class CompatibilityEvaluationConfig:
    contract: ContractConfig          # mode, unresolved behavior, overlays
    evidence: EvidenceConfig          # providers, requirements, variants
    surface: SurfaceConfig            # explicit scope and surface hints
    assurance: AssuranceConfig        # evidence/coverage requirements
    policy: CompatibilityPolicyConfig # immutable base/packs/overrides
    gate: GateConfig                  # exit scheme, preset/packs/severity overrides
    suppressions: SuppressionConfig   # immutable rules and digest
    provenance: Mapping[str, ValueProvenance]
```

It is resolved once at the Tier-2 service boundary and passed to:

- `compare`;
- the baseline-comparison portion of `scan --against`;
- Python/service API requests;
- release/package fan-out;
- MCP and other adapters.

A front end may expose a smaller family of options, but it cannot construct a
second evaluator configuration implicitly. Equivalent semantic inputs must
resolve to an equivalent object.

Provenance is a structure, not a closed source enum. A manifest is an
input selected by another layer; it is **not** a precedence layer of its own:

```json
{
  "layer": "explicit_cli",
  "source_kind": "policy_manifest",
  "reference": "security",
  "path": "/project/abi-policy.yml",
  "sha256": "...",
  "field_location": "gate.packs[0]",
  "selected_by": [
    {"layer": "explicit_cli", "option": "--policy-file", "argument_index": 4}
  ]
}
```

A manifest selected by `--policy-file` inherits `explicit_cli` precedence; the
same manifest referenced by `.abicheck.yml` inherits `project_config`
precedence. `selected_by` preserves the full recipe/profile/config/CLI chain,
while `path`, digest, manifest identity/version, and field location identify the
actual definition used for exact replay.

Supported selector layers are extensible and include:

```text
explicit_cli
api_request
legacy_alias
run_recipe
run_profile
project_config
built_in_default
```

Each effective field carries its own provenance. Precedence follows the
selector of the value or manifest:

```text
explicit CLI or explicit API request for the field/manifest
> legacy CLI alias (for the field it aliases)
> selected run recipe
> selected run profile (execution fields only)
> project config (including manifests referenced there)
> built-in default
```

Contradictory values at the same selector layer, or a legacy alias that
disagrees with an explicit new option, are usage errors (64), except for the
existing `--policy` plus `--policy-file` compatibility rule. During migration,
`--policy-file` continues to win exactly as it does today; provenance records
the file-selected effective base and the shadowed `--policy` input. A future
major-version deprecation may reject that pair, but this ADR does not change it.
Equivalent duplicate values are accepted and report the winning selected-by
chain. Project `.abicheck.yml` may refer to policy manifests but does not silently gain
an unrelated top-level `policy:` scalar without schema migration.

### D8. Configuration separates bases and composable packs

A compatibility configuration is composite:

```yaml
contract:
  mode: public
  unresolved: not_checkable
  packs: [rust_c_ffi]

policy:
  base: strict_abi
  packs: [qt_kde_cpp, glibc_symbol_versioned]
  overrides:
    soname_bump_recommended: break

gate:
  preset: default
  packs: [security_hardening]

surface_hints:
  internal_namespaces: [detail]

assurance:
  require_evidence: true

run:
  profile: ci-gate
```

The conceptual namespaces are:

- **contract mode/providers/packs**: what is promised; language packs such as
  `rust_c_ffi` define an FFI boundary and its closure;
- **compatibility base policy**: what a change to an in-contract entity means;
- **compatibility/rule packs**: optional ecosystem release-governance rules;
- **gate preset/packs**: what blocks CI; security hardening belongs here and is
  `NOT_APPLICABLE` to entity contract membership;
- **surface hints**: evidence used by reachability or out-of-contract proofs;
- **assurance**: evidence requirements and unresolved behavior;
- **run profile**: depth, format, budget, and workflow.

Object-format semantics that are universally true when evidence exists belong
in core detection/classification, not optional packs (for example Mach-O load
compatibility, PE calling convention facts, or ELF version-node removal).
Project/ecosystem governance such as GNOME parallel-install policy and Qt/KDE
additional promises remain optional rule packs.

The existing ecosystem files migrate by responsibility rather than retaining a
single overloaded “policy profile” label:

| Existing file/name | Target responsibility |
|---|---|
| `qt_kde_cpp` | Optional compatibility/rule pack for the documented Qt/KDE promise. |
| `glibc_symbol_versioned` | Core ELF version semantics plus an optional governance pack for project-specific versioning discipline. |
| `gnome_parallel_install` | Optional release-governance rule pack. |
| `mach_o_dylib` | Core Mach-O loader/install-name/compatibility-version semantics; only project-specific release rules remain a pack. |
| `msvc_pe` | Core PE/MSVC format and calling-convention semantics; optional project rules remain a pack. |
| `rust_c_ffi` | Contract/language pack defining the `extern "C"`/`repr(C)` boundary and closure. |
| `security` | Gate pack, composable with any compatibility base and `NOT_APPLICABLE` to entity membership. |

Compatibility bases are deliberately few:

| Canonical base | Meaning | Migration |
|---|---|---|
| `strict_abi` | Strict native ABI/API compatibility. | Existing name retained. |
| `binary_compat` | Binary-consumer compatibility; source-only changes may be compatible. | `sdk_vendor` becomes a compatibility alias. |
| `co_built_plugin_bundle` | Host/plugins are rebuilt and released together; selected toolchain drift may be compatible while load/deployment failures block. | Existing `plugin_abi` becomes a legacy alias. |

`stable-plugin` is a recipe using exact plugin entrypoints/consumers and
`strict_abi`; it is not the relaxed existing plugin policy. The unqualified
`plugin_abi` name must not imply stability for independently distributed
third-party plugins.

Composition order is:

```text
explicit per-ChangeKind override > selected packs > base policy
```

Two selected packs that assign incompatible values to the same field or
`ChangeKind` are a usage error until an explicit final override resolves the
conflict. Pack order never decides semantics. An unknown `ChangeKind` in a
custom policy is a hard load error; warning-and-skip is unsafe because a renamed
kind can silently disable a release rule.

A physical YAML file may remain a single file for migration, but its schema and
report must acknowledge these namespaces. `--policy-file` selects such a
manifest at `explicit_cli` precedence; it is neither an independent precedence
layer nor merely another built-in policy name.

### D9. Normative pipeline includes suppression without suppressing coverage

The required order is:

```text
resolve effective CompatibilityEvaluationConfig
→ resolve/persist observed evidence
→ detect rich + L0 facts
→ normalize and reconcile canonical finding identity
→ apply explicit consumer/manifest scope
→ classify contract relevance
→ apply compatibility policy
→ apply change suppressions
→ compute gate severity
→ aggregate command exit
→ render all ledgers
```

Every detector fact must be conserved in exactly one visible outcome: gated,
proven out of contract, unresolved, suppressed, or reconciled into another
finding with references to all source facts.

Ordinary symbol/type/change suppressions may suppress an `IN_CONTRACT` change
after policy classification and must remain auditable as ADR-013 requires.
They may also hide a row from the primary display while retaining it in the
suppression ledger. They **cannot**:

- change contract relevance;
- turn `UNKNOWN_UNRESOLVED` into `UNKNOWN_UNPROVEN`;
- suppress a provider or domain coverage failure;
- make `analysis_status=NOT_CHECKABLE` green;
- suppress an aggregate required-target failure.

The explicit `unresolved_behavior=warn` control is the only ordinary mechanism
for permissively accepting incomplete contract coverage. It changes only the
orthogonal contract-coverage contribution, not `GateDecision`, observed
evidence, or the relevance label.

### D10. `compare` and `scan --against` have true policy parity

`compare OLD NEW` and the comparison-derived portion of
`scan NEW --against OLD` call the same comparison core with the same
`CompatibilityEvaluationConfig`. For equivalent inputs and effective config,
every shared finding must be field-for-field identical in:

- canonical finding and entity identity;
- `ChangeKind` and detector provenance;
- contract relevance, reason, side, and evidence references;
- compatibility decision;
- suppression decision;
- gate category/contribution.

`scan` may append scan-only source/cross-check findings, but cannot rewrite the
shared decisions. It need not expose every compare flag: a common typed
`compatibility_options` family, project config, or composite manifest is
sufficient, provided all semantic inputs are representable and provenance is
preserved.

L0 reconciliation returns facts, not a breaking bucket. A function such as
`collect_l0_export_delta()` must retain actual removals, deduplicate them with
rich findings by canonical identity, and send them through the same contract,
policy, suppression, and gate pipeline. PR #494's invariant is “a real L0
removal fact must not disappear,” not “every L0 removal blocks.”

### D11. Reporting is explicit and reproducible

Reports expose the effective values and provenance of every semantic field;
they do not rely on a recipe/profile name:

```yaml
recipe: public-library
contract:
  mode: public
  mode_source: {layer: run_recipe, reference: public-library}
  unresolved: not_checkable
  unresolved_source: {layer: run_recipe, reference: public-library}
policy:
  base: strict_abi
  base_source: {layer: run_recipe, reference: public-library}
  packs: []
gate:
  preset: default
run:
  profile: ci-gate
```

The canonical out-of-contract ledger uses
`PROVEN_OUT_OF_CONTRACT`; both unknown classes live in a separate unresolved
ledger. Coverage/provider failures remain visible even when all associated
change rows are suppressed or display-filtered. Render truncation cannot alter
counts, assurance, gate state, or exit code.

### D12. Exit semantics remain orthogonal

This ADR does not redefine ADR-009/042 exit schemes. It adds only an
orthogonal contract-coverage contribution:

- complete selected-domain coverage, including `UNKNOWN_UNPROVEN`: 0;
- incomplete required selected-domain evidence with `UNKNOWN_UNRESOLVED`: 1 by
  default.

The configured `GateDecision` independently contributes `0/1/2/4`. It may
block a compatible addition or demote a breaking finding; compatibility is
reported independently and does not overwrite that gate contribution. Only a
legacy result with no gate block uses verdict-to-exit fallback. Command
aggregation then folds gate and coverage contributions; existing
command-specific 5 (scan budget), 8 (removed release library), and 64 (usage)
retain their documented precedence/short-circuit rules. Reports state whether
exit 1 came from contract coverage, gate severity, or another coverage axis.

## Consequences

### Positive

- pvxs-style proven implementation exports no longer force release failure in
  `public` mode, while their detector facts remain auditable.
- case97 remains blocking when guarded/variant-complete old-side evidence proves
  the declaration public; incomplete macro coverage fails closed instead of
  producing a false green.
- `exports` has precise root-and-closure semantics and `all` preserves the true
  legacy forensic behavior.
- Snapshot replay distinguishes “what was observed” from “how it was judged.”
- All front ends share policy, suppression, provenance, and gate semantics.
- Ecosystem, security, language-boundary, and execution settings stop competing
  for the word “profile.”

### Costs and risks

- The config resolver and report schema are larger than a two-value scope flag.
- Provider completeness is capability- and domain-specific and requires
  explicit generated-header/configuration accounting.
- Existing policy files are composite in practice and need a schema migration.
- `sdk_vendor`/`plugin_abi` and `--profile` need aliases and clear migration
  text before canonical names can change.
- Default `public` mode must not flip until real-world delta review proves zero
  unexplained public-break losses and measures unresolved rates.

## Rejected alternatives

### Change `strict_abi`/`sdk_vendor` severity for private exports

Rejected: severity cannot decide entity membership and would weaken real public
removals of the same `ChangeKind`.

### Treat every export as public

Rejected as the default: it preserves false positives for accidental exports.
It remains available explicitly as `contract=exports`.

### Make `exports` mean “all findings”

Rejected: private unreachable type findings are not exports. The unscoped mode
is named `all`, and only that mode aliases legacy
`--no-scope-public-headers`.

### Treat parser success as complete header evidence

Rejected: active AST parsing can omit macro-conditioned declarations. Closed
world completeness requires every capability needed for the declared domain.

### Store only final relevance in snapshots

Rejected: it prevents reproducible original replay and principled re-evaluation
under a new policy/evaluator.

### Resolve pack conflicts by load order

Rejected: order-dependent compatibility and gate semantics are unsafe and hard
to audit.

## Rollout constraints

1. Land terminology, this ADR, and schema contracts.
2. Implement one field-level resolver and `CompatibilityEvaluationConfig`.
3. Implement canonical finding identity and fact-conserving L0/rich
   reconciliation.
4. Run a shadow evaluator while the existing gate remains authoritative.
5. Persist separate `contract_evidence` and `evaluation_context` blocks with
   evaluator/identity versions.
6. Establish `compare`/`scan --against` field parity.
7. Offer opt-in `contract=public`; validate case97, pvxs, all object formats,
   snapshots, packages, and downstream report consumers.
8. Flip the default only after zero unexplained public-break losses, reviewed
   false-positive deltas, and an acceptable measured unresolved rate.
9. Keep `contract=all` and `--no-scope-public-headers` as the forensic rollback.
