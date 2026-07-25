# Public-contract default: implementation and rollout plan

**Status:** Proposed — specification only; no implementation in this PR
**Normative decision:** [ADR-049](../adr/049-contract-relevance-and-compatibility-configuration.md)
**Related:** ADR-010, ADR-013, ADR-015, ADR-024, ADR-028/033, ADR-037/040/043, ADR-042, ADR-048, PR #494 / case97
**Scope:** `compare`, the comparison portion of `scan --against`, service/API
adapters, release fan-out, snapshots, reports, configuration, and migration

This document is an implementation plan. ADR-049 owns the durable public
enums, mode semantics, configuration precedence, snapshot separation, pipeline
order, and exit contract.

## 1. Problem and target behavior

Current policy profiles answer only “how severe is this `ChangeKind`?” They do
not answer “is this entity part of the contract promised to consumers?” The L0
reconciliation path currently performs an unscoped symbols-only compare and
folds `func_removed_elf_only` directly from a breaking bucket. That keeps the
case97 removal visible, but also blocks a pvxs-style removal even when evidence
proves the export is outside the declared public contract.

The implementation must preserve this flow:

```text
detected fact
→ normalized identity
→ contract relevance
→ compatibility policy
→ explicit change suppression
→ gate severity
→ command exit
```

A detector fact never disappears and its `ChangeKind` is never rewritten merely
to obtain a desired gate result.

### Acceptance outcomes

- case97: old-side macro-conditioned public declaration removal remains a real,
  blocking break when guarded/configuration-complete evidence proves it public;
  incomplete macro coverage is `UNKNOWN_UNRESOLVED`, never a green absence.
- pvxs: a removal with authoritative out-of-contract provenance is retained in
  an audit ledger and does not block in `contract=public`.
- bare export with no complete declared contract evidence is
  `UNKNOWN_UNRESOLVED`/`NOT_CHECKABLE`, not silently public or private.
- complete closed-domain search with no commitment is `UNKNOWN_UNPROVEN` and
  contributes exit 0 while remaining visible.
- all comparison-derived findings are field-for-field equal between
  `compare OLD NEW` and `scan NEW --against OLD` for equivalent inputs and
  effective configuration.

## 2. Public vocabulary

### 2.1 Contract modes

Implement exactly three modes:

| Mode | Contract roots and closure | Primary use |
|---|---|---|
| `public` | Selected declared-public providers/overlays supply roots; evaluate their closure from the raw type graph. Only capabilities required to close that domain are required. | Normal library/API gate. |
| `exports` | Export extraction supplies every function/variable root; evaluate their ABI closure from the raw type graph. Other surface providers are unrelated and advisory. | Binary-only/distro or projects declaring exports as contract. |
| `all` | The normalized detector-fact set is the domain: every finding is gate-eligible and no surface evidence is required. | Forensics, detector debugging, legacy unscoped behavior. |

Legacy aliases have asymmetric guarantees:

```text
--no-scope-public-headers  → --contract all     # exact alias
--scope-public-headers     → --contract public  # stricter migration alias
```

The positive legacy flag did not enforce the new fail-closed completeness
contract, so its mapping is intentionally stricter. Do not map
`--no-scope-public-headers` to `exports`: the old unscoped behavior also gates
findings with no exported root, such as a debug/header-only private type layout
change.

### 2.2 Contract relevance

```text
IN_CONTRACT
PROVEN_OUT_OF_CONTRACT
UNKNOWN_UNPROVEN
UNKNOWN_UNRESOLVED
NOT_APPLICABLE
```

The machine value is `PROVEN_OUT_OF_CONTRACT`, not `PRIVATE`. UI text may say
“private under the declared contract,” but the tool does not claim that an
unknown consumer cannot use an accidental export.

Mode-to-relevance mapping:

| Mode | Inside roots/closure | Outside roots/closure | Required evidence incomplete | Non-entity finding |
|---|---|---|---|---|
| `public` | `IN_CONTRACT` | `PROVEN_OUT_OF_CONTRACT` only after complete authoritative exclusion; otherwise `UNKNOWN_UNPROVEN` after complete search | `UNKNOWN_UNRESOLVED` | `NOT_APPLICABLE` |
| `exports` | `IN_CONTRACT` | `PROVEN_OUT_OF_CONTRACT` only after complete export-root/type-graph traversal proves unreachability | `UNKNOWN_UNRESOLVED` | `NOT_APPLICABLE` |
| `all` | `IN_CONTRACT` for every normalized entity finding, including one with ambiguous identity | Not applicable | Surface evidence is unnecessary; detector-production coverage is separate | `NOT_APPLICABLE` |

