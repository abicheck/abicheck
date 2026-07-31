# Public-contract default: implementation and rollout plan

**Status:** ADR-049 accepted (2026-07-26); implementation in progress — see
"Work breakdown" below for current per-phase status
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
custom policy are hard errors, so a renamed kind cannot silently disable
policy — implemented (2026-07-26, ADR-049 D8) in `policy_file.py`'s
`_parse_overrides` and `CompatibilityPolicyConfig.overrides`, replacing the
prior warning-and-skip behavior.

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

**Progress:** ADR-049 accepted (2026-07-26, decision maker: napetrov).
Vocabulary/reason-code/schema-version types reserved
(`abicheck/contract_relevance_types.py`); nothing wired into detection,
policy, the CLI, or reports yet — the remaining Phase 0 items are pure
documentation/schema artifacts that ride along with Phase 1's wiring work
rather than being separately actionable.

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

**Progress:** the typed `CompatibilityEvaluationConfig` shape (slice 1,
`abicheck/compatibility_evaluation_config.py`), the field-level precedence
resolver (slice 2, `abicheck/compatibility_evaluation_resolver.py`
`resolve_field`, implementing this phase's conflicts/aliases rules), pack
conflict detection (`detect_pack_conflicts`, generalized to any pack field,
not only `ChangeKind` overrides), and the unknown-`ChangeKind`-slug hard
error are done — enforced at both the YAML edge (`policy_file.py`'s
`_parse_overrides` raises `PolicyError`) and the typed-config edge
(`CompatibilityPolicyConfig.overrides` rejects an unknown slug regardless
of which front end constructs it directly). The first real front-end
wiring also landed:
`abicheck/compatibility_evaluation_wiring.py`'s
`resolve_legacy_contract_mode` resolves `contract.mode` from the real
`--scope-public-headers`/`--no-scope-public-headers` CLI flag via
`resolve_field` — not called from any live command yet (that's the Phase 3
shadow evaluator's job).

A second real wiring landed the same way: `resolve_internal_namespaces`
resolves `surface.internal_namespaces` from a real `--policy-file` YAML's
`internal_namespaces` list (`policy_file.py`'s `PolicyFile` — the only front
end that can set this field today, since no CLI flag exists for it). An
absent `--policy-file`, or one that sets the key to an empty list
(indistinguishable, once parsed, from never setting it), contributes no
candidate and falls through to the built-in default (`()`, equal to
`SurfaceConfig.internal_namespaces`'s own default), the same "a selector
layer only participates when it actually selected something" principle the
first wiring's untouched-flag case already applies. The candidate value is
sorted+deduped before being handed to `resolve_field`, mirroring
`SurfaceConfig.__post_init__`'s own canonicalization of this exact
order-insensitive field (and `compatibility_evaluation_packs.py`'s
`_canonicalize_order_insensitive_field`, which already treats
`surface.internal_namespaces` the same way at the pack-manifest layer) —
two policy files listing the same namespaces in a different order resolve
to an equal value, per D7. Also not called from any live command yet.

Pack *content* loading has also landed: `abicheck/compatibility_evaluation_packs.py`'s
`load_pack_manifest` reads a small versioned YAML pack-manifest format
(`id`/`version`/`kind: contract|policy|gate`/`assignments`) into a
`LoadedPack` (an `ImmutableIdentity` content-digested over the manifest's raw
bytes, paired with the pack's resolved `field name -> value` assignments),
and `assignments_for_conflict_check` projects a list of `LoadedPack`s,
grouped by `PackKind`, into the `(ImmutableIdentity, Mapping[str, Hashable])`
pairs `detect_pack_conflicts` already accepts -- a caller will need to run
`detect_pack_conflicts` once per returned kind group, since D8's conflict
rule is scoped to comparing packs *within* one namespace, not across them
(a flat, ungrouped projection previously let a policy pack's `ChangeKind`
slug and an unrelated gate pack's own field name collide by string
coincidence alone). A `kind: policy` manifest's
assignments are `ChangeKind` slug -> severity spelling, converted through
`policy_file.py`'s now-public `parse_severity_value` (extracted from
`_parse_overrides` so the two loaders share one severity vocabulary instead
of each declaring it independently) with the identical unknown-slug hard
error as `--policy-file`. A `kind: contract`/`kind: gate` manifest's
assignments are arbitrary field-name -> scalar/list values, recursively
converted to hashable tuples. This module only loads pack content into the
shape `detect_pack_conflicts` accepts — it does not select which packs apply
to a run, call `detect_pack_conflicts` itself, or fold a policy pack's
resolved overrides into `CompatibilityPolicyConfig.overrides`.

**Updated (2026-07-29): pack selection/composition wiring landed.**
`abicheck/compatibility_evaluation_wiring.py`'s `resolve_selected_packs`
takes a real `--pack <path>`-style (repeatable) path list, loads each with
`load_pack_manifest`, groups the result with `assignments_for_conflict_check`,
and runs `detect_pack_conflicts` once per `PackKind` namespace — closing the
exact gap this paragraph used to describe. It returns the three real
`contract.packs`/`policy.packs`/`gate.packs` field resolutions (each a
`tuple[ImmutableIdentity, ...]`, matching those fields' actual type — pack
*content* composition into `CompatibilityPolicyConfig.overrides`/etc. is
still separate, unwired work, see below). Two `--pack` paths naming the
identical manifest content collapse into one contributor; the resolved
tuple is sorted by `(id, version, sha256)` so pack order never changes the
resolved value (D8's "file order never resolves conflicts", extended here
to selection order too). Not called from any live command yet — same
"land the wiring function itself, fully tested" pattern as
`resolve_legacy_contract_mode`/`resolve_internal_namespaces` above; no CLI
flag exists to supply pack paths, and (per Phase 3's own 2026-07-29 update
below) `cli.py` is at its 2000-line hard cap, so an actual `--pack` flag
needs a prerequisite extraction, not a same-PR addition.

Still remaining: wiring any other field (`cli_options.py`'s other shared
option families beyond `scope_options`, `.abicheck.yml` schema/reference
docs, service/API request models) to construct real `FieldCandidate`s —
`resolve_selected_packs` resolves *which* packs are selected, not what
selecting them changes about the rest of the effective config.

**Updated (2026-07-30): the policy-pack half of that gap is closed.**
`compatibility_evaluation_wiring.resolve_policy_pack_overrides` loads every
selected pack, runs the identical D8 conflict check
`resolve_selected_packs` already runs for the `POLICY` namespace (scoped to
only the policy-kind packs among the given paths), and folds every
non-conflicting pack's `ChangeKind` slug -> `Verdict` assignments into one
merged mapping — the exact shape
`CompatibilityPolicyConfig.overrides` already requires, so the result can
be passed straight through. An `explicit_overrides` parameter (forwarded to
the conflict check, same as `resolve_selected_packs`'s own parameter) is
re-applied after the pack merge so an explicit override always wins, even
for a `ChangeKind` no two packs actually disagreed on. Still not attempted:
a contract/gate pack's own field assignments have no equivalent target —
`ContractConfig`/`GateConfig` are fixed-field dataclasses with no open
field-name -> value bag the way `CompatibilityPolicyConfig.overrides` is,
so folding those needs a per-field router this slice does not build (a
separate, larger piece of work). Not called from any live command yet, same
"land the wiring function itself, fully tested" pattern as every other
function in this module.

### Phase 2 — canonical identity and fact conservation

Build finding identity on ADR-048 principles: most specific available identity,
ambiguity-safe fallback, deterministic joins. Refactor L0 collection before any
contract evaluator changes the gate.

**Gate:** rich+L0 conservation and dedup properties; no detector fact loss.

**Progress:** the tiered identity resolver (`abicheck/finding_identity.py`)
is done — `resolve_function_identity`/`resolve_variable_identity`/
`resolve_change_identity` generalize the mangled-primary + name-based
extern-C fallback already hand-rolled in `diff_symbols._diff_functions`
into one documented canonical/normalized/reduced-tier primitive, following
the same principle ADR-045 established for flat type matching
(`diff_helpers.TypeMap`) and ADR-048 for L5 source-graph nodes
(`buildsource/entity_identity.py`).

A first live call site is now wired: `diff_filtering.py`'s
`_deduplicate_cross_detector` uses `resolve_change_identity(c).primary_id`
as its cross-detector dedup key, replacing the hand-rolled
`(change_category, symbol)` tuple it used before —
`resolve_change_identity`'s own `_EQUIVALENT_CHANGE_CATEGORIES` table
already mirrors that function's `_DEDUP_CATEGORIES` exactly (rich-vs-L0
function/variable add/remove, symbol-version-node pairs), so the swap is
behavior-preserving for every kind that stage collapses — verified both by
a dedicated unit suite
(`tests/test_diff_filtering_cross_detector_identity.py`: same-category/
same-symbol collapses, same-category/different-symbol and
different-category/same-symbol do not, first occurrence wins, the
pre-existing symbol-version-alias special case is unaffected) and by the
full existing FP-rate-gate/tier-accuracy-gate/golden/detector-oracle/
detector-property test suites, all of which exercise this stage indirectly
through `checker.compare` and all passed unchanged after the wiring.

Still **not** wired: `diff_symbols.py`'s own old/new function and variable
*matching* (`_diff_functions`/`_match_old_function`/`_diff_variables`) —
deliberately deferred. That matching engine interleaves elf-only-mode/
unconfirmed-parameter/LLP64 threading, the extern-C ambiguity-resolution
fallback (unique-candidate-only), and interactions with
virtual-method-addition, inline-transition, and hidden-friend detection in
one function; replacing its core join with an identity-primitive lookup is
a substantially larger, higher-risk refactor against that hand-tuned logic
and its extensive golden/FP-rate/tier-accuracy test coverage than the
dedup-key swap above, and does not fit a single well-scoped, independently
verifiable change — it needs its own dedicated pass.

**Re-investigated 2026-07-27, conclusion unchanged.** Re-read
`_match_old_function` end to end (not just this paragraph) to check whether
the assessment above was still accurate rather than assuming it. Confirmed
it still holds, for a reason specific to `resolve_function_identity` itself,
not only `_match_old_function`'s surrounding complexity: the identity
resolver returns exactly one `primary_id` per function and has no built-in
notion of "ambiguous, so don't match" — the extern-C fallback's own
correctness depends entirely on the *caller* counting candidates and
declining to match when more than one shares a name
(`len(extern_c_candidates) == 1`). Keying a lookup dict by
`resolve_function_identity(f).primary_id` instead of the current
`new_by_name` multimap would not eliminate that counting step, only move it
behind a different key — the ambiguity-safe fallback logic still has to be
reimplemented on top, so this would not be the same kind of drop-in swap the
`diff_filtering.py` dedup-key wiring was (that call site only needed a
*key*, never an ambiguity *decision*). Attempting it now would mean
rewriting `_match_old_function`'s core join under this session's normal
verification budget, against golden/FP-rate/tier-accuracy/mutation-score
gates it was never exercised against before — exactly the "does not fit a
single well-scoped, independently verifiable change" case above, not a new
finding. Left deferred; a real attempt needs its own dedicated pass with
room for that full gate re-verification, not a fold-in alongside Phase 3/1
work.

A Hypothesis property suite for the identity primitive itself
(`tests/test_finding_identity_properties.py`, `slow`) is also done, covering
determinism (the same declaration always resolves to the same identity),
that two independently-built but content-identical declarations (modeling
an unchanged entity on the old and new side of a comparison) resolve to the
*same* primary id (never a spurious removal+addition pair), that two
declarations with genuinely distinct verified mangled names never collide
onto the same CANONICAL-tier primary id (never masking a real removal), and
that a batch-shaped finding's identity is invariant under which arbitrary
export happened to be sampled into it.

The end-to-end fact-conservation property test over `checker.compare`
itself is also done (`tests/test_fact_conservation_properties.py`, `slow`):
for randomized old/new public function and variable sets, every removed
symbol always surfaces as a `FUNC_REMOVED`/`FUNC_REMOVED_ELF_ONLY`/
`VAR_REMOVED` finding referencing it, and every retained symbol never does
— exercised through the real, now-partially-wired pipeline (matching +
detection + the identity-based dedup stage above), not a mock.

### Phase 3 — shadow contract evaluator

Implement a leaf `contract_surface`/`contract_evaluation` module with no CLI
imports. Produce relevance, assurance, reasons, and provider ledgers in reports,
but leave the old gate authoritative.

**Progress:** a first cut of the leaf module has landed
(`abicheck/contract_evaluation.py`, `evaluate_change_contract_relevance`/
`evaluate_snapshot_pair_contract_relevance`), computing a
`ContractEvaluationDecision` (relevance/reason/assurance) per finding from
evidence that already exists: `surface.py`'s public-surface resolution
(ADR-024) for the entity-membership question, and `finding_identity.py`'s
identity tiers (Phase 2) for the identity-ambiguity downgrade. It is a true
shadow module — not called from `checker.py`, the CLI, or any report path —
so this is not yet the "produce relevance/assurance/reasons in reports" half
of this phase's gate, only the decision-computation half. Scope is
deliberately narrower than the full phase:

- Only `ContractMode.PUBLIC` and `ContractMode.ALL` are implemented;
  `EXPORTS` raises `NotImplementedError` rather than approximating, since no
  export-root-closure evidence provider exists yet (`surface.py` only
  computes a header-derived public closure, not an export-symbol-rooted
  one — a real `EXPORTS` implementation is separate, scoped follow-up work).
  **Superseded 2026-07-31** — `EXPORTS` is now implemented, see the
  "third mode" note at the end of this phase.
- `ContractRelevance.UNKNOWN_UNPROVEN` is never emitted (see the module's
  own docstring): that value requires a closed-world "the declared evidence
  domain was searched completely" claim this module cannot verify with
  today's evidence providers, so every such case degrades to the weaker
  `UNKNOWN_UNRESOLVED` with reason `required_evidence_incomplete` instead —
  a deliberate under-claim, not a shortcut.
- The `NOT_APPLICABLE` (non-entity) `ChangeKind` set is curated by hand
  (SONAME/RPATH, architecture/file-format identity, security-hardening
  posture, toolchain/runtime identity — ~31 kinds) and explicitly
  non-exhaustive; a kind missing from it simply falls through to ordinary
  entity classification rather than crashing.
- The provider ledger this phase's gate calls for ("every shadow delta has
  evidence") is not built yet — a decision today is the relevance/reason/
  assurance triple only, with no persisted per-provider evidence record.
- A finding a pipeline step has already demoted to the audit ledger
  (`post_processing.py`'s `DemoteOffPythonSurface`/
  `DemoteUnreachableInternalChurn`, ADR-024/ADR-028) carries that step's own
  `Change.surface_exclusion_reason` — `evaluate_change_contract_relevance`
  consults it directly before falling back to a from-scratch
  `classify_change_surface` recomputation, since re-deriving from the raw
  surface pair alone can disagree with the specialized detector that
  produced it (an off-Python-surface finding has no C-header surface to
  recompute against at all; an internal-namespace finding's own dedicated
  leak check is a finer-grained, different reachability model than this
  module's coarse public/private split).
- `classify_change_surface`'s `True` verdict is not itself proof of
  confirmed public-root/closure membership — it also covers `surface.py`'s
  own anti-hiding "cannot place this finding, so keep it" fallback (an
  implicated type absent from either snapshot's type universe entirely, or
  an internal-namespace type deferred to the internal-leak detector).
  `_in_surface_result_is_confirmed()` distinguishes the two via
  `public_symbols`/`public_types` membership (mirroring, not
  reimplementing, `classify_change_surface`'s own candidate derivation via
  `_type_identifiers`), downgrading the conservative-retention case to
  `UNKNOWN_UNRESOLVED` — while still trusting `_NEVER_FILTER_KIND_NAMES`/
  `python_*` findings unconditionally, since those are public by
  construction and would never appear in those sets at all.

Not yet done: wiring this into `checker.compare`'s output (even as a
non-authoritative shadow field), the provider-evidence ledger, the
delta/unresolved-rate/proven-loss measurement this phase's gate requires,
and `EXPORTS` mode.

**Updated (2026-07-29): the first bullet above was stale, not still true —
re-checked against the actual code rather than assumed from this
paragraph's own prior wording.** `checker.compare(..., contract_evaluation:
bool = False)` (`checker.py`'s `_apply_contract_evaluation_shadow`) already
calls `evaluate_snapshot_pair_contract_relevance` and stamps every retained
`Change` (and, per a later fix, every demoted `out_of_surface` finding too)
with its shadow `contract_relevance`/`contract_reason_code`/
`contract_assurance` decision when the caller opts in — this landed in an
earlier session than this note and the paragraph above was simply never
corrected afterward. Report surfacing is done too:
`reporter.py`'s `_add_contract_evaluation_fields` serializes those three
fields onto both an ordinary `changes` JSON entry and an
`out_of_surface_changes` entry (covered by `tests/test_report_schema.py`'s
`TestContractEvaluation*` classes), and the flag threads all the way
through the Tier-2 service boundary ADR-049 §3.1 names as the resolution
point — `service.py`'s `compare_snapshots`/`run_compare`/`CompareRequest`
all accept and forward `contract_evaluation` (`tests/test_service_unit.py`'s
`TestContractEvaluationThreading`).

What is genuinely still missing, now that the false "not wired at all"
claim is corrected: **no live front end sets `contract_evaluation=True`.**
It is reachable only by calling the Python API directly. The obvious next
front end, the `abi_compare` MCP tool (`mcp_server.py`), does not accept it
either (confirmed: no `contract_evaluation` mention in that file at all) —
adding it there is a small, precedented change (mirrors the existing
`diagnostic_comparison: bool = False` passthrough parameter immediately
above it) and does not touch `cli.py`. Exposing it on the `compare` CLI
command itself is a real, separate blocker, not just undone work:
`abicheck/cli.py` is at exactly 2000 lines, the AI-readiness hard cap — no
new `@click.option` can land there at all until some existing option family
is first extracted to a sibling module (the pattern this file's own
"Adding a new top-level command" section already documents for large
command groups), which is its own scoped prerequisite, not a drive-by
alongside this wiring.

**Updated (2026-07-29, same PR as the note above): MCP is now the first
live front end.** `abi_compare` accepts `contract_evaluation`, forwarded
through `service.compare_snapshots`, exactly as the paragraph above
proposed (Codex review caught this note going stale within the same PR
that shipped the wiring — flagged here explicitly rather than silently
edited, matching this section's own established practice above). Every
finding in the response gains the shadow fields when the caller opts in,
including scoped-only findings synthesized by `--used-by`/
`--required-symbol` scoping *after* `compare()` already ran — those are
stamped `IN_CONTRACT` directly under a new `explicit_consumer_or_required_symbol_evidence`
reason code (`contract_relevance_types.CONTRACT_REASON_CODES`), not run
through the header-surface evaluator: an explicit required symbol or a
concrete consumer's actual import is section 4.3 item 1's *strongest*
public-evidence tier, stronger than and independent of header-derived
public root membership, so evaluating it against a possibly-unresolved
header surface (routine for a binary-only `used_by` snapshot) would
misclassify authoritative evidence as merely `unknown_unresolved`. Still
missing, unchanged from above: the provider-evidence ledger and `EXPORTS`
mode.

**Updated (2026-07-29): the `compare` CLI flag landed, unblocking the
line-count cap noted above.** `cli.py` was at exactly the 2000-line hard
cap, so no new `@click.option` could land there directly. Rather than
extending `IMPORT_CYCLE_ALLOWLIST` or growing the file past the cap, the
ADR-043 app-usage/required-symbol scoping option family
(`--used-by`/`--verify-runtime`/`--required-symbol`/`--required-symbols`,
previously four separate inline `@click.option` stacks on `compare_cmd`)
was extracted into a single `cli_options.app_usage_scope_options` decorator
— the same "shared utility flags go through a decorator" pattern this file
already documents for `scope_options`/`severity_options`, just applied to a
family that happened to have only one call site rather than several. That
freed 27 lines (`cli.py`: 2000 → 1973), enough headroom for `--contract-
evaluation` (`is_flag=True, default=False`) to be added directly. It threads
through `cli_compare_helpers.run_compare` into `service.compare_snapshots`
exactly the way the MCP tool's own `contract_evaluation` parameter does —
same shadow-evaluator fields, same advisory-only guarantee. Mirroring
`--diagnostic-comparison`'s own precedent, `--contract-evaluation` is
explicitly rejected (`click.UsageError`) on a directory/package (release)
compare via `_reject_set_input_flags`, since that per-library fan-out
doesn't wire the shadow evaluator either. New tests:
`tests/test_cli_compare_contract_evaluation.py` covers keyword forwarding,
the off-by-default case, a real end-to-end JSON report with actual stamped
`contract_relevance`/`contract_reason_code` fields (not a mock), the
`--help-all` text, and the directory/package rejection — plus the frozen
`compare` option-set snapshot in `tests/test_cli_contract.py` and the
generated `docs/reference/cli-reference.md` were updated accordingly. Also
added a `COMPARE_FLAG_BUDGET_RAISES` ledger entry
(`cli_options.py`) for the new visible flag, since `--contract-evaluation`
pushed `compare`'s visible-option count one past the existing budget.

**Codex review on the same PR found a real gap in this slice**:
`--contract-evaluation` combined with `--used-by`/`--required-symbol`
stamped nothing for `scoped_only_changes` (fresh `Change` objects
`scope_diff_to_app`/`scope_diff_to_required_symbols` synthesize, e.g. a PE
ordinal retarget), for synthetic missing-contract-label entries, or
override a weaker header-derived decision on an existing `result.changes`
entry the scoping pass marks relevant — the shadow evaluator runs before
app-usage/required-symbol scoping applies, and nothing in the CLI path
re-stamped afterward. The MCP `abi_compare` tool already had this exact
fix (`_stamp_explicit_scope_contract_evaluation`), but it was private to
`mcp_server.py`. Fixed by promoting it to a shared, public function,
`contract_evaluation.stamp_explicit_scope_contract_evaluation` (mcp_server.py
now imports and aliases it instead of keeping its own copy), and calling it
from `cli_compare_helpers.run_compare` (for `result.changes`/
`scoped_only_changes`, right after scoping and before rendering) and
`cli_compare_fold._fold_scoped_compat_into_text` (for the synthetic
missing-label dict entries, via a new `contract_evaluation` parameter).
New tests in `tests/test_cli_compare_contract_evaluation.py`
(`TestUsedByScopingStampsExplicitEvidence`) mirror
`tests/test_mcp_server_unit.py`'s existing coverage for the same fix on the
MCP side.

**A second Codex review round on the same PR found two more real gaps.**
(1) `cli_options.MCP_CLI_NAME_MAP["contract_evaluation"]` was still `None`
with a stale comment claiming no `compare` CLI equivalent existed — left
over from before this PR's own CLI-flag work, silently exempting the new
flag from the `cli-contract`/parity gate that keeps the MCP tool and native
`compare` command's vocabularies in sync. Fixed by mapping it to
`--contract-evaluation` like every other shared option. (2) `--contract-
evaluation` combined with `--show-redundant`/`scope.show_redundant: true`
rendered a restored redundant finding (e.g. a `func_params_changed`
subsumed by a `type_size_changed` root) with none of the promised contract
fields: `checker._apply_contract_evaluation_shadow` only ever stamped
`kept` + `pp_ctx.out_of_surface`, but the redundant bucket is re-merged
into `result.changes` entirely in the CLI layer
(`cli_helpers_compare._merge_redundant_changes`), long after the shadow
evaluator already ran. Fixed by threading `DiffResult.redundant_changes`
(`redundant_for_report` in `checker.compare`) into
`_apply_contract_evaluation_shadow` as a new `redundant` parameter, stamped
the same way as any other finding — restoring a redundant change is not a
different evidence tier, just a display-dedup decision being reversed. New
test: `tests/test_report_schema.py::TestReportValidatesAgainstSchema::
test_contract_evaluation_stamps_redundant_bucket` (unit-level, directly
exercising `_apply_contract_evaluation_shadow`'s new parameter — a natural
end-to-end fixture that gets a *real* detector to emit a root-type-matching
`FUNC_PARAMS_CHANGED` redundant relative to its own `TYPE_SIZE_CHANGED` was
attempted and found fragile/out of scope for this fix: `_match_root_type`
requires the root type's name to appear verbatim in *both* the old and new
value text of the derived change, which a real signature-level parameter
change does not naturally produce without also changing the param's own
type spelling).

**A third review round (Codex + CodeRabbit) found two more real gaps, plus
two applied quality nits.** (1) Codex, P1: an ordinary `compare --contract-
evaluation` run (default markdown format, no `--format json`) was
byte-for-byte identical to a run without the flag — only the JSON renderer
(`reporter._add_contract_evaluation_fields`) ever surfaced the stamped
fields, so the flag's own help text ("Stamps each finding in the report")
was false for the CLI's default output. Fixed by rendering a `Contract:
<relevance> (<reason_code>), assurance: <level>` line in
`reporter_markdown._format_change_md` (a no-op when the `Change` was never
stamped) — shared by every markdown-based report shape (full, leaf, root-
cause) since they all route through this one per-finding formatter. (2)
Codex, P2: a finding a `--suppress` rule matched was moved to
`DiffResult.suppressed_changes` before `_apply_contract_evaluation_shadow`
ever ran (which only evaluated `kept` + `out_of_surface` + `redundant`), so
its audit-trail entry (`reporter._suppressed_change_entry`) silently lost
its contract decision even though the finding itself stays visible in the
report. Fixed by threading a new `suppressed` parameter into
`_apply_contract_evaluation_shadow` the same way as `redundant` above, and
calling `_add_contract_evaluation_fields` from `_suppressed_change_entry`.
New tests for both:
`tests/test_report_schema.py::TestReportValidatesAgainstSchema::
test_contract_evaluation_renders_in_markdown_report`/
`test_contract_evaluation_stamps_suppressed_changes`.

The two CodeRabbit nits applied in the same pass: hoisted the
`"explicit_consumer_or_required_symbol_evidence"` reason-code literal
(previously duplicated across `stamp_explicit_scope_contract_evaluation`'s
two assignment branches) into one `_EXPLICIT_SCOPE_REASON_CODE` constant;
and factored the `scoped_relevant_finding_ids`/`scoped_only_changes`
traversal — independently hand-copied in both `cli_compare_helpers.py` and
`mcp_server.py`, which is exactly the class of duplication that let the P1
finding above go unnoticed in one of the two call sites for a full review
round — into one shared `contract_evaluation.stamp_scoped_result_findings`.
That hoist required care: a naive version importing `reporter._finding_id`
directly closed a real `checker -> contract_evaluation -> reporter[...] ->
checker` cycle (`checker.py` already imports `contract_evaluation.py`
function-locally, and `reporter.py`/`reporter_markdown.py` both import
`checker.py` at module level) — caught immediately by the
`import-cycle-growth` AI-readiness gate, not discovered later. Fixed by
having `stamp_scoped_result_findings` accept `finding_id` as an injected
callable parameter instead of importing it itself; both call sites already
had their own `_finding_id` import in scope for other reasons, so this
closes the duplication without adding a new cross-module edge.

**A fourth review round (Codex) found one more audit bucket with the
identical unstamped-ledger shape as the redundant/suppressed fixes
above.** A finding `--reconcile-build-context` clears from `kept` (a
context-free header-parse phantom the build's active defines prove never
happened, ADR-039) stays visible in its own ledger
(`reporter._add_reconciled`, `build_context_reconciled.changes`), but
reconciliation runs *before* `_apply_contract_evaluation_shadow` in
`checker.compare`'s own pipeline order — the reconciled finding never
reaches `kept`, so its ledger entry never got a contract decision. Fixed
the same way as the two buckets before it: `_apply_contract_evaluation_shadow`
gained a third optional `reconciled` parameter (folded into `all_changes`
alongside `redundant`/`suppressed`), threaded from `checker.compare`'s own
already-in-scope `reconciled` local, and `_add_reconciled` now calls
`_add_contract_evaluation_fields` on each ledger entry. Two new tests:
`test_contract_evaluation_stamps_reconciled_bucket` (unit-level, mirroring
the redundant-bucket test's direct-call pattern) and
`test_contract_evaluation_stamps_reconciled_bucket_end_to_end` (a real
`--reconcile-build-context` false-positive clear, reusing
`test_diff_reconcile.py`'s own canonical `_fp_pair()` fixture, through the
real JSON renderer) — unlike the redundant-bucket fix, a natural real-
detector fixture *was* readily available here via existing test
infrastructure, so both a unit and an end-to-end test landed.

At this point, four independent review rounds (two Codex, one CodeRabbit,
one more Codex) have each found exactly one more unstamped bucket/gap in
this same feature — `scoped_only_changes`/missing-labels,
`redundant_changes`+`MCP_CLI_NAME_MAP`, markdown rendering+
`suppressed_changes`, and now `reconciled_changes`. `Change.contract_relevance`
is genuinely a **shadow, cross-cutting field**: every place `checker.py`'s
pipeline can pull a finding out of `kept` into a side ledger before
`_apply_contract_evaluation_shadow` runs is a place that ledger can go
unstamped unless it's explicitly threaded through. `kept` +
`pp_ctx.out_of_surface` + `redundant` + `suppressed` + `reconciled` is
believed to be the complete set of pre-shadow-evaluator side buckets as of
this pass (grep for `DiffResult` fields populated from a list separate
from `changes` turns up no further candidates), but this pattern — a
*new* opt-in post-processing step landing in the future and creating a
*sixth* bucket without updating `_apply_contract_evaluation_shadow` to
match — is a real, structural risk worth naming explicitly for whoever
adds the next one.

**A fifth review round (Codex, same PR) found the remaining gap in text-
format rendering: `_fold_scoped_compat_into_text`'s markdown/text/review
branch (not the JSON branch fixed earlier) still lost the contract
decision for `--used-by`/`--required-symbol` scoped-only findings and
missing-contract labels.** The JSON branch (fixed earlier this PR) stamps
a fresh dict for each missing label and reuses the already-stamped
`Change` for `scoped_only`; the markdown/text/review branch builds its own
plain bullet-text lines from the same two collections but never read
either's contract fields — so the *default* CLI invocation
(`compare --used-by ... --contract-evaluation`, no `--format`) reported the
gated finding with zero contract information, the identical shape as the
earlier markdown-rendering P1 fix but for this one text-append code path
specifically. Fixed by appending a `[contract: <relevance>
(<reason_code>)]` tag to each missing-label/scoped-only line, gated on
the same `contract_evaluation` parameter. New tests:
`test_used_by_missing_symbol_gets_contract_evaluation_in_markdown` /
`test_used_by_missing_symbol_omits_contract_tag_in_markdown_by_default`.

Separately, a companion finding named the remaining unaddressed formats —
`sarif`/`junit`/`html` still never render `Change.contract_*` at all
(neither the ordinary per-finding fields nor this scoped fold-in),
contradicting the flag's own help text if read as "every format". Rather
than build out three more renderer integrations in the same PR, the CLI
help text was corrected to state precisely which formats render the
fields today (`json`/`markdown`/`text`/`review`) and which don't yet
(`sarif`/`junit`/`html`) — the "explicitly restrict" alternative Codex's
own P1 finding offered, since a truthful, scoped help text is a complete
fix on its own and extending three more renderers is real, separately-
scoped follow-up work.

**Updated (2026-07-30):** two more Codex findings on the same
`--contract-evaluation` + scoped-gate combination, both in
`cli_compare_fold._fold_scoped_compat_into_text`'s markdown/text/review
branch: (1) the missing-label and `scoped_only` lines added
`contract_relevance`/`contract_reason_code` but dropped `contract_assurance`
even though the JSON branch and `reporter_markdown._format_change_md`
both already render it — fixed by appending `, assurance: <level>` to
both lines, verified against the existing
`TestUsedByScopingStampsExplicitEvidence` markdown tests plus assurance
assertions. (2) `--report-mode root-cause` never reached this fold-in at
all — it is deliberately skipped for root-cause markdown (see the
skip-reason comment above) because `reporter_markdown._to_markdown_root_cause`
already merges `scoped_only`/missing-label findings into its own
root-cause groups. That merge covers `scoped_only` for free (grouped
`Change` objects render via `_format_change_md`, which already reads
already-stamped `contract_*` fields), but the `missing_labels` loop builds
its own plain bullet line independently, with no `Change` object to read a
stamped decision off of — so a missing-contract label's own tag was
silently dropped in root-cause mode specifically. A first fix attempt
tried to *derive* whether `--contract-evaluation` was active from data (any
already-stamped finding in `changes`/`scoped_only`), to avoid growing
`to_markdown`'s public signature — but that heuristic breaks precisely for
the common case this bug covers: a run whose *only* finding is the
missing label itself has nothing else stamped to derive the signal from,
so the heuristic silently stayed off. Fixed properly instead by threading
an explicit `contract_evaluation: bool = False` parameter through the full
call chain — `render_output`/`_render_output` (both the shared
`service_render.py` version and its `cli.py`/`mcp_server.py`-local
duplicates) → `reporter_markdown.to_markdown` →
`_to_markdown_root_cause` — mirroring how `contract_evaluation` was
already threaded to the non-root-cause fold-in path. New tests:
`test_used_by_missing_symbol_gets_contract_evaluation_in_root_cause_mode` /
`test_used_by_missing_symbol_omits_contract_tag_in_root_cause_mode_by_default`.

**Updated (2026-07-30): the `--contract-evaluation` help text itself was
overclaiming, on two points at once (Codex review, fresh evidence).** (1)
It listed `--format text` as a rendering surface, but `compare`'s own
`--format` is a `click.Choice(["json", "markdown", "sarif", "html",
"junit", "review"])` — there is no `text` format for this command; that
word only ever meant something inside `_fold_scoped_compat_into_text`'s
own internal `fmt in ("markdown", "text", "review")` branch condition
(shared plumbing, not a real CLI choice). (2) `--format review` routes
through `reporter_markdown.to_review_digest` — a compact,
reviewer-facing counts-table-plus-top-impacted-symbols digest that never
reads `Change.contract_*` at all, confirmed by reading it end to end (its
"top impacted symbols" list is a bare `- {symbol} — {kind}` line, no
`_format_change_md` call). The only place `--format review` renders
anything contract-related is the `--used-by`/`--required-symbol`
scoped-gate appendix (shared with markdown/text via
`_fold_scoped_compat_into_text`) — so a plain `compare --contract-
evaluation --format review` run with no scoping flag is, correctly per
Codex's fresh evidence, byte-for-byte unaffected by the flag. Fixed by
correcting the help text to state precisely what renders where: per-finding
in `json`/`markdown`; in `review` only via the scoped-gate appendix, never
its own top-impacted-symbols list; still not in `sarif`/`junit`/`html`.
Regenerated `docs/reference/cli-reference.md`; no code path changed, so no
new test was needed beyond the existing `test_help_all_mentions_flag`.

**Updated (2026-07-30): the identical MCP-side gap the CLI fold-in fix
already closed (Codex review, fresh evidence).** `abi_compare`'s own
`_fold_scoped_compat_into_text` call (`mcp_server.py`) never forwarded
`contract_evaluation` at all -- for `output_format="markdown"`/`"review"`,
`response["report"]` (the raw rendered text MCP returns for these formats)
carried no `[contract: ...]` tag on a missing-symbol/missing-entrypoint
finding, even though the same finding in `output_format="json"` was
already correctly stamped (that path has its own separate post-`json.loads`
fix, landed earlier this pass). Fixed by passing
`contract_evaluation=contract_evaluation` into that call, mirroring the
CLI's `run_compare` (`cli_compare_helpers.py`), which already threads it
the same way. New test:
`test_used_by_missing_symbol_gets_contract_evaluation_in_markdown_report`
(`tests/test_mcp_server_unit.py`) -- calls `abi_compare` with
`output_format="markdown"` and asserts the returned `response["report"]`
text carries the tag.

**Updated (2026-07-30): two more real gaps in the same "thread contract_
evaluation to every sibling call site" pattern, both caught fresh (Codex/
CodeRabbit review).** (1) `cli_compare_helpers.run_compare`'s *secondary*
`_render_output` call (the `--secondary-format`/`--secondary-output`
side-channel report) never forwarded `contract_evaluation`, unlike the
primary render call right above it and the secondary
`_fold_scoped_compat_into_text` call right below it in the same block --
a `compare --contract-evaluation --secondary-format markdown` run's
secondary output silently carried no contract fields. Fixed by adding
`contract_evaluation=contract_evaluation` to that call, matching its two
siblings. (2) `reporter_markdown._append_suppression_note` (the "N change(s)
suppressed via suppression file" bullet list, rendered in every markdown
report shape) built its line from only `sc.symbol`/`sc.description`,
never reading the `contract_*` fields `_apply_contract_evaluation_shadow`'s
`suppressed` bucket already stamps (landed earlier this pass) -- so a
suppressed, contract-stamped finding's *JSON* audit entry carried the
decision while its *markdown* audit line did not, the identical
JSON-vs-markdown gap already fixed for `_format_change_md` and the scoped-
gate fold-in, just in this third, independent rendering site. Fixed by
appending the same `[contract: <relevance> (<reason_code>), assurance:
<level>]` tag when `contract_relevance` is present. New tests:
`test_contract_evaluation_renders_suppressed_changes_in_markdown`
(`tests/test_report_schema.py`); the secondary-render fix has no
dedicated new test (no per-call-site regression test existed for any of
`run_compare`'s other secondary-render kwargs either -- covered
transitively by the existing primary-render contract-evaluation tests,
which exercise the identical code path with the identical parameter).

**Updated (2026-07-30): a real logic bug in the evaluator itself, not
another rendering gap (Codex review, fresh evidence).** `--post-manifest`
combined with `--no-scope-public-headers` (ALL mode) let
`evaluate_change_contract_relevance`'s `if mode is ContractMode.ALL:
return _all_mode_decision()` shortcut run *before* the function ever
consulted `change.surface_exclusion_reason` -- so a concrete export
`FilterNonPublicSurface._run_allowlist` already demoted to
`pp_ctx.out_of_surface` for `_REASON_POST_MANIFEST_NOT_COMMITTED` (and
therefore listed in the report's own `surface_scope.out_of_surface_changes`)
was simultaneously stamped `contract_relevance: IN_CONTRACT` with
`COMPLETE` assurance -- directly contradicting the report's own other
ledger for the same finding. The existing `surface_exclusion_reason` check
(`_ALL_SURFACE_REASONS`) only ran on the `ContractMode.PUBLIC` path, one
branch below the ALL-mode shortcut, so it never got a chance to apply.
Not fixed by moving the *whole* check earlier, though: `_ALL_SURFACE_REASONS`
also contains header-origin reasons (`REASON_PRIVATE_HEADER`,
`REASON_SYSTEM_HEADER`, `REASON_OFF_PYTHON_SURFACE`, ...) that ALL mode
*deliberately* treats as irrelevant -- that's the entire point of
`--no-scope-public-headers` (already covered by the pre-existing
`test_all_mode_ignores_pipeline_surface_exclusion_reason` regression test,
confirmed still green). `_REASON_POST_MANIFEST_NOT_COMMITTED` is different
in kind: it's an explicit, exact-manifest fact (ADR-049 D2's own "exact
manifests" evidence provider), not a header-origin classification, so it is
authoritative regardless of which contract mode is selected. Fixed with a
narrow, mode-independent check for specifically that one reason, inserted
between the `NOT_APPLICABLE`-kind check and the `ContractMode.ALL`
shortcut -- every other reason in `_ALL_SURFACE_REASONS` still only applies
on the `PUBLIC` path, unchanged. New test:
`test_post_manifest_not_committed_reason_is_terminal_under_all_mode_too`
(`tests/test_contract_evaluation.py`), mirroring the existing PUBLIC-mode
regression test for the identical reason but asserting the ALL-mode case.

**Updated (2026-07-30): a fourth independent JSON serialization site of the
same demoted findings never carried the stamp (Codex review, fresh
evidence).** `result.out_of_surface_changes` is serialized twice in a JSON
report: once under `surface_scope.out_of_surface_changes` (already stamped,
per the P1 fix above) and once more, independently, under `reporter.
_scope_dict`'s own `scope.filtered_internal_changes` ledger (ADR-024/issue 235's
older, `--scope-public-headers`-only public-surface-scoping block) --
the *same* `Change` objects, but `_scope_dict` built its own bare
kind/symbol/description dict from scratch rather than routing through
`_add_contract_evaluation_fields` (or `_change_to_dict`) the way every
other serialization site in this fix already does. A `--contract-evaluation`
consumer reading `scope.filtered_internal_changes` (the older, more
established ledger) rather than `surface_scope.out_of_surface_changes`
(newer) therefore missed the decision entirely, even though the sibling
ledger for the identical finding carried it. Fixed by extracting a small
`_filtered_internal_entry()` helper that calls
`_add_contract_evaluation_fields` the same way, keeping `_scope_dict`
itself a one-line list comprehension over it. New test extends
`test_contract_evaluation_stamps_demoted_out_of_surface_findings`
(`tests/test_report_schema.py`) with an assertion against `payload["scope"]
["filtered_internal_changes"]` alongside its existing `surface_scope`
assertion, on the same fixture/result -- confirming both ledgers agree,
not just that each independently contains *a* stamped entry.

Measure:

- delta by old/new decision;
- unresolved rate by provider/domain/platform;
- proven public-break losses;
- proven false-positive reductions.

**Gate:** every shadow delta has evidence and stable identity; zero unexplained
fact loss.

**Updated (2026-07-31): the shadow evaluator's third mode landed — `EXPORTS`
is implemented, backed by its own evidence provider.** Every note above says
`EXPORTS` raises `NotImplementedError` "since no export-root-closure evidence
provider exists yet"; that provider now exists, in one new leaf module,
`abicheck/export_surface.py` (`ExportSurface`/`compute_export_surface`/
`observed_export_names`). It answers a genuinely different question than
`surface.py` rather than re-scoping the same one: roots are the declarations
whose own linker symbol appears in the binary's **observed export table**
(ELF `.dynsym` defined symbols, the PE export directory, the Mach-O export
trie — unioned, since one snapshot can legitimately carry more than one), not
`Visibility.PUBLIC` declarations, and no header origin demotes anything
anywhere. The closure over the raw record/enum/typedef graph reuses
`surface.py`'s own `_index_surface_types`/`_walk_type_closure`, so
the two domains cannot drift apart in *how* they follow fields, bases, and
typedef targets — only the seeds differ (plus one local, additive key added to
the returned index, described under owner seeding below), which is exactly the
difference ADR-049 D2 draws between the two modes.

`evaluate_change_contract_relevance`/`evaluate_snapshot_pair_contract_relevance`
gained `exports_old`/`exports_new` parameters, required when and only when
`mode` is `EXPORTS`: omitting them is a `ValueError`, never a silent
degradation to a header-derived answer for a domain that is not
header-derived (that would misrepresent the mode rather than implement it).
The decision path follows Section 7's `exports` row clause by clause — an
export root or a closure member is `IN_CONTRACT`
(`export_root_membership`); a *known* entity outside both, after a complete
traversal, is `PROVEN_OUT_OF_CONTRACT`; incomplete root/graph or identity
evidence is `UNKNOWN_UNRESOLVED` — and it is dispatched **before** the
`public` path's own `_ALL_SURFACE_REASONS` fast path, since every reason in
that set is a header-origin/reachability classification this domain treats
as "unrelated and advisory". The one cross-domain exclusion, `--post-manifest`
(an exact committed-*export* manifest), is still checked ahead of every mode
branch, unchanged.

Deliberately conservative, every guard aimed at the same failure direction —
a false `PROVEN_OUT_OF_CONTRACT`, the one that would hide a real break.
`ExportSurface.exclusion_is_provable` gates the exclusion branch and requires
all three of: an export table that was actually captured (a captured-but-empty
one is indistinguishable from a failed parse in the recorded data, so it
counts as absent); at least one declaration resolved to a root (an observed
table that matched nothing is a mangling-scheme gap, not proof of emptiness);
and **no ABI-relevant observed export left unaccounted for** — an export no
declaration matched is a real entry point whose own signature, and therefore
whose own type closure, the snapshot knows nothing about, so it could be
exactly what reaches the entity being judged. Two further guards sit on top:
an entity absent from the snapshot's own symbol/type universe (a macro, a
Python-API-axis finding) is unplaceable rather than proven-excluded, and
proving a *type* unreachable additionally requires `all_roots_typed` —
`has_typed_roots` (i.e. *some* root is typed) is not enough, since a
partial closure proves unreachability only from the roots it covers, and
`all_roots_typed` is strict down to the individual parameter (one `"?"`
sentinel, what `dwarf_snapshot._process_param` writes for a missing
`DW_AT_type`, leaves that root's closure incomplete).
"ABI-relevant" delegates to `elf_symbol_filter.is_abi_relevant_elf_symbol`,
the repo's existing owner of that judgment, so `_init`/`_fini`/thunks/
transitive stdlib exports don't count as unexplained — applied per export
table and only where its conventions hold (ELF and Mach-O, matching what
`dumper.py` itself does), never to PE, whose MSVC-decorated names would
otherwise lose a legitimate export like `api__v2` to the ELF "`__` means
private" heuristic.

**No declared-public evidence crosses into this domain, in either
direction.** Section 7's `exports` row is unconditional — "Roots/closure are
`IN_CONTRACT` ... Public-header/manifest/consumer failures are unrelated and
advisory" — so `--public-symbol` (`force_public_symbols`) and
`--post-manifest` (`public_surface_allowlist`) neither add an entity to this
contract nor remove one from it: neither observes an export, and a user
assertion can make an unexported declaration neither exported nor
un-exported. Consequently this mode dispatches ahead of *every* header-domain
shortcut in `evaluate_change_contract_relevance`, including the
POST-manifest exclusion the other two modes still honor. (An earlier revision
had the manifest exclusion win here, on the reasoning that a committed-export
manifest can only ever narrow the export set — the plan does not say that,
and the ADR's own table is unconditional.)

A finding's own membership is decided at the right level: a symbol-level
finding by whether its own linker symbol is an export root, a type-level or
member-level one by the closure. Letting a symbol-level finding match the
closure would classify an unexported internal helper `IN_CONTRACT` merely
because its `caused_by_type` happens to be reachable from some *other*
exported signature. Lookup aliases are ambiguity-checked in both directions:
rootness is decided by linker identity alone (never the demangled-name or
bare-tail aliases `_symbol_keys` adds for finding lookup), and an alias a
*non*-root declaration also answers to is dropped from `export_symbols`
entirely, so an exported `ns::foo` cannot lend its bare tail to an unrelated
unexported C `foo`.

"Type-level findings never consult a symbol universe" has exactly one
exception, and it is a producer fact rather than a relaxation:
`diff_symbols._check_vtable_index_change` reuses the type-level
`TYPE_VTABLE_CHANGED` for a moved virtual slot but sets `symbol` to the
*method's mangled linker name*. Rejecting it outright left a vtable-slot
change on an observed exported method `UNKNOWN_UNRESOLVED`, with no way back
via the closure either (an Itanium encoding does not yield the owning class to
`_type_candidates`). An **exact** hit on a mangled spelling is admitted for
that reason: `_Z`/`__Z`/`?` are mangling-scheme prefixes no source-level type
name can occupy, so the bare-name collision the blanket rejection exists to
prevent cannot reach this branch. Nothing else about the rule changes — no
tail fallback, and an unexported method's mangled name still resolves the
other way.

Mach-O needs underscore normalization in **both** directions, because its two
producers disagree with the export trie by one underscore each way: clang's
`mangledName` keeps the platform underscore (`__ZN...`) while the trie parser
strips one (`_ZN...`), and the headerless path (`dumper._dump_macho`'s
`_normalize_macho_sym`) strips a *second* one from that already-stripped name
(`ZN...`). Both shifted spellings are tried, and only against the **Mach-O
table's own names** — not the union of every table the snapshot carries — so a
snapshot holding both an ELF and a Mach-O table can't let an ELF export `foo`
make an unrelated `_foo` a root. Export names keep their table provenance all
the way through root matching for this reason, not only for the artifact
filter. An unnamed ordinal-only PE export is carried as the same
`ordinal:<n>` placeholder `dumper._dump_pe` records, rather than dropped for
having an empty `name` — dropping it hid a real entry point whose signature
is unknown.

A method root's owner is seeded only through an **exact** identity hit
against a known record (its `qualified_name`, and its bare `name` only when
no differing qualified spelling was recorded — see below). `owner_class_of`
cannot tell an enclosing *class* from an enclosing *namespace* from the
string alone, so an exported namespace function `api::run()` yields the bare
fragment `"api"`, which `_walk_type_closure`'s own alias-tolerant
`record_by_name` lookup would resolve to an unrelated `other::api` and pull
its whole field closure in.

Exactness has to hold on *both* sides of that match, which the first cut got
half-right: on the castxml/clang path a record's `name` is the bare leaf, so
`other::api` is stored as `name="api"` and the namespace fragment matched it
"exactly" after all — the same collision, re-entered through the record side
(confirmed with a minimal snapshot: `other::api`'s own field landed in
`export_types`). A bare `name` therefore counts as a full identity only when
the producer recorded no differing `qualified_name`. That loses no real
owner: whenever a record carries a qualified name, `owner_class_of` produces
the complete scope chain too — from an already-qualified declaration name, or
from the mangled symbol's full nested-name — so a genuine owner matches on
the qualified key instead.
This is the same collision — and the same fix — `type_reachability.py`
already carries (see this repo's `AGENTS.md`, "sixth finding"); `surface.py`
still has it, deliberately, as that file documents.

Exact matching alone isn't enough on the castxml/clang path, though:
`_index_surface_types` keys its index by `RecordType.name` and its `::` tail,
which for those producers is the *bare* leaf, so `ns1::Foo` and `ns2::Foo`
collapse onto one ambiguous key and neither owner has an unambiguous handle at
all. Dropping the ambiguous key (the only safe option, since seeding it walks
both records) then left an exported `ns1::Foo::bar()` with no class-typed
signature outside its own class's closure, and a layout finding on that
exported class came back `PROVEN_OUT_OF_CONTRACT` — the exact failure
direction every other guard here is aimed at. `compute_export_surface` adds
each record's `qualified_name` to its **own local copy** of that index, which
gives every such record an exact handle without touching `surface.py`'s shared
indexing (relied on by every other consumer of the closure walk, and already
recorded in `AGENTS.md` as needing its own scoped design). The ambiguity set
is computed from the un-augmented index, so the added keys can never make an
existing name look ambiguous.

**Known residue, deliberately not fixed here — an ambiguous bare tail is
still enqueued alongside the qualified spelling.** `_type_identifiers` turns a
signature type `ns1::Foo *` into *both* `ns1::Foo` and the bare `Foo`, and the
bare one still resolves through `record_by_name`'s tail keys to every
same-named record — so a signature naming `ns1::Foo` also pulls in whatever is
reachable only through `ns2::Foo` (confirmed with a minimal snapshot:
`OnlyNs2` landed in `export_types`). Now that the qualified spelling resolves
exactly, that tail adds nothing but noise for this shape. Two things make the
narrow patch the wrong call anyway:

- The identical enqueueing happens again, one edge deeper, inside
  `_walk_type_closure` itself, for a *kept type's own field or base* spelled
  `ns1::Foo` (confirmed the same way). Fixing only the seed set would leave
  the same leak reachable through any record field while making the invariant
  look enforced — the correct fix is one shared rule in `surface.py`'s walk,
  which is exactly the scoped design this PR does not take on.
- Dropping the ambiguous tail key outright — the obvious local shortcut —
  would flip the failure into the *dangerous* direction: castxml's own
  convention lets a signature spell a namespaced record with the bare leaf
  alone, and with no tail key that reference resolves to nothing, which
  reports a genuinely reachable type `PROVEN_OUT_OF_CONTRACT`.

A typedef alias colliding with a record/enum key is the same residue reached
by a second route: `_walk_type_closure` resolves one name through
`snap.typedefs` *and* `record_by_name`, so a global `typedef Foo` in an
exported signature also walks an unrelated castxml-recorded `ns::Foo` (whose
bare `name` is `"Foo"`) and pulls in what only that record reaches —
confirmed the same way. Same location, same direction, same scoped fix.

What *was* fixed locally is the ambiguity bookkeeping that collision exposed:
`_index_surface_types` tallies collisions across the record and enum indexes
only, never `snap.typedefs`, so the colliding name was not flagged in
`ambiguous_type_names` at all and a finding naming it got confirmed against
whichever node the walk happened to reach. `compute_export_surface` now adds
every typedef alias that is also a record/enum index key to its own ambiguity
set, so such a finding resolves `UNKNOWN_UNRESOLVED`/`identity_ambiguous` —
the same treatment a record-vs-record collision already gets. That does not
close the closure leak (the walk still visits both nodes, and a finding about
a type reachable only through the unintended one has an unambiguous name of
its own), which is why it is recorded here rather than presented as a fix.

Note the direction: this residue over-*includes* (an unrelated internal type
reads `IN_CONTRACT`), the opposite of the qualified-owner bug above and of
every other guard in this section. `surface.py`'s tail keys are deliberate
over-keeping — "never hide a real break behind snapshot order" — which is the
right default for the `public` domain and merely noisy for this one.

**Measured, 2026-07-31 — the strictness is satisfiable on the DWARF path.**
An earlier revision of this note guessed that the "no unmatched ABI-relevant
export" rule would make `PROVEN_OUT_OF_CONTRACT` unreachable on a real C++
library, because the ELF table carries `_ZTV`/`_ZTI`/`_ZTS` entries that
`is_abi_relevant_elf_symbol` does not filter (it only drops `_ZTh`/`_ZTv`/
`_ZTc` thunks). That guess is **wrong**, verified against two real
`g++ -shared -g` libraries dumped through `dumper.dump()`: the DWARF path
records those vtable/RTTI symbols as `Variable` declarations, so all 15
exports of a polymorphic two-class library matched, `unmatched_exports` was
empty, and `exclusion_is_provable` was `True`. The header-scoped (castxml)
path was not measured — no castxml in that environment — so it stays
genuinely unknown rather than assumed either way.

**Closed — unresolvable type edges are now tracked, and the domain still
proves exclusions.** A signature, field, or base whose type string resolves
to nothing in the record/enum/typedef indexes stops the closure silently,
yet `exclusion_is_provable` stayed `True` — so a type reachable only through
that missing edge could be reported `PROVEN_OUT_OF_CONTRACT` (Codex review).
`ExportSurface.unresolved_type_edges` records those edges and
`exclusion_is_provable` now requires the set to be empty, alongside the
root-level `unmatched_exports` rule it already had.

What made this worth deferring once was that a *naive* implementation is
useless, not that the guard is wrong. Measured at each granularity on two
real `g++ -shared -g` libraries:

| Granularity of "unresolved" | libmeasure | libpoly |
|---|---|---|
| identifier not in `all_types` | 0 | 2 (`Base`, `string` — both false: the walk resolves bare tails via `record_by_name`) |
| identifier not resolvable by the walk's own indexes | 0 | 1 (`string`) |
| whole type *reference* with no resolvable identifier | 0 | 1 — field `api::Base::tag`, type recorded as bare `"string"` |

That last one is real: DWARF spells the field type `"string"` while the
typedef key is `"std::string"` and the record is
`std::__cxx11::basic_string<...>` — exactly the bare-vs-qualified gap
`AGENTS.md` documents at length for `type_reachability.py`. Resolving it is
what the implemented guard does: `_resolvable_type_spellings` reuses that
module's `_namespace_suffix_spellings` (partial qualification —
direct-clang prints `api::Outer::Inner` as `"Outer::Inner"`) and
`_stripped_signature_spelling` (the stdlib namespace and `__cxx11::`/`__1::`
inline-ABI markers) over every record, enum, and typedef key, rather than
matching index keys literally.

Three further noise sources turned up only once the resolver was measured
against a header-scoped (`--ast-frontend clang`) dump of a library taking a
`std::vector<api::Item>`, and each is excluded for a stated reason rather
than tuned away:

| Reported "missing" edge | Why it is not one |
|---|---|
| `_Tp`, `_Alloc`, `_CharT`, `_Traits` in libstdc++ records' own fields | Template *parameter* names in a generic definition. Toolchain-owned records' internals are no longer scanned (`STDLIB_TYPE_NAMESPACE_PREFIXES`); the closure walk still follows them, so nothing is hidden |
| `_S_local_capacity` | A static constant in an array bound, inside `basic_string` — same exclusion |
| `_Alloc` via `allocator_type`/`pointer` | libstdc++ *member* typedefs, stored under bare unattributable keys by the direct-clang backend. Typedef *targets* are not scanned at all — see below |

Typedef targets are deliberately outside the scan. The motivating example —
a snapshot recording parameter type `Alias` while omitting
`typedef Alias = Internal` — is caught by the *signature* scan, since `Alias`
resolves to nothing; target scanning would add only the narrower
"alias present, target absent" shape, and against a real library it produced
nothing but the bare-key noise above. A dependent spelling (`typename`,
`template` — C++'s own markers, not a naming heuristic) is likewise not an
edge, since it names nothing until instantiation.

**Result, measured after the fix:** a pure-C library and the
`std::`-carrying C++ library both report zero unresolved edges and
`exclusion_is_provable = True`, while a synthetic snapshot whose export
signature names an undeclared type reports that edge and correctly degrades
the same finding from `PROVEN_OUT_OF_CONTRACT` to `UNKNOWN_UNRESOLVED`. The
guard is satisfiable, not vacuous.

Three membership primitives shared by both domains (`_symbol_matches`,
`_type_candidates`, `_confirmed_type_matches`) were extracted from
`_in_surface_result_is_confirmed` rather than reimplemented — the
member-level owner-stripping and the ambiguous-bare-tail rejection this
file's Phase 3 notes describe at length are subtle enough that a second copy
would drift. One deliberate reason-code difference between the domains is
documented at its own call site: an ambiguous-only closure match reports
`identity_ambiguous` under `exports` (the registry's precise code for it)
where `public` reports the coarser `required_evidence_incomplete`; relevance
and assurance agree, only the reason string differs.

Tests: `tests/test_export_surface.py` (the provider in isolation — the three
platforms' tables, roots vs. declaration visibility in both directions,
closure over fields/bases/typedefs, header origin *not* demoting, untyped
roots, the unresolvable case) and `tests/test_contract_evaluation_exports.py`
(the decisions — split into its own sibling file because
`test_contract_evaluation.py` reached the AI-readiness file-size hard cap
(see `AGENTS.md`), the same split `test_contract_evaluation_not_applicable.py`
already is).

**Still open, unchanged by this:** no front end selects `EXPORTS` yet —
`checker._apply_contract_evaluation_shadow` still derives `PUBLIC`/`ALL` from
`scope_to_public_surface`, so the new mode is reachable only through the
Python-level evaluator API. Exposing it is Phase 6's `--contract
public|exports|all` flag, which also needs the corpus validation that phase's
gate requires; and the provider-evidence ledger this phase's own gate names
still does not exist for either domain (`ExportSurface.resolvable`/
`has_typed_roots` is the same coarse per-surface signal
`PublicSurface.resolvable`/`has_provenance` is, not a per-provider
completeness record).

### Phase 4 — snapshot evidence/context split

Persist policy-independent `contract_evidence` and separate
`evaluation_context`, each versioned; add evaluator and identity algorithm
versions. Implement original replay, new-policy re-evaluation, legacy and
unknown-future handling.

**Gate:** byte/order-independent round-trip decisions and explicit mixed-version
failure behavior.

**Updated (2026-07-29):** landed the block *shapes* this phase's Section 5.1
describes, matching the "land the shape first, wire it later" precedent
already established for Phase 0 (`contract_relevance_types.py`) and Phase 1
slice 1 (`compatibility_evaluation_config.py`) — a new leaf module,
`abicheck/contract_evidence.py`, with no other first-party module depending
on it yet:

- `ContractEvidenceBlock` (policy-independent: `providers` — a tuple of
  `ProviderEvidenceEntry`, each wrapping an `EvidenceSearchRecord` plus
  `declarations`/`manifests`/a `TypeGraphSnapshot` — `schema_version`,
  `identity_algorithm_version`);
- `EvaluationContextBlock` (policy-dependent: wraps a
  `CompatibilityEvaluationConfig`, `schema_version`, `evaluator_version`,
  `identity_algorithm_version`; its `field_provenance` property aliases
  `resolved_config.provenance` rather than duplicating it, since Phase 1
  already made that the single source of truth for field-level provenance);
- `DecisionReceiptBlock` (the actual per-finding relevance decisions:
  `evaluated_contract_roots`, `evaluated_type_closure`,
  `relevance_by_finding`);
- `PersistedContractContext`, bundling all three;
- `check_persisted_context_versions_supported()` / `UnsupportedSchemaVersionError`,
  implementing D6 ("unknown future versions fail closed") for each of the
  five independent version fields this phase's gate requires
  (`contract_evidence.schema_version`,
  `contract_evidence.identity_algorithm_version`,
  `evaluation_context.schema_version`, `evaluation_context.evaluator_version`,
  `evaluation_context.identity_algorithm_version`)
  — checked independently, so a mixed-version context (older
  `contract_evidence` replayed against a current `evaluation_context`, the
  ordinary re-evaluation-against-newer-policy case this phase's gate names)
  is explicitly *not* an error; only an individual counter exceeding its own
  current ceiling is. All four version constants reuse Phase 0's
  already-reserved `contract_relevance_types.py` values
  (`CONTRACT_EVIDENCE_SCHEMA_VERSION`/`EVALUATION_CONTEXT_SCHEMA_VERSION`/
  `EVALUATOR_VERSION`/`IDENTITY_ALGORITHM_VERSION`) rather than inventing new
  ones.

All dataclasses are frozen with `__post_init__` validation and tuple/mapping
freezing, matching `compatibility_evaluation_config.py`'s established
pattern — construction order never affects equality (this phase's
"order-independent round-trip" half of the gate), verified directly in
`tests/test_contract_evidence.py::TestRoundTripEquality`.

Not yet done, deliberately out of scope for this slice (same reasoning as
Phase 0/1's own "shape only" landings): wiring into
`dumper.py`/`serialization.py`/`checker.py` so a real snapshot actually
persists these blocks; the original-replay and new-policy-re-evaluation
*procedures* themselves (only their version-compatibility precondition is
implemented); and populating `TypeGraphSnapshot`'s nodes/edges with real
type-graph content (they are deliberately opaque strings for now, mirroring
`ContractEvidenceBlock`'s own "land the shape, not the producer" scope).

### Phase 5 — shared authoritative comparison

Route both direct compare and scan baseline compare through the same core and
same typed config. Add suppression and unsuppressible coverage ledgers in the
normative stage order.

**Gate:** field-for-field parity tests across binaries, snapshots, mixed inputs,
policies, packs, suppressions, and explicit scope.

**Updated (2026-07-29):** landed the first concrete slice of "route both
direct compare and scan baseline compare through the same core" -- not the
full phase, which stays open. §6.3's collector, `collect_l0_export_delta()`,
now lives in one new leaf module, `abicheck/l0_export_delta.py`: the
"re-resolve both sides symbols-only, diff them unscoped, and keep only the
`func_removed_elf_only` fact" logic that recovers a hard ELF/DWARF removal a
macro-gated header pass can hide (`examples/case97_api_depends_on_consumer_env`)
was previously hand-copied verbatim in
`cli_helpers_compare.fold_l0_hard_removals` (direct `compare`) and
`cli_scan_baseline._run_baseline_compare` (`scan --against`) -- each
docstring explicitly cross-referenced the other as its twin, and PR #494's
own regression tests (`tests/test_pr494_scan_regressions.py`) already assumed
they'd stay in lockstep by hand. Both call sites now call the same function;
`fold_l0_hard_removals` keeps only the staleness check that is genuinely
specific to it (re-deriving paths from an already-resolved snapshot that
could have been read back from a stale pre-dumped JSON file -- `scan
--against` already holds the real, freshly-resolved paths and has nothing to
go stale against). New tests in `tests/test_l0_export_delta.py` cover the
collector in isolation (resolve failure, compare failure, the exact
`func_removed_elf_only` fold, and non-matching-kind rejection), independent
of either call site's own staleness/scoping tests. `scripts/check_ai_readiness.py`'s
`IMPORT_CYCLE_ALLOWLIST` gained one new member (`l0_export_delta`) joining
the existing CLI-registration SCC -- the same by-design function-local-import
pattern every other member already uses, not a new dependency direction (see
that allowlist's own inline comment for the reasoning).

**Updated (2026-07-29, same PR as the note above): a second slice landed --
`scan --against` config-surface parity.** `scan_cmd` (`cli_scan.py`) now
carries `@policy_options`/`@scope_options`, the exact same decorators
`compare` uses, giving it real `--policy`/`--policy-file`/`--suppress`/
`--scope-public-headers` flags reusing `compare`'s own
`_load_suppression_and_policy` loader (`cli_params.py`) -- a no-op
returning `(None, None)` when neither `--suppress` nor `--policy-file` is
given, so a plain `scan --against` invocation with none of these flags is
unchanged. The resulting `suppression`/`policy`/`policy_file`/
`scope_to_public_surface` values are threaded through `run_scan_core`
(`scan_engine.py`) into `_run_baseline_compare`'s own `compare_snapshots`
call (`cli_scan_baseline.py`), replacing the previously-hardcoded
`policy="strict_abi"`/`suppression=None`/`scope_to_public_surface=True`.
`_run_baseline_compare` also now calls the same
`cli_compare_helpers._verdict_exit_code` `compare` already uses instead of
its own hand-rolled BREAKING=4/API_BREAK=2 inline mapping.
`abicheck.service_scan.ScanRequest` gained matching
`suppression`/`policy`/`policy_file`/`scope_to_public_surface` fields (all
defaulted to the prior hardcoded behavior) so the Python API gets the same
config surface, threaded into its own `run_scan_core` call in
`service_scan.run_scan`. The MCP `abi_scan`/`abi_estimate` tools do **not**
yet expose these as tool parameters -- deliberately deferred, since adding
MCP-surface parameters needs its own generated-doc regeneration
(`gen_mcp_reference.py`) and tool-schema review, not a drive-by extension
of this CLI/service-layer slice. New tests: `test_scan_baseline_headers.py`
gained a CLI param-surface check
(`test_scan_exposes_against_config_surface_options`) and a kwarg-capture
test asserting `_run_baseline_compare` forwards its own
suppression/policy/policy_file/scope_to_public_surface arguments into
`compare_snapshots` unchanged
(`test_run_baseline_compare_threads_policy_and_scope_to_compare_snapshots`).

**Updated (2026-07-29, same PR): a third slice landed -- the rest of
`compare`'s policy-adjacent config surface, plus real cross-command parity
tests.** `scan --against` now also accepts `--strict-suppressions` (reuses
`_load_suppression_and_policy`'s existing `strict_suppressions` kwarg --
already there for `compare`/`compare-release`, just not threaded from
`scan_cmd` before), `--public-symbol`/`--public-symbols-list` (the
force-public overlay, via the same `_collect_force_public_symbols`/
`_warn_force_public_ignored` helpers `compare` uses), `--pattern-verdicts`,
and `--env-matrix` (loaded via `service.load_env_matrix`, same as
`compare`) -- all four were already plain kwargs `compare_snapshots` itself
accepted (`force_public_symbols`/`pattern_verdicts`/`env_matrix`), so this
is CLI/service plumbing parity, not new engine capability. `ScanRequest`
gained matching fields for the Python API. New:
`tests/test_scan_compare_parity.py` runs `compare` and `scan --against` on
the *same* JSON snapshot pair through Click's `CliRunner` and asserts they
agree end to end (exit code) under identical suppression/scope flags --
unsuppressed removal breaks both (exit 4), the same suppression file makes
both compatible (exit 0), and `--no-scope-public-headers` agrees on both
sides too. This is deliberately narrow (one concrete suppression scenario,
not every flag/input combination §6.4's Gate lists), but it is a real,
executable field-for-field parity assertion between the two commands where
none existed before.

**Two Codex review rounds on this same slice found real, fixed gaps.**
First: `_run_baseline_compare` forwarded `policy_file` to `compare_snapshots`
but not to `prepare_embedded_build_source` (`cli_buildsource_helpers.py`),
which is what actually applies `require_evidence`/evidence-verdict
overrides (ADR-033 D7) -- a `--policy-file` requiring evidence had no
effect on `scan --against`, fixed by threading `policy_file` into that call
too. `--env-matrix` with malformed YAML also raised an uncaught
`ValidationError` instead of `compare`'s existing `AbicheckError` ->
`click.UsageError` handling; fixed the same way. Second: every one of these
config-surface flags only means anything for a `--against` comparison
(`run_scan_core` calls `_run_baseline_compare` only when a baseline is
given) -- without `--against` they were silently parsed (and, for
`--env-matrix`, even validated against a file that could never matter) and
then discarded, which could hide a `--policy-file` requiring evidence the
user actually needed. `scan_cmd` now rejects any of them (via
`ctx.get_parameter_source(...) == COMMANDLINE`) with a `click.UsageError`
(exit 64) when passed without `--against`, rather than accepting silent
no-op configuration. New tests: `test_scan_rejects_comparison_only_flags_without_against`
(parametrized over four of the flags) and
`test_scan_without_against_and_without_comparison_flags_is_unaffected`
(the guard must not fire for a plain audit that touches none of them).

**Updated (2026-07-29, same PR): a fourth slice closed a real, related gap
-- `scan --against`'s own suppression audit trail.** Investigating "no
suppression ledger exists" (below) turned up prior art that was closer than
first thought: `DiffResult.suppressed_changes` ("full audit trail" per its
own field comment, `checker_types.py`) and `reporter.py`'s `_add_suppression`
already give `compare`'s JSON report a per-run list of every finding a
`--suppress` rule silenced, including which rule (`Change.suppression_rule`).
`scan --against` newly honors suppression (this Phase 5's earlier slices)
but its own summary never surfaced *which* finding got silenced -- an
asymmetry between the two commands' audit trails, not a missing concept.
Fixed by adding the equivalent (`suppressed_count`/`suppressed`, capped
independently of the existing gating-findings truncation) to
`_run_baseline_compare`'s summary dict, reusing the existing
`_baseline_finding_dicts` projector. New test
`test_scan_against_exposes_suppression_ledger_like_compare` asserts `scan
--against --suppress --format json` surfaces the same audit trail
end-to-end (not just that the flag threads through in isolation). Also
bumped `SCAN_SCHEMA_VERSION` to `1.4` for these additive `diff` keys (Codex
review caught the missing bump) and pinned a test that had hardcoded the
prior literal `"1.3"` to the live constant instead.

**Updated (2026-07-29, same PR): one more Codex-caught gap in the same
suppression ledger.** `_baseline_finding_dicts`' projection dropped
`Change.suppression_rule` -- the ledger could show *which finding* was
silenced but not *which `--suppress` rule* silenced it, unlike `compare`'s
own `reporter._suppressed_change_entry`
(`impact_assessment.decision.suppression_rule`). Fixed by having
`_baseline_finding_dicts` add a `suppression_rule` key, but only for
`bucket="suppressed"` entries -- the breaking/api_break/risk buckets keep
their exact prior shape unchanged (an existing strict-equality test and two
sibling modules, `cli_compare_release.py`/`stack_report.py`, already pin
that shape). New assertions in both the CLI end-to-end parity test and the
unit-level truncation test confirm the rule label (falling back to a rule's
`reason` when it has no `label`, same as `compare`) round-trips through
JSON.

**Updated (2026-07-29, same PR): the CLI's "reject comparison-only flags
without --against" guard had a Python-API-side gap too.** The `scan_cmd`
guard (added earlier this slice) only lives in the CLI front-end;
`service_scan.run_scan` -- the Python API entry point behind the same
`ScanRequest` fields -- had no equivalent, so a library caller could set
e.g. `ScanRequest(policy_file=..., baseline=None)` and have it silently
accepted and discarded, same failure mode the CLI guard was built to close.
Fixed by adding the identical rejection in `run_scan` itself (raising
`ValidationError`, this module's own established validation-error type,
rather than `click.UsageError` which is CLI-specific) -- gated on
`req.baseline is None OR ScanMode(req.mode) is ScanMode.AUDIT`, since an
explicit `mode="audit"` skips the baseline compare in `run_scan_core` even
when a baseline path is set (a case the CLI guard doesn't need to consider,
since the public CLI has no `--mode` flag left to set it explicitly). Three
new tests in `test_service_unit.py` cover: rejection with no baseline,
rejection with a baseline but `mode="audit"`, and that a real baseline
comparison using the config surface is unaffected.

**Updated (2026-07-29, same PR): the CLI-flag-only reading was itself
incomplete -- project-config resolution was still missing entirely.**
`scan --against` read `--scope-public-headers`/`--public-symbol`/
`--strict-suppressions` from raw CLI values only, never resolving them
through the project's `.abicheck.yml` the way `compare` does (CLI flag >
config > built-in default, ADR-037 D4, `cli_helpers_compare.
resolve_compare_config`). Concretely: a project config declaring
`suppression.strict: true` (already honored by `compare`) silently had no
effect on `scan --against` -- an expired `--suppress` rule that `compare`
rejects would pass through `scan --against` unnoticed. Fixed by resolving
`scan`'s existing `--config`/`build_config` option (previously only used
for the `build.query` gate) through the *same* `resolve_compare_config`/
`discover_project_config` functions `compare` uses -- `scan_cmd` now loads
the project config once (auto-discovered upward from cwd when `--config`
is omitted, matching `compare`) and overwrites its local
`scope_public_headers`/`strict_suppressions`/`public_symbols` with the
resolved (CLI-explicit-beats-config-beats-default) values before loading
suppression/collecting the force-public overlay. Severity/debug/exit-code-
scheme config keys are resolved too (required positional args of the
shared function) but deliberately discarded -- `scan` has no equivalent
flags for them. New test
`test_scan_against_honors_config_suppression_strict_like_compare` runs
`compare` and `scan --against` against the same expired-suppression-rule
fixture with only a config-declared `suppression.strict: true` (no CLI
flag on either side) and asserts both reject it identically (exit 1).

**Updated (2026-07-29, same PR): the config-resolution fix above was
itself incomplete -- two more `resolved_cfg` fields were computed and then
discarded.** `resolve_compare_config` also resolves
`collapse_versioned_symbols` (`scope.collapse_versioned_symbols`) and
`require_justification` (`suppression.require_justification`), but
`scan_cmd` only ever read `.scope_public`/`.strict_suppressions`/
`.public_symbols` off the result. Concretely: an ICU-style version-suffix
rename (most removed symbols reappearing renamed only by version token)
reported `BREAKING` under `scan --against --config` while `compare
--config` correctly demoted it to `COMPATIBLE_WITH_RISK`, and a reason-less
`--suppress` rule was silently accepted by `scan --against --config` even
under a config declaring `suppression.require_justification: true`. Fixed
by reading both fields off `resolved_cfg` (neither has a `scan`-side CLI
flag of its own -- config-only, same as `compare`'s own hidden/demoted
`--collapse-versioned-symbols`/`--require-justification`) and threading
`collapse_versioned_symbols` through `run_scan_core`/
`_run_baseline_compare`/`compare_snapshots` (which already accepted it as a
plain kwarg) and `require_justification` into `_load_suppression_and_policy`
(ditto). New tests: an end-to-end `compare`-vs-`scan --against` parity
check for `require_justification` (mirroring the `strict_suppressions` one
above), and a kwarg-capture unit test confirming
`collapse_versioned_symbols` reaches `compare_snapshots` (constructing a
real ICU-style versioned-rename fixture end-to-end was judged not worth
the added fixture complexity when the kwarg-threading is what was actually
missing, not the underlying detector logic itself).

**Updated (2026-07-29, same PR): three more Codex-caught gaps in the same
config-resolution fix, all fixed together.** (1) The new comparison-config
resolution only ever searched cwd upward (`discover_project_config()`),
while `resolve_compile_context`'s own `compile:` block resolution already
prefers the `--sources` tree root (`discover_build_config(sources)`) --
`scan --against --sources DIR` run from outside `DIR` could resolve its
`compile:` settings from one `.abicheck.yml` and its scope/suppression
settings from a different one. Fixed by adopting the identical precedence
(`explicit --config` > `--sources` root > cwd-upward). (2) The whole
resolution block ran even for a plain one-build audit (no `--against`),
where every field it resolves is comparison-only -- a malformed
*auto-discovered* config could fail an otherwise-unrelated audit outright.
Fixed by gating the entire block on `against is not None`, with
`collapse_versioned_symbols`/`require_justification` defaulting to `False`
outside it (mirrors the "reject comparison-only flags without `--against`"
guard's own reasoning). (3) Even inside that gate, any config parse
failure reaching this code unconditionally raised `click.UsageError` --
but `merge_compile_config` already attempts the *identical* load first
(unconditionally, for an explicit `--config` or one found at the
`--sources` root) and is deliberately best-effort there (warn + fallback)
for anything not explicitly bound to. Reaching this code's own parse
failure can therefore only happen for the cwd-upward fallback
`merge_compile_config` never attempts -- so fail-loud here was both
inconsistent with the established convention and, for the explicit/
sources-root cases, literally unreachable dead code (confirmed via
coverage: the `if explicit_config: raise` branch was never hit by any real
scenario, since `merge_compile_config` always fails first for those two
cases). Simplified to always warn + fall back, matching
`merge_compile_config` exactly. New/updated tests: a sources-root-discovery
end-to-end test (config found via `--sources`, no CLI flag, still rejects
an expired suppression), an audit-mode test asserting
`discover_project_config` is never even called without `--against`, and
the earlier malformed-auto-discovered-config test updated from "must be a
usage error" to "must warn and fall back" (it had encoded the exact
behavior this round's fixes corrected).

Separately, `ScanRequest` (Python API) gained a `collapse_versioned_symbols`
field -- the CLI threads a config-resolved value through `run_scan_core`,
but the typed request object had no equivalent, so a library caller could
not request the same ICU-style version-suffix handling. Wired into the
existing "reject comparison-only fields without a baseline" guard and
`run_scan`'s `run_scan_core` call the same way the other fields are.

**Updated (2026-07-29, same PR): a final Codex-caught gap, this time in
compile-context consistency rather than scope/suppression resolution.**
Gating the whole scope/suppression-resolution block on `against is not
None` and switching to fail-loud-only-for-explicit-config (previous round)
still left one inconsistency: `resolve_compile_context` runs *before* that
block, with the raw CLI `build_config` value only -- so a `scan --against`
run with no explicit `--config` and no `--sources` could have its
scope/suppression settings resolved from a cwd-upward-discovered
`.abicheck.yml`, while that same file's `compile:` block (defines, include
dirs, frontend, std, sysroot) never reached `resolve_compile_context` at
all. A macro- or dialect-dependent header API could then parse under the
wrong context and produce a false `COMPATIBLE` verdict -- exactly the class
of bug `compare`'s own "resolve config once, thread the same path into both
resolvers" pattern already avoids. Fixed by moving all project-config path
resolution + loading + error handling into `cli_scan.py` itself, executed
once, upfront, before `resolve_compile_context` is even called: this
guarantees the path passed into its `build_config` parameter is always
either `None` or a path already confirmed to parse, so
`merge_compile_config`'s own internal reload (which treats any non-`None`
path as user-explicit and would otherwise fail loud on a parse error
regardless of how the path was actually discovered) can never hit that
branch. The later scope/suppression-resolution block was simplified to
reuse the already-loaded config object directly instead of re-discovering
it. New regression test: a cwd-discovered config with a
`compile: {defines: [FOO=1]}` block, verifying the resulting
`CompileContext.gcc_option_tokens` actually contains `-DFOO=1` (spying on
`run_scan_core`'s `compile_context` kwarg), not just that the path gets
threaded through structurally.

Still not yet done, deliberately out of scope for these four slices (each
is real, separately-scoped Phase 5 work): `CompatibilityEvaluationConfig`
(Phase 1) is still constructed by neither command -- "same typed config" is
still just the plan's own vocabulary, not live. The "unsuppressible
coverage ledger" specifically (as opposed to the ordinary suppression audit
trail just closed above) remains undesigned -- there is still no concept in
the codebase for *which* `ChangeKind`s categorically cannot be suppressed
regardless of policy, nor a ledger proving a given run's suppressions never
touched one. The parity test suite above covers one concrete
scenario (plus, now, the suppression-ledger scenario), not
the exhaustive binaries/snapshots/mixed-inputs/policies/packs/suppressions/
explicit-scope matrix §6.4 names in full. `compare`'s remaining
config-surface options that are genuinely out of scope for "shared
authoritative comparison" (`--used-by`/`--verify-runtime`/
`--required-symbol(s)` app-usage scoping, `--follow-deps`/`--search-path`/
`--ld-library-path` dependency-graph traversal, `--probe-matrix` build-config
snapshots, `--post-manifest` POC export-manifest scoping) were deliberately
not ported to `scan --against` -- each is its own subsystem with its own
input model that doesn't obviously map onto `scan`'s classify/tier/level
orchestration, not a plain kwarg `compare_snapshots` already accepts.

**Updated (2026-07-30): `SuppressionList.audit()`/`SuppressionAudit`'s
"still-orphaned piece of related prior art" gap (immediately above) is
closed for `compare` -- wired in as `compare --audit-suppressions`.**
Requires `--suppress` (a `click.UsageError` otherwise, in
`cli_compare_helpers.run_compare` right after suppression loading, mirroring
every other "reject without its prerequisite flag" guard in this file).
When set, `suppression.audit(list(result.changes) + list(
result.suppressed_changes))` runs after `_finalize_compare_result` (the
*pre*-suppression change set, not just `result.changes` -- auditing only
the post-suppression survivors would read every suppression rule that
actually did its job as "stale," since the changes it matched are exactly
the ones no longer in `changes`) and is attached as `result.
suppression_audit`. A new fold function,
`cli_compare_fold._fold_suppression_audit_into_text` (mirroring
`_fold_scoped_compat_into_text`'s own JSON-vs-text branch structure), folds
it into the rendered report: a `suppression_audit` JSON key (`total_rules`,
`stale_rules`, `high_risk_matches`, `expired_rules`, `near_expiry_rules`,
each rule identified by its `label`/`reason`, falling back to
`rule#<index>` only when a rule has neither), or a `## Suppression Audit`
markdown/text/review section built from `SuppressionAudit.summary()` plus
an explicit high-risk-match listing. Threaded to both the primary and
secondary (`--secondary-format`) render/fold call sites from the start,
having learned from `--contract-evaluation`'s own multi-round "forgot the
secondary call site" findings earlier in this same PR. Rejected on
directory/package (release) comparisons, same reasoning and message shape
as `--contract-evaluation`'s identical restriction (the per-library fan-out
has no single result to attach one audit to). `--audit-suppressions` added
to `cli_options.COMPARE_FLAG_BUDGET_RAISES` (a genuine per-run analysis
input, not a stable project setting -- like `--contract-evaluation`
itself). New tests: `tests/test_cli_compare_audit_suppressions.py` (usage
guard, directory/package rejection, JSON stale/high-risk-match rendering,
markdown rendering, default-off, `--help-all` mention).

**Updated (2026-07-30): two Codex/CodeRabbit review findings on the same
`--audit-suppressions` slice, both real.** (1) `_suppression_rule_label`'s
fallback for a rule with neither `label` nor `reason` used the rule's
position *within the filtered bucket* (stale/high-risk/expired/near-expiry),
not its real position in the suppression file -- e.g. the second rule in
the file being the sole stale one rendered as `rule#0`, and two different
rules could each render as "rule#0" across two different buckets.
`SuppressionAudit` has no field carrying a rule's original index (`match_
counts` does, but its own list-typed buckets don't), and threading one
through would touch `suppression.py`'s public dataclass shape for a
display-only concern. Fixed instead by falling back through each of the
rule's own matching selectors (`symbol`/`symbol_pattern`/`type_pattern`/
`member_name`/`source_location`/`namespace`/`entity_namespace`/
`cause_namespace`, rendered as `field=value`) before ever reaching the
`rule#<index>` fallback, which now only fires for a rule with none of
label/reason/any selector set at all (a real rule, per `Suppression`'s own
validation, always has at least one selector, so this last-resort path is
effectively unreachable). New test:
`test_label_falls_back_to_selector_not_bucket_index` (a second, unlabeled
rule that's the sole stale one, asserting it renders via its `symbol=`
selector, not a misleading `rule#0`). (2) The audit was computed *before*
`_apply_used_by_scoping`/`_apply_required_symbol_scoping` ran, so a rule
matching only a scoping-synthesized finding (e.g.
`CONSUMER_REQUIRED_SYMBOL_REMOVED`, never present in `result.changes`) was
misreported as stale, and its potential high-risk match was invisible.
Fixed by moving the audit computation to after both scoping calls and
including `result.scoped_only_changes` in the audited change set. **Not a
complete fix**, documented in the code itself: `scope_diff_to_app`/
`scope_diff_to_required_symbols` (`appcompat.py`) apply suppression to
their own scoping candidates *internally*, before this code ever sees
them -- a rule matching only a candidate suppression already dropped
(so it never reaches `scoped_only_changes` at all) is still invisible to
the audit. Closing that residual gap needs those functions to expose their
own pre-suppression candidate list back to the caller, a separate, larger
change to `appcompat.py` (a module this slice otherwise doesn't touch) that
this fix deliberately does not attempt -- same "real, separately-scoped
follow-up" reasoning as every other `appcompat.py`-adjacent gap already
documented in this file. New test:
`test_rule_matching_scoped_only_change_is_not_reported_stale`
(`tests/test_cli_compare_audit_suppressions.py`, mirroring
`TestUsedByScopingStampsExplicitEvidence`'s existing `scope_diff_to_app`
monkeypatch pattern). Also fixed the MD018 markdownlint warning this same
review round flagged: an unescaped `#235` (issue reference) at the start of
a line, misparsed as an invalid ATX heading -- reworded to "issue 235".

**Updated (2026-07-30): two more real findings on the same
`--audit-suppressions` slice (Codex review, fresh evidence).** (1) The new
top-level `suppression_audit` JSON key was additive but never bumped
`REPORT_SCHEMA_VERSION` or declared itself in the packaged
`compare_report.schema.json` -- `jsonschema.validate` alone wouldn't have
caught this (the schema's `additionalProperties: true` accepts an
undeclared key silently), so a version-aware consumer had no way to detect
this report shape exists. Fixed by bumping `REPORT_SCHEMA_VERSION` to
`"2.24"` (`abicheck/schemas/__init__.py`, with the same per-version
docstring convention every prior bump uses), adding the `suppression_audit`
object schema to `compare_report.schema.json`, and regenerating the
published docs mirror (`scripts/publish_schemas.py`). New test:
`test_suppression_audit_validates_against_packaged_schema` -- deliberately
asserts `"suppression_audit" in schema["properties"]` on top of the plain
`jsonschema.validate` call, since the latter alone can't distinguish a
correctly-declared key from an accepted-but-undeclared one. (2)
`compare --audit-suppressions --dry-run` (no `--suppress`) reported "ok"
even though the identical non-dry-run invocation is rejected: `emit_dry_run`
raises `SystemExit` before `run_compare` ever reaches the post-suppression-
loading guard this fix added earlier. Fixed by moving the validation to
right after the directory/package rejection block -- the same place, and
same "validated ahead of the --dry-run emit" reasoning, `_reject_set_input_
flags` already uses for the identical class of gap. The now-redundant later
guard (unreachable once the earlier one always fires first) was removed,
keeping only the `assert suppression is not None` mypy-narrowing hint at
the actual `.audit()` call site. New test:
`test_rejected_without_suppress_even_with_dry_run`.

**Updated (2026-07-30): one more real finding on `_suppression_rule_label`,
and one restatement of an already-documented, deliberately-deferred gap
(Codex review, fresh evidence).** (1) The selector fallback (added by the
immediately-preceding round's fix) returned only the *first* populated
selector field it found -- but `Suppression`'s selectors combine
conjunctively (e.g. `symbol` + `change_kind` together narrow one rule), so
two distinct unlabeled rules sharing their first selector (the same
`symbol`) but differing on a second (`change_kind`) rendered as the
identical, ambiguous label, defeating the whole point of the earlier fix.
Fixed by rendering *every* populated selector (comma-joined), not just the
first, and adding `change_kind` to the checked field list (present in
`Suppression` but missing from the original fallback's field tuple). New
test: `test_label_includes_every_conjunctive_selector_not_just_the_first`
(two rules sharing `symbol` but differing on `change_kind`, asserting
distinct labels). (2) A second finding restated the residual
`scope_diff_to_app`/`scope_diff_to_required_symbols`-internal-suppression
gap the immediately-preceding round's own fix and plan-doc update already
named explicitly as known and deliberately deferred (a rule that only
matches a scoping candidate suppression drops *before* `scoped_only_changes`
is ever populated is still invisible to the audit) -- not a new finding,
so no additional code change; replied pointing at the existing
documentation rather than re-fixing the same acknowledged gap twice.

**Updated (2026-07-30): two more real findings, both on the same
`--audit-suppressions` help text/rendering (Codex review, fresh
evidence).** (1) The flag's help text repeated the exact `--format text`
mistake `--contract-evaluation`'s own help text made and already had fixed
earlier this pass: `compare`'s `--format` choice is
`json`/`markdown`/`sarif`/`html`/`junit`/`review` -- there is no `text`
format, so advertising an audit output under it was simply wrong. Fixed by
dropping `text` from the documented formats (`markdown/review`, matching
the fold-in's real behavior). (2) `_fold_suppression_audit_into_text`'s
markdown/text/review branch rendered `audit.summary()` (which only gives
*counts* for expired/near-expiry rules, e.g. "1 expired rule(s)") plus
per-rule detail lines for high-risk matches only -- unlike the JSON branch,
which already names every rule in every bucket, the default report gave no
way to tell *which* expired or near-expiry rule needs action without
switching to `--format json`, contrary to the flag's own promise. Fixed by
adding the identical per-rule detail-line treatment for `expired_rules`/
`near_expiry_rules`. New test:
`test_expired_rule_labeled_not_just_counted` (an expired rule with a real
`reason`, asserting its label appears under an "Expired rules:" heading in
the markdown report, not just a bare count).

**Updated (2026-07-30): two more real findings, closing out this pass on
`--audit-suppressions`/`--contract-evaluation` (Codex review, fresh
evidence).** (1) `--report-mode leaf` routes a root `TYPE_*` finding (e.g.
`type_size_changed`) through `reporter_markdown._format_leaf_type_change`
-- a separate code path from `_format_change_md`, which full and root-cause
mode both already use for every finding, type or not. That separate path
never read the already-stamped contract fields, so a leaf-mode type
finding's own decision was silently dropped -- the fourth independent
markdown rendering site this pass has found and fixed the identical gap
in (after `_format_change_md`, the scoped-gate fold-in, and the
suppression-note line). Fixed with the identical `Contract: <relevance>
(<reason_code>), assurance: <level>` tag, gated on `c.contract_relevance
is not None` the same "no-op unless already stamped" way every other site
uses. New test: `test_contract_evaluation_renders_in_leaf_type_findings`
(a public struct's own size change, `--report-mode leaf`). (2)
`_suppression_rule_label` disambiguated *unlabeled* rules by rendering
every populated selector, but a rule *with* a `label`/`reason` returned it
bare, with no disambiguation at all -- `label`/`reason` is documented as a
free-form grouping tag with no uniqueness guarantee (`Suppression`'s own
docstring), so two distinct rules sharing one reason (a common real
pattern -- rules grouped under the same waiver ticket, say) still rendered
as the identical, ambiguous identifier this whole line of fixes exists to
prevent. Fixed by always appending the selector tuple in parentheses
alongside label/reason when both exist (`"reason (symbol=foo)"`), not only
as a fallback for the label-less case. Updated the existing high-risk/
stale/expired-rule tests' exact-string assertions to the new format, and
added `test_shared_reason_still_disambiguated_by_selectors` (two rules
sharing one `reason`, differing only by `symbol`, asserting distinct
rendered labels).

**Updated (2026-07-30): `--audit-suppressions`'s "high risk" classification
now respects an active `--policy-file` (Codex review, fresh evidence).**
`SuppressionList.audit()` classified a matched change as high-risk solely
via the static, imported `BREAKING_KINDS` set, ignoring any
`--policy-file overrides:` in effect for the same run. A policy that
promotes a normally-`API_BREAK` kind (e.g. `constant_removed`) to
`BREAKING` meant a rule suppressing that finding actually prevented a
BREAKING verdict, but the audit still reported it as not-high-risk;
conversely a policy that demotes a normally-`BREAKING` kind away from it
(as this fix's regression test does with `func_removed`) still had the
audit calling that suppression high-risk even though the run's own
verdict wouldn't have been BREAKING either way. Fixed by adding an
optional `breaking_kinds` parameter to `audit()` (default: the existing
`BREAKING_KINDS`, so every other caller is unaffected), and passing
`result._effective_kind_sets()[0]` -- the same override-applied breaking
set `DiffResult.breaking`/`_effective_verdict_for_change` already use --
from `cli_compare_helpers.run_compare`'s `--audit-suppressions` call site.
New test: `test_high_risk_respects_policy_file_demotion` (a `--policy-file`
demoting `func_removed` to `risk`, asserting the suppressed removal is no
longer reported in `high_risk_matches`).

**Updated (2026-07-30): two more real findings on `--contract-evaluation`
(Codex review, fresh evidence), closing what looks like the last remaining
gap in per-finding contract rendering.** (1) `--show-filtered`'s stderr
audit ledgers (`cli_audit.echo_filtered_surface`/`echo_reconciled`) print
every out-of-surface/reconciled finding's kind, symbol, location, and
exclusion reason, but never read the `contract_relevance`/
`contract_reason_code`/`contract_assurance` fields the same findings are
already stamped with by the time `_finalize_compare_result` runs (the
shadow evaluator stamps `out_of_surface_changes`/`reconciled_changes`
inside `compare()`, which always runs before `_finalize_compare_result`).
Fixed by threading a `contract_evaluation: bool = False` parameter through
`_finalize_compare_result` → both `cli_audit` printers, and a shared
`_contract_tag()` helper in `cli_audit.py` appending a
`[contract: <relevance> (<reason_code>), assurance: <level>]` suffix
per line, gated on the finding actually being stamped -- a no-op unless
both `--contract-evaluation` and `--show-filtered` are given together. New
tests in `tests/test_cli_compare_contract_evaluation.py`
(`TestShowFilteredAuditLedger`): one asserting the tag renders for a
demoted `InternalCache` finding, one asserting it's omitted by default.
(2) CodeRabbit separately flagged that the `Contract: <relevance>
(<reason_code>), assurance: <level>` tag-building pattern was duplicated
across four sites in `reporter_markdown.py`
(`_format_leaf_type_change`, `_to_markdown_root_cause`'s missing-label
branch, `_append_suppression_note`, `_format_change_md`) -- a nitpick, not
a correctness bug, but a real duplication-drift risk (a future field
addition to the contract tuple would need editing four near-identical
blocks in sync). Extracted a shared `_contract_decision_text(relevance,
reason_code, assurance)` core helper covering the three sites that render
an *already-stamped `Change`* (`_format_leaf_type_change`,
`_append_suppression_note`, `_format_change_md`) -- deliberately returning
just the `<relevance> (<reason_code>), assurance: <level>` core text with
no `Contract:`/`[contract: ...]` wrapper, since the three sites render in
visibly different shapes (a leading `"Contract: "`, a bracketed
`"[contract: ...]"`) and each keeps its own exact prefix/suffix/casing
around the shared core. Left the fourth, dict-based site
(`_to_markdown_root_cause`'s missing-label branch, which builds a
`label_decision` dict via `stamp_explicit_scope_contract_evaluation`
rather than reading an existing `Change`'s attributes) and `cli_audit.py`'s
own `_contract_tag` unmerged -- genuinely different shapes (dict values
vs. enum attributes; a different module), not the same duplicate this
nitpick was about.

**Updated (2026-07-30): two more real findings on `_suppression_rule_label`
and the stale-rule audit ledger (Codex review, fresh evidence).** (1) The
selector tuple `_suppression_rule_label` renders omitted `reachability`,
`allow_public_break`, and `allow_unknown_reachability` -- ADR-044 D2 gates
that affect which findings a rule matches exactly like `symbol`/
`change_kind`/etc. Two rules sharing every listed selector but differing
only by `reachability` (e.g. `public-only` vs. `unreachable-only`) match
disjoint findings yet rendered identically. Fixed by adding all three to
the field list (the two booleans use the same "if truthy" convention as
every other field, since both default `False`). New test:
`test_label_includes_reachability_gates`. (2) `_fold_suppression_audit_into_text`'s
markdown/text/review branch included `audit.summary()` wholesale, and
`SuppressionAudit.summary()`'s own stale-rule section printed up to 5
per-rule detail lines naming each rule by only its *first* populated
selector -- the identical ambiguity `_suppression_rule_label` was built to
fix, just reintroduced by a different code path. Fixed by trimming
`summary()`'s stale-rule section down to the count only (matching how it
already reports expired/near-expiry as counts only), and rendering an
explicit "Stale rules (matched nothing):" list in the fold-in using
`_suppression_rule_label`, the same as the high-risk/expired/near-expiry
buckets already do. New tests:
`test_stale_rules_rendered_with_disambiguated_labels`
(`tests/test_cli_compare_audit_suppressions.py`); existing
`test_audit_summary_output` (`tests/test_review_fixes.py`) still passes
since it only checks for the word "stale"/"matched nothing", not the
removed per-rule detail.

**Updated (2026-07-30): one more real finding on `_suppression_rule_label`
(Codex review, fresh evidence) -- `expires` was still missing from the
identifier.** Two otherwise-identical rules (same symbol, same label)
differing only by their `expires` date rendered as the identical label in
`expired_rules`/`near_expiry_rules` -- exactly the two buckets where expiry
is the one thing distinguishing them, so a reader couldn't tell which
deadline belonged to which rule. Unlike the earlier `reachability` fix,
`expires` is deliberately *not* a matching selector (it doesn't affect
which findings a rule matches, only whether the rule itself is still
active) -- appended separately after the selector loop, as
`expires=<ISO date>`, whenever set. Updated
`test_expired_rule_labeled_not_just_counted`'s exact-label assertion to
include the now-appended `expires=2000-01-01`, and added
`test_label_includes_expires_date` (two rules sharing a label, differing
only by `expires`, asserting distinct `near_expiry_rules` labels each
containing their own date).

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