For `public` and `exports`, identity ambiguity that prevents root/closure
membership from being decided is `UNKNOWN_UNRESOLVED`; it cannot prove
exclusion. For `all`, no membership join is needed, so every entity finding
that reached normalization is `IN_CONTRACT` and ambiguity remains independent
detector-production coverage. If ambiguity prevents normalization, there is no
entity finding to classify and detector-production coverage reports the failure.

### 2.3 No new profile axis

There is no persistent `public_contract` profile or preset. The intended
effective default is simply:

```text
contract.mode       = public
policy.base         = strict_abi
contract.unresolved = not_checkable
```

If rollout temporarily exposes a one-token alias, it is a transparent,
time-limited recipe that expands into those values and is reported field by
field. It never serializes as a contract mode or policy name.

Use **run profile** for the existing execution bundles (`ci-gate`,
`release-cut`, `quick`). Documentation should call the current `--profile` a
run profile. A later CLI cleanup may add `--run-profile` and retain
`--profile` as a deprecated alias; implementation of that rename is not a
prerequisite for the contract evaluator.

User-facing recipes are documented compositions, for example
`public-library`, `exported-library`, `source-sdk`, `stable-plugin`,
`co-built-plugin`, `ffi-boundary`, and `forensic`. Reports expand recipes into
effective fields and provenance; recipe names are never hidden semantics.

## 3. Effective configuration

### 3.1 One typed object

Add a leaf-layer immutable object (exact module name may vary):

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

Resolve it once at the Tier-2 service boundary. The same object goes to:

- direct `compare`;
- the baseline-comparison portion of `scan --against`;
- service/Python API;
- release/directory/package fan-out;
- MCP and other adapters.

`scan` need not copy every compare flag. It does need a small shared
compatibility-options family and/or one shared config input that can represent
all semantic fields. Front ends normalize into the same typed object instead of
reimplementing defaults.

### 3.2 Field-level precedence and provenance

Each field is resolved independently. A manifest is selected by a layer; it is
not itself a precedence layer. Store enough provenance for exact replay:

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

Required selector layers include:

```text
explicit_cli
api_request
legacy_alias
run_recipe
run_profile
project_config
built_in_default
```

Field precedence follows the selector:

```text
explicit CLI / explicit API request for the field or manifest
> legacy CLI alias for that field
> selected run recipe
> selected run profile (execution fields only)
> project config, including a manifest selected there
> built-in default
```

Thus a manifest selected by CLI `--policy-file` has `explicit_cli` precedence,
while the same manifest referenced by `.abicheck.yml` has `project_config`
precedence. `selected_by` records the complete selection chain. Provenance also
records immutable manifest/pack identity and version, path, digest, and field
location.

Rules:

- conflicting values in the same selector layer are usage error 64;
- a legacy alias conflicting with an explicit new option is usage error 64;
- compatibility exception: when both current `--policy` and `--policy-file`
  are supplied, `--policy-file` keeps winning as documented and tested today;
  provenance records the effective file-selected base plus the shadowed
  `--policy` input. Rejecting this pair requires a separate major-version
  deprecation;
- equivalent duplicates are accepted and report the winning selected-by chain;
- unknown config keys/enum values fail at load time;
- `.abicheck.yml` does not gain an ad hoc top-level `policy: strict_abi`
  scalar unless its strict schema is deliberately migrated;
- `--policy-file` is a selector for a composite manifest and must never
  disappear from provenance.

Implement resolution as table-driven per-field code and test the cross-product
of layers rather than relying on Click callback order.

### 3.3 Configuration namespaces and packs

A composite manifest should converge on explicit namespaces:

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

Separate concepts:

- contract/language packs define roots, providers, and ABI closure (for example
  Rust `extern "C"`/`repr(C)` boundaries);
- compatibility base policy maps in-contract changes to `Verdict`;
- rule packs add ecosystem release-governance rules;
- gate packs affect `GateDecision` and compose with any compatibility policy;
- surface hints inform provenance/reachability and cannot themselves silently
  demote a public fact;
- assurance controls required evidence/unresolved behavior;
- run profiles control execution depth, format, budget, and workflow.

Object-format truths belong in core behavior when evidence exists: Mach-O load
compatibility, PE/MSVC calling convention semantics, ELF symbol-version node
removal, and universal native layout/calling rules must not require an optional
profile. GNOME parallel-install and project-specific SONAME rules remain
optional rule packs. Security hardening is a gate pack and `NOT_APPLICABLE` to
entity contract membership.

Migration map for current ecosystem files:

| Existing file/name | Target |
|---|---|
| `qt_kde_cpp` | Optional compatibility/rule pack. |
| `glibc_symbol_versioned` | Core ELF symbol-version semantics plus optional project governance rules. |
| `gnome_parallel_install` | Optional release-governance rule pack. |
| `mach_o_dylib` | Core Mach-O semantics plus optional project governance rules. |
| `msvc_pe` | Core PE/MSVC semantics plus optional project governance rules. |
| `rust_c_ffi` | Contract/language pack defining exact C/FFI roots and closure. |
| `security` | Gate pack composable with every ABI policy. |

Canonical compatibility bases:

- `strict_abi` retained;
- `binary_compat`, with `sdk_vendor` as a compatibility alias;
- `co_built_plugin_bundle`, with the current `plugin_abi` as a legacy alias.

`stable-plugin` is a recipe using exact entrypoints/consumers plus
`strict_abi`; independently distributed plugins must not inherit the current
co-build relaxations from an ambiguous `plugin_abi` label.

Composition:

```text
explicit per-kind override > selected packs > base policy
```

Conflicting packs are a usage error until an explicit final override resolves
the field. File order never resolves conflicts. Unknown `ChangeKind` slugs in
custom policy are hard errors; replace the current warning-and-skip behavior in
the implementation phase so a renamed kind cannot silently disable policy.

## 4. Evidence model and completeness

### 4.1 Observed provider ledger

Add policy-independent provider records such as:

```text
EvidenceSearchRecord :=
  id + provider + side + entity_class + entity_scope
  + domain_kind + domain_identity
  + requested_scope + searched_scope
  + status + completeness
  + identity_coverage + configuration_coverage
  + reason_code + input_identity

status       := available | unavailable | failed | unsupported | stale
completeness := complete | partial | not_started
```

A resolved evaluation plan separately says which **capabilities** are required
for a declared domain. Do not persist “this provider was required under one
policy” as if it were an observed fact.

Provider failures are scoped to the affected domain/entity class. An unrelated
failed provider does not poison a completed exact-manifest search for another
entity. Contradictory identity joins preserve every candidate and a stable
ambiguity reason; never select by iteration order.

### 4.2 Closed-world rule for `UNKNOWN_UNPROVEN`

`UNKNOWN_UNPROVEN` is legal only if the authoritative side satisfies:

```text
declared domain is closed and enumerable
AND every capability required to close that domain completed
AND requested scope equals searched scope
AND affected entity identity coverage is complete
AND every declared compile/generated-header variant completed
AND no unresolved contradiction remains
```

Provider-specific contracts:

- exact manifests and exact export maps can be closed enumerable domains;
- wildcard export rules do not prove an intentional per-symbol commitment;
- active AST alone does not close a header domain that permits conditional
  declarations;
- guarded/token declaration indexing is required when needed to enumerate
  macro-conditioned declarations (case97);
- generated headers are complete only after known generation and digest/scope
  capture;
- projects with configuration-dependent declarations must declare the variant
  set and complete every required variant;
- parse success with missing macro/index/variant coverage is `partial`;
- ambiguous mangled/demangled/type identity is partial for affected entities.

A provider can be optional only if no capability it supplies is needed to close
the selected domain. “Optional globally” must not become a loophole that lets
case97 fall to `UNKNOWN_UNPROVEN`.

### 4.3 Public and out-of-contract proofs

Public evidence, strongest first:

1. explicit required symbol, exact contract/ABI manifest, package symbols
   metadata, or concrete consumer import/relocation/recorded entrypoint;
2. side-authoritative declaration physically originating in a declared public
   header, including guarded declarations omitted from the active AST;
3. transitive public ABI type closure and leak paths;
4. exact project export-map/`.def` commitment;
5. concrete runtime consumer evidence.

`PROVEN_OUT_OF_CONTRACT` requires resolved identity and authoritative positive
proof, for example private/system-header provenance outside every public
closure, an exact authoritative exclusion, or a framework-specific oracle. An
internal-looking name, missing docs, wildcard export, or absence from active AST
is only a hint. Any authoritative in-contract evidence wins.

The negative proof must itself be complete:

```text
out_of_contract_proof_complete :=
    identity coverage is complete
    AND no authoritative in-contract evidence or contradiction exists
    AND (
        a terminal exact exclusion directly identifies the entity
        OR (
            positive out-of-contract provenance exists
            AND every provider capable of stronger-or-equal public evidence
                completed for that entity/domain
        )
    )
```

An exclusion is terminal only if no configured stronger/equal manifest,
consumer, required-symbol, guarded-declaration, or other overlay can override
it. Private-header provenance while any such provider is unavailable, failed,
stale, partial, or identity-ambiguous is `UNKNOWN_UNRESOLVED`. A complete
search with no commitment and no complete exclusion is `UNKNOWN_UNPROVEN`.

### 4.4 Side authority

- removals and modifications of existing obligations: old side;
- additions/new commitments: new side;
- public→private visibility: old side remains blocking;
- out-of-contract/unknown→public: model as a new commitment, not a retroactive
  old break;
- unresolved authoritative side cannot be repaired by non-authoritative-side
  evidence.

## 5. Snapshot and report schemas

### 5.1 Snapshot blocks

Persist observations separately from a decision context:

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

`contract_evidence` stores raw policy-independent type nodes/edges. The
mode/root-dependent closure is computed by the evaluator and stored in the
decision receipt, not as observed evidence. `evaluation_context` must serialize
the complete immutable resolved `CompatibilityEvaluationConfig`, including all
contract/evidence/surface/assurance fields, provider requirements and variants,
explicit scope and hints, policy/gate bases, packs and overrides with identities
and digests, the resolved gate/exit scheme, suppressions, and field provenance
with selected-by chains. The
illustrative provenance map is abbreviated; persisted output has one entry for
every resolved leaf, and every selected provider/base/preset/pack or rule set
carries an immutable identity/version/digest.

Behavior:

- original-decision replay uses both blocks and exact versions;
- re-evaluation uses old observations with a newly resolved context;
- current required-provider defaults cannot alter the recorded original
  decision;
- evaluator and identity/join algorithm versions are explicit because the same
  raw facts can classify differently under a new matcher;
- unknown future versions fail closed;
- legacy snapshots remain readable but become unresolved where old-side facts
  needed by `public` are absent;
- no silent live-file re-probe changes a replayed verdict; disclosed enrichment
  may be allowed only with strong input identity.

The final relevance can be stored as a decision receipt, but observations must
remain available for new-policy evaluation.

### 5.2 Canonical result/report shape

Add one canonical block used by JSON, text, SARIF, JUnit, Markdown, GitHub, and
aggregate ingestion. Illustrative shape:

```json
{
  "effective_evaluation": {
    "recipe": "public-library",
    "contract": {
      "mode": "public",
      "mode_source": {"layer": "run_recipe", "reference": "public-library"},
      "unresolved": "not_checkable",
      "unresolved_source": {"layer": "run_recipe", "reference": "public-library"},
      "assurance": "partial"
    },
    "policy": {
      "base": "strict_abi",
      "base_source": {"layer": "run_recipe", "reference": "public-library"},
      "packs": []
    },
    "gate": {"preset": "default", "packs": []},
    "run": {"profile": "ci-gate"}
  },
  "contract_counts": {
    "in_contract": 3,
    "proven_out_of_contract": 8,
    "unknown_unproven": 1,
    "unknown_unresolved": 1,
    "not_applicable": 2
  },
  "unresolved_contract_changes": [],
  "contract_coverage_failures": []
}
```

Per finding:

- canonical finding/entity identity;
- `contract_relevance`;
- stable `contract_reason`;
- evidence references with side and input identity;
- `compatibility_evaluation_status` (`EVALUATED|NOT_EVALUATED`);
- `compatibility_decision` (`Verdict` when evaluated, JSON `null` otherwise);
- suppression decision/reference;
- gate category/contribution.

Canonical non-evaluated shape:

```json
{
  "contract_relevance": "UNKNOWN_UNRESOLVED",
  "compatibility_evaluation_status": "NOT_EVALUATED",
  "compatibility_decision": null,
  "gate_contribution": 0
}
```

The sibling contract-coverage ledger may contribute exit `1`; it never rewrites
the null compatibility decision or the finding's zero gate contribution.

Keep proven-out-of-contract and unresolved ledgers separate. Provider/domain
coverage failures are a sibling canonical ledger, not synthetic change rows
that ordinary suppression can erase. Existing `surface_scope` and
`out_of_surface_changes` may be derived during a compatibility window, but no
new `PRIVATE` machine value is emitted.

Display filtering and truncation cannot affect counts, assurance, gate state,
or exit. SARIF emits deterministic properties and a tool-level coverage
notification. JUnit represents `NOT_CHECKABLE` according to its coverage/error
contract, never as a passed compatibility test. Aggregate preserves the three
orthogonal axes from ADR-042: compatibility, gate, and required coverage.

## 6. Pipeline implementation

### 6.1 Canonical order

```text
resolve CompatibilityEvaluationConfig
→ resolve and persist observed evidence
→ detect rich + L0 facts
→ normalize/reconcile canonical identity
→ apply explicit consumer/manifest scope
→ classify contract relevance
→ apply compatibility base/packs/overrides
→ apply explicit change suppressions
→ compute gate preset/packs/severity
→ aggregate command exit
→ render every ledger
```

### 6.2 Suppression semantics

Ordinary change suppressions can suppress an in-contract finding after
compatibility policy classification. They remain visible in the ADR-013 audit
ledger. They may hide a proven-out-of-contract/unresolved row from a selected
view only if the canonical ledger/counts remain intact.

They cannot:

- alter contract relevance;
- suppress a provider/domain coverage failure;
- turn `UNKNOWN_UNRESOLVED` into `UNKNOWN_UNPROVEN`;
- clear `analysis_status=NOT_CHECKABLE`;
- make a failed required aggregate target green.

`unresolved_behavior=warn` is the explicit mechanism to accept incomplete
contract assurance. It changes only the orthogonal contract-coverage
contribution, not `GateDecision`, evidence, or labels.

### 6.3 L0/rich reconciliation

Replace `fold_l0_hard_removals()` with a collector such as
`collect_l0_export_delta()`:

- returns normalized facts, never a preclassified breaking bucket;
- retains L0 detector provenance and coverage;
- deduplicates rich/L0 changes by canonical entity + change identity;
- records references to every reconciled input fact;
- sends the result through contract, policy, suppression, and gate stages;
- is shared by direct compare and scan baseline compare.

PR #494's invariant becomes: a real L0 removal fact must not disappear. It
does not mean every L0 removal blocks. Case97 blocks because complete old-side
public evidence says it is in contract.

### 6.4 Cross-command parity

For equivalent inputs and effective config, compare the baseline-derived result
from both commands field by field:

```text
compare OLD NEW
scan NEW --against OLD
```

Equal fields include canonical identity, `ChangeKind`, detector provenance,
contract relevance/reason/evidence side, compatibility decision, suppression,
and gate contribution. Scan-only source/cross-check findings may be appended;
they cannot rewrite the shared comparison findings.

## 7. Command behavior and exit composition

### `scan ARTIFACT` without baseline

A one-build audit cannot synthesize removals. It builds the candidate contract
index, runs quality/security/source checks, audits uncommitted exports, and
reports coverage. Complete unproven entities contribute coverage 0; unresolved
required evidence contributes coverage 1. The independent configured gate
contributes `0/1/2/4` (and may block compatible additions or demote breaks);
budget overflow is 5 and usage is 64.

### `compare` and `scan --against`

- `public`: its evidence domain is the selected declared-public
  providers/overlays. Roots/closure are `IN_CONTRACT`; non-entity findings are
  `NOT_APPLICABLE`. Complete authoritative exclusions are
  `PROVEN_OUT_OF_CONTRACT`; a complete search with neither commitment nor
  exclusion is `UNKNOWN_UNPROVEN`; incomplete required evidence is
  `UNKNOWN_UNRESOLVED`. Only failures needed to close this domain contribute
  coverage `1`; unrelated provider failures are advisory.
- `exports`: its domain is only exported function/variable roots and closure
  computed from the raw type graph. Roots/closure are `IN_CONTRACT`; an entity
  proven unreachable after complete root/graph traversal is
  `PROVEN_OUT_OF_CONTRACT`; incomplete root/graph or identity evidence is
  `UNKNOWN_UNRESOLVED`; non-entity findings are `NOT_APPLICABLE`.
  Public-header/manifest/consumer failures are unrelated and advisory.
- `all`: its domain is all normalized detector facts. Every entity finding is
  `IN_CONTRACT`, including a normalized finding with ambiguous identity;
  non-entity findings are `NOT_APPLICABLE`. If identity ambiguity prevents
  normalization, detector-production coverage reports it rather than creating
  an unclassifiable contract finding. No surface evidence is required and
  surface-provider failures are advisory. Detector-production coverage remains
  independently enforceable.

Compatibility policy is evaluated only for `IN_CONTRACT` and `NOT_APPLICABLE`.
Other relevance states have `compatibility_evaluation_status=NOT_EVALUATED`, a
JSON `null` compatibility decision, and zero change-gate contribution;
`UNKNOWN_UNRESOLVED` may still contribute the independent coverage exit `1`.

### Snapshot/binary and package/release

Use persisted evidence on snapshot sides and fresh evidence on binary sides.
Report side asymmetry. Resolve contract and coverage per library before release
aggregation. Whole-library removal continues to use existing
`--fail-on-removed-library` exit 8 rules; entity evidence does not hide it.

### Exit aggregation

No new global integer ordering is introduced. Preserve ADR-042's orthogonal
axes:

- the configured `GateDecision` contributes `0/1/2/4`, independent of the
  compatibility verdict; a compatible addition may block and a breaking
  finding may be demoted;
- selected-domain contract coverage contributes `0` or `1` independently;
- only a legacy result with no gate block derives `2/4` from API/ABI verdict;
- command aggregation folds gate and coverage contributions using the existing
  command-specific rules;
- invalid invocation is 64 before analysis;
- scan budget overflow short-circuits with 5;
- release removed-library 8 retains the legacy/severity-aware precedence
  documented in `docs/reference/exit-codes.md`;
- output serialization failures use the existing operational path.

Reports identify whether exit 1 comes from contract coverage, gate severity,
or aggregate required-target coverage.

## 8. Scenario matrix

| Scenario | `public` | `exports` | `all` |
|---|---|---|---|
| Public header function removed | `IN_CONTRACT`; evaluate policy/gate | `IN_CONTRACT` if exported/rooted; otherwise proven out only after complete closure | `IN_CONTRACT`; evaluate |
| Macro-conditioned public declaration removed (case97) | `IN_CONTRACT` when guarded/config matrix is complete; otherwise `UNKNOWN_UNRESOLVED`/1 | `IN_CONTRACT` if exported; header-provider failure advisory | `IN_CONTRACT`; header-provider failure advisory |
| Proven private-header exported helper removed (pvxs) | `PROVEN_OUT_OF_CONTRACT` only with complete negative proof; otherwise unresolved | `IN_CONTRACT`; evaluate policy/gate | `IN_CONTRACT`; evaluate |
| Export absent from a complete exact declared contract | `UNKNOWN_UNPROVEN`, audit/0 | `IN_CONTRACT`; evaluate policy/gate | `IN_CONTRACT`; evaluate |
| Export with no usable public-contract source | `UNKNOWN_UNRESOLVED`, `NOT_CHECKABLE`/1 | `IN_CONTRACT` if export/type evidence complete; unrelated public-provider failure advisory | `IN_CONTRACT`; surface-provider failure advisory |
| Undocumented export imported by `--used-by` | `IN_CONTRACT`; evaluate policy/gate | `IN_CONTRACT`; evaluate policy/gate | `IN_CONTRACT`; evaluate |
| Exact manifest/version-script symbol removed | `IN_CONTRACT`; evaluate policy/gate | `IN_CONTRACT` if exported/rooted; otherwise complete closure decides | `IN_CONTRACT`; evaluate |
| Wildcard-only export rule | Unknown unless other evidence | `IN_CONTRACT` when observed as export root | `IN_CONTRACT`; evaluate |
| Private unreachable type layout change | `PROVEN_OUT_OF_CONTRACT` only after complete exclusion proof; otherwise `UNKNOWN_UNPROVEN` after complete search or `UNKNOWN_UNRESOLVED` if incomplete | `PROVEN_OUT_OF_CONTRACT` only after complete root/graph traversal; otherwise `UNKNOWN_UNRESOLVED` | `IN_CONTRACT`; evaluate |
| Private-header type leaked through public/exported signature | `IN_CONTRACT`; evaluate public closure | `IN_CONTRACT`; evaluate exported closure | `IN_CONTRACT`; evaluate |
| Public symbol becomes hidden | `IN_CONTRACT`; evaluate from old side | `IN_CONTRACT`; evaluate exported old-side root | `IN_CONTRACT`; evaluate |
| Private symbol becomes public | New `IN_CONTRACT` commitment/addition; gate may block | New `IN_CONTRACT` export; gate may block | `IN_CONTRACT` addition; gate may block |
| Active AST complete, guarded index required but failed | `UNKNOWN_UNRESOLVED`, coverage/1 | Export domain remains checkable; header failure advisory | `IN_CONTRACT`; header failure advisory |
| SONAME/loader/security regression | `NOT_APPLICABLE`, policy/gate applies | Same | Same |
| Explicit required symbol missing | `IN_CONTRACT`; evaluate policy/gate | `IN_CONTRACT` only when represented by an old exported root; otherwise complete closure decides | `IN_CONTRACT`; evaluate |

## 9. Work breakdown

### Phase 0 — terminology and schema contracts

- Accept ADR-049.
- Reserve `public|exports|all` and relevance enums.
- Define report/snapshot schema version strategy and stable reason-code
  registry.
- Document run-profile vocabulary and aliases.

**Gate:** docs and schemas have no `exports == all`, `PRIVATE`, hidden
`public_contract` preset, or policy/contract conflation.

### Phase 1 — effective resolver

Likely surfaces:

- a new leaf config module;
- `cli_options.py` shared compatibility family;
- `.abicheck.yml` strict schema/reference docs;
- `policy_file.py` composite namespacing/migration;
- service/API request models and release fan-out.

Implement field-level provenance, conflicts, aliases, pack conflict detection,
and hard errors for unknown `ChangeKind` slugs.

**Gate:** every front end resolves equivalent semantic input to an equal
`CompatibilityEvaluationConfig` and provenance receipt.

### Phase 2 — canonical identity and fact conservation

Build finding identity on ADR-048 principles: most specific available identity,
ambiguity-safe fallback, deterministic joins. Refactor L0 collection before any
contract evaluator changes the gate.

**Gate:** rich+L0 conservation and dedup properties; no detector fact loss.

### Phase 3 — shadow contract evaluator

Implement a leaf `contract_surface`/`contract_evaluation` module with no CLI
imports. Produce relevance, assurance, reasons, and provider ledgers in reports,
but leave the old gate authoritative.

Measure:

- delta by old/new decision;
- unresolved rate by provider/domain/platform;
- proven public-break losses;
- proven false-positive reductions.

**Gate:** every shadow delta has evidence and stable identity; zero unexplained
fact loss.

### Phase 4 — snapshot evidence/context split

Persist policy-independent `contract_evidence` and separate
`evaluation_context`, each versioned; add evaluator and identity algorithm
versions. Implement original replay, new-policy re-evaluation, legacy and
unknown-future handling.

**Gate:** byte/order-independent round-trip decisions and explicit mixed-version
failure behavior.

### Phase 5 — shared authoritative comparison

Route both direct compare and scan baseline compare through the same core and
same typed config. Add suppression and unsuppressible coverage ledgers in the
normative stage order.

**Gate:** field-for-field parity tests across binaries, snapshots, mixed inputs,
policies, packs, suppressions, and explicit scope.

### Phase 6 — opt-in public mode and corpus validation

Expose `--contract public|exports|all`. Preserve
`--no-scope-public-headers` as the exact alias for `all`; migrate
`--scope-public-headers` to intentionally stricter `public` semantics. Keep the
old default while running case97, pvxs, real-world corpus, ELF/Mach-O/PE,
stripped, versioned, C/C++, snapshot, package, and downstream
renderer/aggregate lanes.

**Gate:** zero unexplained public-break losses; reviewed FP reductions; measured
and accepted unresolved rate; all downstream consumers understand new schema.

### Phase 7 — default flip

After release notes and a migration window, set the three independent defaults
to `public`, `strict_abi`, and `not_checkable`. Keep `contract=all` and
`--no-scope-public-headers` as the exact forensic rollback. Do not make a
`public_contract` enum/preset permanent.

## 10. Test plan

### 10.1 Unit tests

**Resolver**

- every precedence pair and equivalent duplicate;
- explicit CLI/API, policy file, legacy alias, recipe, run profile, project
  config, and built-in provenance, including current `--policy-file` wins over
  `--policy` behavior and its shadowed-input provenance;
- field-by-field policy-file interactions;
- conflicting packs and explicit conflict resolution;
- unknown `ChangeKind` hard failure;
- canonical aliases for `sdk_vendor` and `plugin_abi`.

**Provider completeness**

- exact complete-empty manifest;
- private/system provenance plus unavailable stronger public provider is
  `UNKNOWN_UNRESOLVED`, never `PROVEN_OUT_OF_CONTRACT`;
- terminal exact exclusion vs non-terminal exclusion, including a stronger
  manifest/consumer/guarded provider that completes, fails, or is stale;
- active AST with complete guarded index;
- active AST with guarded index failed/missing;
- generated header present/missing/stale;
- complete and incomplete compile-variant matrix;
- partial traversal, timeout, unsupported provider, stale input;
- required capability supplied by alternative provider;
- optional enrichment failure that is not needed to close the domain;
- identity ambiguity and contradictory evidence;
- scope-local failure that does not poison unrelated entities.

Assert requested/searched scope, input identity, provider status, closed-domain
reason, relevance, assurance, and exit contribution.

**Classifier and side authority**

Cross product of add/remove/modify/visibility/type × old/new relevance ×
explicit consumer/manifest evidence. Cover the complete `public|exports|all`
mode-to-relevance table: export root/closure membership, proven unreachable
entities, non-entity `NOT_APPLICABLE`, all-mode entity `IN_CONTRACT`, and
identity ambiguity. Assert that out-of-contract, unproven, and unresolved
findings have `compatibility_evaluation_status=NOT_EVALUATED`, JSON-null
compatibility decision, and no change-gate contribution. Include old
unresolved/new public modification: old obligation remains unresolved while a
separate new commitment may be emitted.

**L0 normalization**

- L0-only removal survives;
- rich+L0 gives one logical finding referencing both facts;
- symbol versions do not collapse incorrectly;
- collector never assigns gate severity;
- ordering is deterministic.

**Suppressions**

- public change can be explicitly suppressed and remains in audit trail;
- suppression cannot alter relevance;
- suppressing every affected change does not clear provider coverage failure;
- `unresolved_behavior=warn` changes only the contract-coverage contribution.

### 10.2 Integration and CLI tests

For text and JSON, assert verdict, exit, canonical identity, evidence side,
relevance, reason, compatibility decision, suppression, gate, assurance,
coverage, and provenance for:

1. public function removal;
2. case97 guarded declaration;
3. pvxs authoritative out-of-contract export;
4. complete-domain unknown export;
5. no-evidence unresolved export;
6. `--used-by` and required-symbol proof;
7. exact vs wildcard export manifests;
8. private unreachable type and public leak closure;
9. old/new side asymmetry and public→private/new-public transitions;
10. generated headers and variant matrix failures;
11. binary, DWARF, header, source/full depth;
12. binary/binary, snapshot/snapshot, and mixed inputs;
13. one-build scan (no synthetic removal);
14. directory/package/release aggregation;
15. framework oracle and Rust/C FFI contract pack;
16. loader/SONAME/security `NOT_APPLICABLE` behavior;
17. policy/rule/gate/contract pack composition and conflicts;
18. suppressions plus unresolved coverage;
19. all three contract modes and both legacy aliases;
20. all output formats and aggregate ingestion.

Run equivalent `compare` and `scan --against` invocations and compare every
shared field, not only top-level verdict.

Exit combinations cover contract coverage 1 with gate 1/2/4, scan budget 5,
usage 64, release removal 8 under legacy and severity schemes, and operational
errors. Do not sort all integers as one global severity scale.

### 10.3 Regression tests

Rewrite the PR #494 regression around two independent invariants:

1. a real L0 removal is conserved;
2. it blocks only when contract mode/evidence makes it relevant.

Case97 variants:

- old guarded declaration proves public and blocks;
- changing only new-side metadata cannot hide it;
- missing guarded coverage is unresolved, not unproven;
- L0-only still uses old persisted public evidence;
- rich+L0 emits one finding with both provenances;
- reverse comparison is a new-side addition.

pvxs fixture variants:

- authoritative out-of-contract proof audits in `public`;
- proof absent becomes the correct unknown;
- concrete consumer evidence wins and gates;
- `exports` gates because it is an export root;
- `all` gates every finding, including unreachable private type changes.

Update case182 by supplying explicit evidence if it is expected to remain
public or by making it unresolved in `public`; preserve legacy result under
`all`.

### 10.4 Property tests

- **Conservation:** every detector fact is represented by gated,
  proven-out-of-contract, unresolved, suppressed, or reconciled output.
- **Public evidence monotonicity:** adding authoritative public evidence can
  move unknown/out-of-contract to in-contract, never the reverse.
- **Out-of-contract proof monotonicity:** authoritative exclusion can move an
  unknown out of contract but cannot override public/consumer proof.
- **Mode relation:** public and exports each gate a subset of all; neither is
  generally a subset of the other because public manifests can name a
  non-exported API obligation and exports roots every actual export.
- **Side symmetry:** reversing inputs maps add/remove authority correctly.
- **Order independence:** provider/finding/pack order cannot change semantics.
- **Deduplication and conservation:** rich+L0 emits one logical finding while
  retaining both source facts.
- **Anti-hiding:** explicit scope, leaks, loader/security, and coverage failures
  cannot become out of contract or vanish through ordinary suppression.
- **Cross-command parity:** shared findings are field-for-field equal.
- **Snapshot reproducibility:** serialization and provider order do not alter
  original replay; a changed policy creates a new evaluation context.

### 10.5 Rollout gates

Before default flip:

- case97 and the complete PR #494 lane pass;
- pvxs stops blocking in `public` and remains auditable;
- every real-world old/new delta is manually classified;
- zero unexplained public-break losses;
- unresolved rate measured separately from false-positive rate;
- ELF visibility/versioning/stripped, Mach-O exports/load metadata, and PE
  `.def`/ordinals/decorated identity are covered;
- unsupported providers fail closed rather than silently skip;
- JSON schema, snapshot schema, SARIF, JUnit, Markdown, aggregate, service,
  MCP, and GitHub Action consumers pass;
- rollback is documented as `contract=all` /
  `--no-scope-public-headers`.

## 11. Risks and non-goals

Risks:

- provider completeness can become falsely optimistic unless generated headers,
  macros, variants, and identity coverage are modeled explicitly;
- policy-file migration can break existing ecosystem packs if compatibility,
  gate, contract, and surface namespaces are not bridged deliberately;
- schema consumers may assume a single “filtered/private” bucket;
- changing default before corpus review can trade known false positives for
  public-break false negatives.

Non-goals:

- proving no unknown consumer uses an accidental export;
- treating naming/documentation absence as authoritative exclusion;
- changing detector facts to obtain a gate result;
- making one-build scan claim cross-version compatibility;
- implementing the evaluator in this specification PR;
- multiplying base policies for every ecosystem choice when composable packs or
  transparent recipes suffice.

## 12. Definition of done

Implementation is ready for the default flip only when:

1. ADR-049 vocabulary and three modes are implemented exactly.
2. Every front end consumes one `CompatibilityEvaluationConfig`.
3. Snapshot observations and decision context are separate and versioned.
4. `UNKNOWN_UNPROVEN` is emitted only after provider-specific closed-world
   completeness.
5. L0 facts are conserved without bypassing contract evaluation.
6. Suppression is explicit and coverage failures are unsuppressible.
7. `compare` and `scan --against` shared findings are field-for-field equal.
8. Reports expose full effective configuration and structured provenance.
9. Policy bases, rule packs, gate packs, contract/language packs, and run
   profiles are distinct and conflicts are deterministic errors.
10. The regression/property/real-world gates pass on supported object formats.
11. The default flip has zero unexplained public-break losses and an accepted
    unresolved rate.
