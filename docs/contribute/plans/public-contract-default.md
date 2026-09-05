# Public-contract default: implementation and rollout plan

**Status:** ADR-049 accepted (2026-07-26); implementation in progress — see
"Work breakdown" below for current per-phase status. **2026-09-02: the
contract decision became authoritative** (`contract_pipeline.py`'s
`ContractEvaluationStage` runs before `checker._compute_verdict_for`, not
after `verdict`) — the "still open" items in this document that predate
that change (framed as "flipping the evaluator to run before the verdict")
are done. This does **not** mean the *default* flip (making `--contract`
apply without being asked for) is the only remaining item: Phase 6 below
documents two unresolved relevance defects that can lose a known public
break (a template-instantiated-parameter seed mismatch, and an
unresolved `ambiguous_namespaced_leaf` identity gap) and two explicitly
uncovered measurement lanes (`package`, `real_binaries`) — those stay open
prerequisites alongside the flip, not items the "authoritative" milestone
already closed. The MCP `abi_compare`/`mcp_server.py`/
`mcp_compare_receipt.py` references in this document's history below are
historical — the MCP server was later retired in full (see
[ADR-021](../adr/021-mcp-security-model.md)); do not read them as a
current, reachable code path.
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

**Updated (2026-07-31): the phase's remaining field-wiring gap and its
contract/gate pack-composition gap are both closed; this phase's gate is now
an executable check rather than a description.** Everything above resolves
*individual* fields. The missing piece was the object those fields belong to:
a new module, `abicheck/compatibility_evaluation_frontend.py`, collects every
setting a front end can actually state today, resolves each field through
`resolve_field`, and assembles all seven typed namespaces plus one
provenance-receipt entry per resolved field.

- **Front ends wired** (the "wiring any other field ... to construct real
  `FieldCandidate`s" item above): `compare_cli_inputs` takes the `compare`
  command's real kwargs plus the set of parameters the user actually typed
  (Click's `ctx.get_parameter_source(...)`, which a live caller supplies) —
  needed because `--policy` and `--scope-public-headers` carry non-`None`
  click defaults that must not be mistaken for stated values;
  `compare_request_inputs`/`compatibility_config_from_compare_request` take a
  typed `CompareRequest` (the service/API request model this phase named);
  `ProjectCompatibilityInputs.from_build_config` projects a real
  `.abicheck.yml` (`BuildConfig`) at `PROJECT_CONFIG` tier. Fields resolved
  from real input: `contract.mode`, `contract.packs`/`policy.packs`/
  `gate.packs`, `policy.base`, `policy.overrides`,
  `surface.internal_namespaces`, `surface.explicit_scope`,
  `gate.exit_code_scheme`, `gate.preset`, the four `gate.severity.*`
  categories, and `suppressions`.
- **Both D7 compatibility exceptions are implemented as exceptions, not
  errors**, each retaining the shadowed input in
  `provenance[field].shadowed_legacy`: `--policy` vs. `--policy-file` (D7
  verbatim), and `--contract` vs. `--scope-public-headers` (the Phase 6
  flag's own documented "an explicit value outranks those" — making that pair
  a usage error would reject a combination the live CLI accepts today).
- **`auto` never reaches a resolved value, but selecting it is still a
  stated choice.** An explicit `--exit-code-scheme auto` contributes a
  candidate carrying the answer to "is a severity setting in effect?", so it
  outranks a lower layer's concrete scheme exactly as any other explicit
  value would — matching `cli_helpers_compare.resolve_compare_config`, where
  the CLI value wins whatever it is. (A first cut treated `auto` as "not
  stated" at every layer, which let a project config's concrete scheme beat
  an explicitly typed `--exit-code-scheme auto`; caught in review.) A
  project config's own `auto` does contribute nothing, because
  `BuildConfig`'s default for that key *is* the string `"auto"`, making a
  stated one indistinguishable from an absent one. A test pins the resolved
  `gate.severity` field-for-field against `severity.resolve_severity_config`,
  so the two cannot drift.
- **Every file-derived receipt entry carries its source's own digest**
  (ADR-049 D6): the `--policy-file` document's bytes for the base/overrides/
  namespaces it supplied, and the pack manifest's `id`/`version`/`sha256` for
  a field a pack filled — `resolve_pack_field_assignments` returns a
  `RoutedPackAssignment` (value + identity + path) rather than a bare value
  for exactly this reason. Which of several *agreeing* packs is credited is
  decided by sorted pack identity, so the receipt never depends on `--pack`
  order. A composed field (`policy.overrides`, `surface.explicit_scope`)
  credits only sources that actually contributed: a policy pack whose every
  assignment the policy file overrode is not listed.
- **`surface.explicit_scope` is resolved as the additive field it really
  is.** `--public-symbol` and `scope.public_symbols` *merge* today (ADR-037
  D4), which is a genuine exception to per-field highest-layer-wins; the
  resolved value is the union, and the receipt records the highest-precedence
  contributor with every contributor listed in `selected_by`, so an additive
  resolution is still exactly replayable.
- **Contract/gate pack composition** (the "per-field router this slice does
  not build" gap immediately above):
  `compatibility_evaluation_wiring.resolve_pack_field_assignments` routes each
  selected non-policy pack's assignments onto the typed fields that namespace
  may set (`CONTRACT_PACK_FIELD_ROUTES`/`GATE_PACK_FIELD_ROUTES`), validating
  and converting each value; an assignment naming anything else is a
  `PackManifestError`, never a silently-ignored key. Deliberately not
  routable: `contract.mode` (a pack switching which evidence domain a run
  judges against is exactly the hidden preset D3 forbids), `policy.base`
  (packs compose over a base, they don't replace it), and any `*.packs`
  field. A pack's value applies only when *no* other layer stated the field —
  D8's "explicit override > selected packs > base", read conservatively
  enough that a pack can never silently override a project's own value
  either. `resolve_field` could not express this directly: a pack tagged with
  the layer that selected it would *tie* with an explicit value for the same
  field and be reported as conflicting with it, which is the exact case D8
  says the explicit value resolves.
- **This phase's gate is executable.** `cross_front_end_differences(a, b)`
  compares two resolved configurations modulo exactly one permitted
  difference — which front end stated a value (`explicit_cli` vs.
  `api_request`, and the option spelling recorded with it), the two D7 puts in
  one precedence tier — and returns a per-field difference list.
  `tests/test_compatibility_evaluation_frontend.py` runs a real `compare`
  kwargs mapping against the equivalent `CompareRequest` through it, over
  every field both front ends can state.
- **D6 identities for the two file-less selections.** A built-in
  `--policy` base and a `--severity-preset` are code, not files, so
  `builtin_policy_identity`/`severity_preset_identity` digest what each
  *resolves to* (the four `ChangeKind` sets; the four category levels) — the
  identity then detects the drift a digest exists to detect, which a bare
  `id`/`version` pair could not. An unknown base name raises rather than
  minting an identity: `policy_kind_sets` falls back to `strict_abi` for a
  typo, which is right for classification and wrong for an identity.
- **`.abicheck.yml` schema/reference docs** (the last item on the list
  above): the strict `BuildConfig` schema and its generated key reference
  already existed; what was missing was the ADR-049 layer over them. New page
  `docs/reference/compatibility-evaluation-config.md` documents the seven
  namespaces, the precedence chain and its two exceptions, the per-field
  input map across CLI/API/`.abicheck.yml`/pack, the pack-manifest format
  with its per-kind assignable-field table, and the receipt.

**Still not done, and deliberately out of scope here** (each is a separate
piece of work, not a loose end of this one):

- No live command constructs this object. That is intentional and unchanged
  from every other slice of this phase — resolution that changes no verdict,
  finding, or exit code is what Phase 1 owns; consuming it in the
  authoritative comparison path is Phase 5's "same typed config" item, and
  the default flip is Phase 7.
- No `--pack` CLI flag. The composition path is real and tested, but pack
  paths reach it from the Python API only. `cli.py` remains at its 2000-line
  hard cap, so the flag still needs the prerequisite extraction noted above —
  and a flag whose only effect is to feed an object nothing consumes would be
  dead surface until Phase 5 lands.
- `EvidenceConfig` in full (no CLI/API surface selects an evidence provider or
  a variant set), and `contract.unresolved`/`contract.overlays`/
  `assurance.require_evidence`, which have no flag at all — a `kind: contract`
  pack is the only input that reaches them today.
- `--strict-suppressions`/`--require-justification` are real inputs with no
  field in ADR-049's own typed shape to carry them. Adding one is an ADR-level
  shape change, not a resolver change, so they stay outside the object and are
  documented as such rather than folded into an ill-fitting field.

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

**Updated (2026-07-31): the deferred matching wiring landed; this phase's
remaining item is closed.** Two prior passes deferred it as "a substantially
larger, higher-risk refactor" and, on re-investigation, for a sharper reason:
`resolve_function_identity` returns exactly one `primary_id` and has **no
notion of "ambiguous, so don't match"** — the `extern "C"` fallback's whole
correctness rests on the *caller* counting candidates
(`len(extern_c_candidates) == 1`), so keying a dict by `primary_id` would
only have moved that count behind a different key, not removed it.

The fix was to supply the missing notion rather than to work around it.
`finding_identity.SymbolIdentityIndex` is the flat-symbol counterpart of
`diff_helpers.TypeMap` (ADR-045): a `Mapping` over the same keys
`_public_functions`/`_public_variables` already return — so every existing
loop is untouched and each declaration is still visited once — plus an
ambiguity-checked alias tier (`unique_alias_match`, which answers `None` for
"no candidate" and "several candidates" alike, with `alias_candidates` left
available to tell those two apart). `_match_old_function`'s exact-key tier is
now the index's own lookup and its extern-C fallback one alias lookup with an
eligibility predicate; `_diff_variables` joins through the same index.

Two deliberate departures from `TypeMap`, both documented at their call
sites:

- **`__getitem__` never resolves an alias.** A type's bare-name alias is a
  schema-evolution accident worth healing silently; a *symbol*'s is not —
  two differing mangled names are two different exports, and joining them by
  display name would report a genuine removal as a modification.
- **No alias tier for variables at all**, which is a decision rather than an
  omission: the alias tier exists for `extern "C"`, where one entity is
  legitimately spelled two ways by two producers. A variable has no overload
  set and no C/C++ linkage mismatch to heal, so its key *is* its export. A
  regression test pins that a re-mangled variable stays removal + addition.

Matching behavior is unchanged by construction, and the verification is what
the earlier deferral asked for: full unit suite including golden (21824
passed, unchanged count), the FP-rate gate (0/0 delta), the per-tier accuracy
gate (top-tier correct, under-call monotonic), and the `slow`
detector-property, identity-property and fact-conservation suites. New unit
coverage in `tests/test_symbol_identity_index.py` (the primitive in isolation
plus each matching rule end-to-end through `checker.compare`); the module is
at 100% line+branch.

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

A third route reaches the same residue from the output side: even when the
exact qualified key *is* seeded and only the intended record is walked,
`_walk_type_closure` writes `rec_node.name` — the bare `Foo` — into its
result, so `export_types` never carries `ns1::Foo` and a layout finding on
the exported class resolves `UNKNOWN_UNRESOLVED`/`identity_ambiguous`
instead of `IN_CONTRACT` (CodeRabbit review, confirmed with a minimal
snapshot). It cannot be recovered afterwards, for the same reason the
finding exists: from outside the walk, `Foo` in `export_types` is
indistinguishable between "the exact key was seeded" and "the ambiguous bare
key was walked, visiting both". And it has a second half —
`_confirmed_type_matches` rejects a qualified candidate whose bare tail is
ambiguous, correctly today because the walk adds *every* matching record for
such a tail, so making a qualified identity meaningful means relaxing that
guard in step with the walk change rather than independently. One
coordinated change to the shared walk plus its consumer; the same scoped
design as the two routes above.

Note the direction: this residue over-*includes* (an unrelated internal type
reads `IN_CONTRACT`), or — on that third route — under-resolves to
`UNKNOWN_UNRESOLVED`. Both are the opposite of the qualified-owner and
rival-scope bugs above, which produced a false `PROVEN_OUT_OF_CONTRACT` and
are fixed, and of every other guard in this section. `surface.py`'s tail keys are deliberate
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

A spelling that resolves to a **typedef** is followed to its target,
transitively. The motivating example — a snapshot recording parameter type
`Alias` while omitting `typedef Alias = Internal` — is already caught by the
*signature* scan, since `Alias` itself resolves to nothing; following adds
the narrower "alias present, its target absent" shape, which is invisible to
that scan because the alias key resolves fine (CodeRabbit review). An earlier
revision skipped targets entirely, on the measurement above — that was
over-broad: the noise comes from *how* an alias is reached, not from
following one. Following happens only from an already-scanned spelling,
never over `snap.typedefs` wholesale, and an ambiguous alias key is not
followed; together those keep libstdc++'s bare-key alias templates
(`"vector"`, `"basic_string"`, colliding with the real records) and its
member typedefs (reachable only by walking a toolchain record's internals,
which the rule above already declines) out of the scan. A dependent spelling
(`typename`, `template` — C++'s own markers, not a naming heuristic) is not
an edge either, since it names nothing until instantiation.

**Result, measured after the fix** (re-measured with typedef following on):
a pure-C library and the `std::`-carrying C++ library both report zero
unresolved edges and `exclusion_is_provable = True`, while a synthetic
snapshot whose export signature names an undeclared type — or names an alias
whose target is undeclared, directly or through a chain — reports that edge
and correctly degrades the same finding from `PROVEN_OUT_OF_CONTRACT` to
`UNKNOWN_UNRESOLVED`. The guard is satisfiable, not vacuous.

A token must match a registered spelling **as written**. Accepting its bare
leaf as well — an earlier revision did — resolved a qualified edge through an
unrelated record that merely shares the leaf: with `ns::Missing` absent and
`other::Missing` present, the edge counted as resolved and exclusion stayed
provable while a type reachable only through the omitted definition could be
proven out (Codex review). The one legitimate fallback is narrower and
keyed on what the snapshot actually records: a leaf may absorb a
*more*-qualified token only when it is the complete identity of a node
carrying no scope of its own — the producer-side scope loss that stores
libstdc++'s `std::string` typedef under the bare key `string`. A node that
does record a scope never lends its leaf to a different one. Both directions
are measured: dropping the fallback outright made the real C++ library report
`std::string` unresolved, and keeping it unconditional made the synthetic
`ns::Missing` case resolve wrongly.

**Naming a known node is necessary but not sufficient**, a second condition
added after the resolver above was measured. The registered spellings are
broader than what `_walk_type_closure` can actually look up: the walk
resolves a record or enum through its own name or bare `::` tail, but a
*typedef* only through its exact key, with no spelling tolerance at all. So
an exported parameter spelled `ns::Alias` against a captured typedef
`outer::ns::Alias -> Victim` counted as a resolved edge while the walk —
trying only `ns::Alias` and its tail `Alias`, neither of them that key —
never followed the alias, leaving `Victim` outside `export_types` and free to
be stamped `PROVEN_OUT_OF_CONTRACT` (Codex review, reproduced). An edge is
now resolved only when it *both* names a known node and is one the walk could
traverse.

The two conditions are independent, and each catches what the other misses:
a token can be traversable yet wrong (the `ns::Missing` → `other::Missing`
tail collision above — the walk follows *something*, but not the node the
edge names, so the real node's closure stays unknown), and a token can name a
known node yet be untraversable (the typedef case here). Both are holes in
the same closure, so both are reported. Re-measured after adding the second
condition: the pure-C and `std::`-carrying C++ libraries above still report
zero unresolved edges with `exclusion_is_provable = True`, on both the
header-scoped and headerless dumps — the narrowing costs the domain nothing
real, because a backend that bares a *record* spelling produces exactly the
tail key the walk indexes.

`exclusion_is_provable` folds in every incompleteness signal rather than
leaving some to the caller: an observed table, at least one resolved root,
**every** root carrying real signature types (`all_roots_typed`, which the
evaluator used to re-check separately for the same outcome), no unaccounted
export, and no unresolved type edge (CodeRabbit review).

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

**Phase 6's first slice landed with this:** `compare --contract
public|exports|all` selects the domain, threaded through
`CompareRequest.contract_mode` → `service.run_compare`/`compare_snapshots` →
`checker.compare`. `_apply_contract_evaluation_shadow` no longer re-derives
the D2 alias mapping inline — it calls Phase 1's own
`compatibility_evaluation_wiring.resolve_legacy_contract_mode`, making that
wiring's first live caller, and an explicit `--contract` outranks it per D7
(`explicit_cli` > `legacy_alias`). The flag requires `--contract-evaluation`
(on its own it would select a domain nothing evaluates) and is rejected for
directory/package comparisons, matching `--contract-evaluation`'s own
fan-out limitation. **Still open:** the corpus validation Phase 6's gate
requires; and the provider-evidence ledger this phase's own gate names
still does not exist for either domain (`ExportSurface.resolvable`/
`has_typed_roots` is the same coarse per-surface signal
`PublicSurface.resolvable`/`has_provenance` is, not a per-provider
completeness record).

**Closed (2026-07-31): the provider-evidence ledger and the measurement —
this phase's two remaining pieces.** Both were named as missing in the note
directly above; neither is now.

*The ledger* (`abicheck/contract_evidence_collect.py`, a new leaf module) is
the producer for the `EvidenceSearchRecord`/`ProviderEvidenceEntry` shapes
Phase 4 landed with no writer. It emits one record per *(provider, side)* —
`public_header` and `export_table` for the two domains this build actually
has, plus `post_manifest`/`forced_public_symbols` for the explicit overlays
when a run configures them — each carrying its own status, completeness,
identity coverage, requested-vs-searched scope, and a content digest of what
it observed. Three things it does deliberately, each of them a line of
Section 4.1 or 4.2:

- **Failure is scoped per provider.** An unavailable header surface leaves a
  completed export-table search alone, and vice versa. That is why the
  ledger reports *which* guard failed
  (`unmatched_exports`/`untyped_export_roots`/`unresolved_type_edges`/…)
  rather than mirroring `ExportSurface.exclusion_is_provable`'s single
  boolean: the whole purpose of the ledger is to say why a domain was not
  closed, which that boolean cannot.
- **`configuration_coverage` is honestly `NOT_STARTED` everywhere.** No
  variant source exists in this build, so the closed-world rule for
  `UNKNOWN_UNPROVEN` (Section 4.2) cannot be satisfied — which is exactly
  why `contract_evaluation.py` never emits that value. Recording the facet
  as not-started rather than quietly complete is what will let a future
  variant provider flip it *on evidence*, instead of the under-claim being
  an unexplained constant in a module docstring.
- **"Not consulted" ≠ "consulted and failed".** A run that never computed an
  export surface has no `export_table` entry at all, rather than one marked
  unavailable — Section 4.1 forbids persisting "this provider was required
  under one policy" as though it were an observed fact.

Each stamped finding now carries `Change.contract_evidence_refs`: the record
ids its decision rests on, computed by `evidence_refs_for_reason()` from the
reason code, the selected domain, and the authoritative side (ADR-049 D4 —
exposed as `contract_evaluation.authoritative_side()` rather than re-derived,
so the two cannot drift from that function's two carefully-scoped kind sets).
A non-entity finding correctly cites *nothing*: its relevance follows from
its `ChangeKind`, consulting no provider. A `--used-by`/`--required-symbol`
stamp cites a run-level reference instead of a block record, because that
decision is made after `compare()` returned, by a caller holding no block.
`validate_decision_evidence()` rejects a reference naming neither — a
dangling ref is worse than none, since a consumer cannot tell it apart from a
record that failed to serialize.

Two cases the reason code alone cannot distinguish get their own attribution,
in `contract_evaluation.evidence_refs_for_change()` (there, not in the
collector, so the matching reuses this module's own overlay rules rather than
a second copy): an `IN_CONTRACT` decision produced by `--public-symbol`'s
widening overlay or by a `--post-manifest` commitment carries exactly the
same `public_root_membership` code a genuine header-derived membership does,
and a `--post-manifest`-driven *exclusion* shares
`terminal_authoritative_exclusion` with header-origin exclusions. In both,
the evaluator short-circuits on the overlay before consulting the surface at
all, so citing the header provider would be a false citation in a ledger
whose whole purpose is evidence honesty. Matching mirrors each overlay's own
rule exactly — suffix-tolerant for the widening overlay, exact-name for the
manifest — and is skipped entirely under `exports`, where Section 7 makes
manifest/consumer evidence unrelated and advisory. The refs are serialized on every finding
entry the JSON report already stamps (`reporter._add_contract_evaluation_fields`,
so `changes`, `out_of_surface_changes`, the suppressed/redundant/reconciled
ledgers, and `scope.filtered_internal_changes` all inherit it).

*The measurement* is `scripts/measure_contract_shadow.py`, mirrored into the
unit lane by `tests/test_contract_shadow_measurement.py` (the same
script-plus-mirror pattern `check_fp_rate.py`/`test_fp_rate_gate.py`
established). It runs the FP-rate corpus — already labelled internal-noise
vs. real-break, so the ground truth needed no new curation — through
`compare(..., contract_evaluation=True)` in **all three** domains, and
reports this phase's four quantities: the delta matrix (legacy kept/demoted ×
contract relevance), unresolved rate by provider state / domain / platform,
proven public-break losses, and proven false-positive reductions. The gate is
three zero baselines: a proven public-break loss, a delta whose decision
cites no resolvable evidence, and a finding with no recorded decision
("zero unexplained fact loss", read literally).

First run, 32 cases, 41 findings per domain: **0 losses, 0 unevidenced
deltas, 0 fact losses.** `public` resolves 71% of findings and proves 7
internal-noise findings out of contract; `all` produces 16 deltas (every
legacy-demoted finding, by definition of the mode) and 0 unresolved;
`exports` is 100% unresolved on this corpus, because the corpus's synthetic
snapshots carry no export table. That last number is reported rather than
hidden: it is the honest "unresolved rate by domain" signal the phase asks
for, and measuring only the flattering domain would defeat the point. One
gate test asserts the `public` domain proves *something* out of contract,
so the loss baseline cannot pass vacuously — zero losses is trivial for an
evaluator that never concludes anything.

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

**Closed (2026-07-31): all three, plus the round-trip the gate names.** The
paragraph above lists exactly what was missing; each is now implemented, and
the one place this pass deliberately departs from the plan's own wording is
called out below rather than quietly resolved.

*Real type-graph content.* `contract_evidence_collect.build_type_graph()`
walks the snapshot's whole record/enum/typedef/declaration graph and emits it
as `TypeGraphSnapshot` nodes/edges. The node encoding
(`decl:`/`record:`/`enum:`/`typedef:`/`alias:`) is documented in that module,
since `TypeGraphSnapshot` itself deliberately treats nodes as opaque. Two
properties matter more than the encoding:

- It is **policy-independent**, the block's whole contract: a private,
  hidden-visibility declaration is walked exactly like a public one, and no
  contract mode is consulted anywhere. That is what makes one collected block
  valid input to a re-evaluation under a mode the original run never
  evaluated (`test_a_different_mode_needs_no_new_evidence`).
- References are resolved **at collection time**, through the same
  name/bare-tail indexes `surface._index_surface_types` builds for the live
  closure walk — which is precisely why the block carries an
  `identity_algorithm_version`. A future matcher resolving the same raw
  spellings differently produces a different graph from identical inputs, and
  D6 requires that to be tellable apart rather than silently reinterpreted. A
  spelling resolving to nothing is *not* recorded as an edge to a placeholder
  node: there is no node to point at, and inventing one would let a replayed
  closure claim to have walked something it never did.

*The two procedures* are `abicheck/contract_replay.py`.
`replay_original_decisions()` reads the `decision_receipt` and nothing else —
no evidence re-walk, no live re-probe, and this build's own provider defaults
cannot alter the answer (`test_replay_ignores_this_build_s_evidence` empties
the entire evidence block and asserts the replayed decisions are unchanged).
`reevaluate_from_evidence()` answers the *different* question — old
observations, newly resolved context — by walking the persisted graph and
never reading the receipt, so a receipt written under `public` puts no thumb
on an `exports` re-evaluation. Both route through
`load_replayable_context()`, so there is no path that consumes a persisted
context without D6's fail-closed version check; a *mixed* context (older
evidence, current evaluation context) is explicitly fine, since that is the
ordinary re-evaluation case rather than an error.

Re-evaluation is deliberately a **narrower** evaluator than the live one, and
says so instead of pretending otherwise: it has no access to the live
surfaces' origin maps, hidden-friend reasoning, or the pipeline's own
`surface_exclusion_reason` annotations — none of which are observations the
evidence block carries — so where the live evaluator would use one of those,
this one answers `UNKNOWN_UNRESOLVED`. That is why `compare_decisions()`
checks *directional* soundness rather than equality: a replay that weakens a
decision is a known coverage limit, while one that strengthens it (or flips
`IN_CONTRACT` ↔ `PROVEN_OUT_OF_CONTRACT`, equally strong but opposite) is a
real defect — persisted evidence may never out-claim the live evaluator that
wrote it. The same conservatism governs the exclusion branch: an entity
absent from the graph is *unplaceable*, never proven out, and a `PARTIAL`
provider search proves no absence at all
(`test_incomplete_provider_cannot_prove_an_exclusion` uses the identical
snapshot as its proven-out sibling, minus declaration provenance).

*Persistence and the round-trip gate.* `abicheck/contract_context.py`
assembles the three blocks from a real comparison and `checker.compare(...,
contract_evaluation=True)` now returns one on `DiffResult.contract_context`;
`abicheck/contract_context_io.py` round-trips the whole group — including the
complete resolved `CompatibilityEvaluationConfig` and its field provenance,
per Section 5.1's "must serialize the complete immutable resolved
configuration" — and `reporter.py` emits it as the JSON report's
`contract_context` block from all three report paths (full/leaf/root-cause,
the same three `_add_surface_scope`/`_add_reconciled` already cover). The
gate is tested as stated: round-trip through *real* `json.dumps`/`loads`
(not dict-level, so a decoder's tuple→list conversion is actually exercised)
is the identity, serialization is byte-stable, and a provider-order-reversed
block serializes to identical bytes rather than merely to an equal object.
Version counters survive a decode verbatim — re-stamping them with this
build's constants would erase exactly the mixed-version evidence D6 requires
a reader to act on — and the *decoder* deliberately does not enforce the
ceiling, so a tool can read a newer-than-supported block in order to *report*
the mismatch, while `load_replayable_context()` is what refuses to evaluate
it.

**Deliberate departure from this section's own first sentence, stated rather
than glossed:** the blocks are persisted with the *comparison* (the JSON
report), not inside `AbiSnapshot` via `dumper.py`/`serialization.py`. Three
reasons, in order of weight. (1) The evidence is two-sided by construction —
every `EvidenceSearchRecord` carries `side: old|new`, and the header/export
providers are collected per side of one pair — so it is a fact about a
comparison, not about either snapshot alone. (2) Everything the block records
is *derived* from the snapshot's own already-persisted content (declarations,
visibilities, type graph, export tables); deriving it at compare time from a
persisted snapshot is lossless and equivalent, while storing it in the
snapshot would duplicate that content on disk. (3) A new `AbiSnapshot` field
means a `serialization.SCHEMA_VERSION` bump, which is part of the
comparability contract (ADR-050) and would need its own
`SCOPE_FIELD_KEYS`/`check_contracts_comparable` review — a materially larger
blast radius than this phase's gate requires, since the gate is about
round-trip *decisions*, which the comparison-level record satisfies in full.
The replay guarantee is unaffected: the persisted comparison record carries
the evidence, so no replayed verdict depends on re-reading a binary or a
header. If a genuine single-side use case appears (e.g. `dump` publishing a
baseline whose evidence a later consumer wants without the pair), moving the
block into the snapshot is an additive schema change on top of this shape,
not a rewrite of it.

**Post-review corrections (2026-07-31, same PR).** Seven findings from the
Codex/CodeRabbit round, each changing behaviour rather than wording:

1. **Export evidence is collected for every opted-in comparison, not only
   when `exports` is the selected mode** (Codex P1). Collecting it
   conditionally on the *original* run's mode contradicted this phase's own
   headline guarantee: a `public` run's persisted block could not be
   re-evaluated under `exports` without re-reading the binaries, since
   `_PersistedDomain` would find no export roots and answer
   `UNKNOWN_UNRESOLVED` for a pair whose export tables were fully
   observable. The cost is one export-table match per side, paid only under
   `--contract-evaluation`.
2. **A `public` graph-only exclusion is no longer `PROVEN_OUT_OF_CONTRACT`**
   (Codex P1). Section 4.3's negative proof needs positive private/system-
   header provenance *plus* every stronger-or-equal provider having
   completed; the persisted block carries neither (it records declarations
   and a type graph, not per-entity header origin, and
   `configuration_coverage` is `NOT_STARTED` on every record this build
   writes). Proving an exclusion from graph non-membership alone would have
   *strengthened* the live decision — the one direction this module's own
   contract forbids. `exports` keeps its exclusion branch, because there the
   provider's `COMPLETE` state *is* `ExportSurface.exclusion_is_provable`,
   which is the terminal exclusion the ADR names.
3. **The decision receipt is keyed by the report's own `finding_id`**
   (Codex P2). The coarse `kind:symbol` fallback collapsed two findings of
   one kind on one symbol (two parameters of the same function) into a
   single entry, silently dropping a recorded decision and making the
   receipt uncorrelatable with the report. The id's implementation lives in
   the dependency-free leaf module `finding_identity.report_finding_id`,
   which `checker` imports directly; `reporter_markdown._finding_id` is now
   a compatibility alias for it (moved there because importing the renderer
   from `checker` — even function-locally, which the `import-cycle-growth`
   gate counts — would close a `checker -> reporter_markdown -> checker`
   cycle). Callers that hold a result rather than a comparison still inject
   it as a callable (`contract_evaluation.stamp_scoped_result_findings`,
   `contract_context.relevance_map`).
4. **A `--policy-file`'s per-kind overrides reach the persisted config**
   (Codex P2). Recording only the base-policy name made the "resolved"
   configuration wrong in exactly the way an audit consumer would act on.
5. **`decision_receipt` gained its own `schema_version`** (CodeRabbit), a
   fifth reserved constant checked independently by
   `check_persisted_context_versions_supported`. Its *keys* are their own
   contract, so its shape can change while observations and configuration do
   not — the same "one counter per independently-evolvable concern" rule the
   other blocks already followed.
6. **`compare_decisions` gained a `disagreed` bucket** (CodeRabbit): a
   transition into or out of `NOT_APPLICABLE` is not a point on the strength
   scale, and was silently landing in `strengthened`. It is reported
   separately but still fails `is_sound` — both evaluators share one
   `_NOT_APPLICABLE_KIND_SLUGS` set, so disagreeing there means the receipt
   and this build classify the same `ChangeKind` differently.
7. **Under `all`, a replayed decision cites the header provider**
   (CodeRabbit), matching the live attribution. `all` has no root provider,
   so the refs were empty — which carries the *non-entity* meaning instead.

**A second review round on the same PR found three more, all fixed.** (1)
The audit-ledger serializers (`out_of_surface_changes`, the suppressed and
reconciled ledgers, `scope.filtered_internal_changes`) carried the contract
fields but no `finding_id` — and also omit `old_value`/`new_value`, two of
that id's own inputs — so a demoted finding could neither be joined to its
decision in the receipt nor have its key recomputed. The id is now emitted by
`_add_contract_evaluation_fields` itself, so the key and the decision always
travel together. (2) `evidence_refs_for_reason` cited *both* sides for an
`export_root_membership` decision, but `_exports_mode_decision` reads one
`ExportSurface` (chosen by D4's side rule) and decides from it alone — so the
second citation claimed a decision rested on evidence it never read, actively
misleading when that side's provider is unavailable. The two-sided reason set
is now per-domain: `public` keeps it (its membership classification really
does run against `surface_unions`), `exports` and `all` cite one side. (3)
The selected suppression source (rules + digest) now reaches
`resolved_config.suppressions`; leaving it `None` claimed no source was
selected at all. A list with no `source_sha256` (assembled in memory, never
read from a file) still records `None` rather than a fabricated digest.

Three structural cleanups landed with them: one shared
`contract_context.persisted_domain_view()` replaces the duplicated
provider-walk in the receipt builder and the replay domain (they could
otherwise disagree about the same closure); `graph_node_index()` resolves
spellings through a per-side index instead of rescanning a whole-snapshot
graph once per spelling per finding; and every required-key read in the
persisted-context decoder now fails as `TypeError`, so a consumer handling a
corrupt block catches one exception type rather than three.

**A third review round found four more, all fixed.** (1) The decision
receipt was frozen inside `compare()`, *before* `--used-by`/`--required-symbol`
scoping overwrites already-recorded findings with the stronger explicit-scope
`IN_CONTRACT` decision and synthesizes fresh `scoped_only_changes` — so the
persisted receipt disagreed with the report emitted beside it, and
`replay_original_decisions` reproduced decisions that were never the run's
own. `contract_evaluation.refresh_contract_receipt` now re-keys the receipt
from the two collections a scoping pass can touch, called from the same
shared `stamp_scoped_result_findings` traversal both front ends already use
(so neither can forget it). It *merges* rather than rebuilds: the receipt
also covers the suppressed/redundant/out-of-surface findings that never
reach `result.changes`. (2) The persisted type graph carried no edge from a
method to its own enclosing class, which *both* live surfaces seed
(`surface._seed_public_roots`, `export_surface._seed_export_roots`) precisely
because a consumer holding an exported method can declare, allocate, and
inherit that class. A replay walking only signature edges would find the
owner unreachable and could turn a live `IN_CONTRACT` into
`PROVEN_OUT_OF_CONTRACT` — the one direction `compare_decisions` treats as
unsound. The edge uses `surface.py`'s permissive spelling resolution rather
than `export_surface.py`'s exact-identity map: one graph serves both domains,
and over-linking can only ever *weaken* a replayed decision. (3)
`--public-symbol`'s forced-public set and `--post-manifest`'s committed-export
allowlist now reach `contract.overlays`/`surface.explicit_scope`; both decide
membership (ADR-049 D2 counts overlay-selected roots as part of the `public`
domain's roots), so omitting them described a run that never happened. They
are recovered from the run's own evidence ledger rather than re-read from the
caller's arguments, so the two halves of one context cannot disagree, and the
digest combines each overlay's own `input_identity` — a merged item list
alone could not tell one manifest naming A and B apart from two overlays
naming one each. (4) `graph_node_index` followed an `alias:` edge whose
source node was absent from `nodes`, where `resolve_graph_node` does not; the
two are documented to agree by construction, so the index now applies the
same guard.

**A fourth round found two more, both fixed.** (1) The persisted export roots
were derived by intersecting each declaration's alias-expanded symbol keys
with `ExportSurface.export_symbols` — the exact trap
`export_surface._unresolved_type_edges` already documents avoiding. A binary
exporting the C symbol `foo` alongside an unexported `ns::foo` puts the bare
tail `"foo"` in both declarations' key sets, so the unexported C++
declaration was persisted as an export root and re-evaluated into a contract
it is provably out of. `ExportSurface` now carries the `root_identities` set
its own seeding already computed, and the collector matches on *linker
identity* against it. (2) `public`-domain reconstruction read only the
`public_header` provider, so an entity kept by an explicit overlay
(`--public-symbol`, `--post-manifest`) re-evaluated to `UNKNOWN_UNRESOLVED`
even with the overlay's own evidence entry sitting in the same block.
`persisted_domain_view` now folds overlay manifests into the `public`
domain's roots, as ADR-049 D2 prescribes ("roots selected by explicit
overlays") — resolved through the same `graph_node_index` a finding's own
spelling uses, so an overlay entry naming nothing the graph knows contributes
no root rather than a guess. Deliberately `public`-only: `exports`' roots are
the observed table, and a user's assertion is not an observation of the
binary.

**A fifth round found three more, all fixed — and the first two together
settled what an overlay actually is on replay.** (1) A positive membership
was concluded from a provider that had reported the domain unavailable —
not that it observed nothing, but that what it observed never resolved into
a usable domain: an `elf_only_mode` snapshot leaves `PublicSurface.resolvable`
false, so the live evaluator answers `UNKNOWN_UNRESOLVED`/`UNAVAILABLE` at
its `not auth.resolvable` gate — but the ledger entry still carries the
declarations and type graph the collector had built before bailing out, and
the closure walk reached the entity through them and answered `IN_CONTRACT`
with `COMPLETE` assurance. `_PersistedDomain.domain_is_available` is the
persisted counterpart of that live gate, and it is one-to-one rather than an
approximation: `contract_evidence_collect` writes `UNAVAILABLE` on exactly
the `resolvable` check the live path branches on. (2) An overlay root seeded
the closure walk, so forcing `hidden_api(Secret *)` public also pulled
`Secret` in — while live, `force_public_symbols`/`public_surface_allowlist`
are strictly *per-finding* overrides matched against the finding's own
symbol, leaving a `Secret` finding `PROVEN_OUT_OF_CONTRACT`. Overlay nodes
are therefore still roots (D2's "roots selected by explicit overlays", and
still what the receipt reports as `evaluated_contract_roots`) but no longer
closure *seeds* — the new `PersistedDomainView.closure_seeds_by_side` is the
root provider's own declarations alone. Those two fixes also fix each
other's edge: because the overlay check now mirrors live's ordering — ahead
of the resolvability gate — a user-named entity stays `IN_CONTRACT` even
when the header provider observed nothing, which is exactly what live does.
(3) `surface.internal_namespaces` reached the resolved config from the same
`--policy-file` as `policy.overrides` but carried no provenance entry; a
populated list now gets one. An empty one deliberately does not:
`build_evaluation_context` receives a tuple, which cannot distinguish "no
policy file" from "a policy file that stated an empty list"
(`PolicyFile.internal_namespaces_stated` is the field that can, and it does
not reach here), and claiming a source for a possibly-unstated value is
worse than claiming none.

**A sixth round found three more, all the same shape: the replay resolver
was looser than the live matcher it stands in for.** Each looseness was a
strengthening path, and each fix is "mirror what live already does":
(1) `_entity_spellings` offered a symbol's bare `::` tail in *every* mode,
but the live exports matcher passes `allow_tail_fallback=False` precisely so
an unexported `ns::foo` cannot borrow an exported C `foo`'s identity — the
tail reintroduced that collision one layer down, at node resolution. It is
`public`-only now, matching `_symbol_matches`'s own per-mode argument.
(2) The same function offered `caused_by_type` for every finding, while
`_exports_mode_decision` gates it behind `type_scoped`, for the reason its
own comment gives: closure membership answers a *type*-level question, so a
symbol-level finding on an unexported helper must not be placed in contract
because its `caused_by_type` is one some other exported signature reaches.
(3) `_link_owner_class` resolved the owner permissively, on the reasoning
that over-linking only ever weakens — wrong, because one graph serves both
domains: an exported namespace function `api::run()` yields the owner string
`"api"`, which permissive resolution matches against an unrelated
`other::api` record by its bare tail and pulls into the *export* closure.
It matches by exact record identity now, mirroring
`export_surface._seed_export_roots`'s own `owner_seed_by_identity` map — the
same rule, for the same reason, that `type_reachability.py`'s owner seeding
settled on. Exact matching loses nothing real: `owner_class_of` always
reconstructs a complete scope chain, so a real class matches exactly and a
non-method's namespace noise correctly matches nothing.

**A seventh round found two more of the same shape, one of them the exact
inverse of the round above.** (1) `exports`-mode node resolution still went
through the persisted graph's `alias:` edges, and `compute_export_surface`
prunes exactly those: an exported `ns::foo` contributes the bare alias `foo`
that an unrelated *unexported* C `foo` also answers to, so live drops the
shared key from `export_symbols` and returns `PROVEN_OUT_OF_CONTRACT` for a
finding on the C declaration. Replay resolved that spelling to the exported
node and reported `IN_CONTRACT`. The resolver now applies the same pruning —
an export root reached only through a spelling that a *declaration* outside
the root set also answers to is dropped, unless the spelling is a root's own
canonical node identity, which is the same pair of exemptions live keeps
(record/enum nodes never prune, because live's `nonroot_keys` is built from
declarations alone). Note that round six's fix and this one are the two
halves of one collision: there the finding was qualified and the root bare,
here the finding is bare and the root qualified.
(2) `all` mode returned `IN_CONTRACT` unconditionally, bypassing a persisted
`--post-manifest`. ADR-049 D2's `all` row drops *header-origin* scoping, not
every provider — the live evaluator checks the manifest's exclusion ahead of
its own `all`-mode shortcut, because a committed-export manifest is an exact,
closed-domain observation rather than a header-origin classification — so a
concrete export the manifest omits is `PROVEN_OUT_OF_CONTRACT` live and was
`IN_CONTRACT` replayed, including with a deliberately empty manifest (which
`collect_contract_evidence` records as a selected source, not an absent one).
The manifest's spellings are now read in every mode, and an `all`-mode
finding the manifest could not have admitted answers `UNKNOWN_UNRESOLVED`
rather than the live `PROVEN_OUT_OF_CONTRACT`: the replay reproduces
`_run_allowlist`'s keep conditions only approximately. Two of them are
deliberately left out, both in the weakening direction — the
concrete-export test (the persisted export provider's declarations are not
`_snapshot_export_ids`, so consulting it could wrongly conclude "kept") and
`--public-symbol`'s rescue (which live honors only when header scoping is
also on, a flag the persisted context does not record).

**An eighth round found two joinability gaps rather than soundness ones.**
(1) A missing `--used-by`/`--required-symbol` contract member has no backing
`Change` at all — each report format synthesizes its own entry for it — so it
reached neither collection `refresh_contract_receipt` merges, yet every format
stamps that synthetic entry `IN_CONTRACT`. The emitted finding therefore had a
decision the receipt had no key for, and the entry carried no `finding_id` a
consumer could have joined with anyway (it never routed through
`_change_to_dict`). Both halves are fixed by one identity:
`finding_identity.missing_contract_finding()` builds the `Change`-shaped
identity — including the description, one of `report_finding_id`'s own hash
inputs — that the CLI JSON fold, the MCP tool, and the receipt refresh now all
key from, so they agree by construction instead of by three hand-copied
literals. `missing_contract_kind()` joins it there for the same reason, folding
the four independent `gate_scope` → slug derivations into one.
(2) `decision_receipt.relevance_by_finding` was defaulted to `{}` when absent,
which made a truncated receipt indistinguishable from a comparison that
genuinely recorded no decisions — the same fail-closed rule the enclosing
`decision_receipt` block already got a round earlier, one level down.
(`schema_version` stays defaulted, deliberately: an absent counter has a
defined meaning — version 1 — where an absent decision map does not.)
(3) A third absent-vs-empty slip, same shape one layer over:
`evidence_refs_for_change` gated its `--post-manifest` attribution on the
allowlist's *truthiness*, so a manifest that validly commits to zero exports
scoped everything out and then had the resulting exclusions cite
`public_header` — the provider that decided nothing here. It tests
`is not None` now, matching what `collect_contract_evidence` already records.

**A ninth round found the same absent-vs-fabricated defect in the resolved
configuration itself, on the two fields the run's *gating* rests on.**
(1) `checker.compare` never sees the gate — the exit-code scheme and the
severity levels are resolved by the front end and applied to the returned
result *after* the core verb finishes — so `build_evaluation_context`
recorded a default `GateConfig()`. That is not an omission but a wrong
claim: it asserts the built-in `severity` scheme and the built-in severity
levels for every run, including a `legacy`-scheme one (confirmed: a run
exiting 4 on the legacy floor persisted `exit_code_scheme: "severity"`) and
one whose `--severity-abi-breaking warning` genuinely moved a category. The
front end now calls `contract_context.with_resolved_gate()` once, before any
report is rendered, with the values it actually resolved *and* the D7 layer
it resolved each from — which, unlike the core verb's `API_REQUEST`
under-claim above, is really observable here: a typed flag is
`EXPLICIT_CLI`, a value only `.abicheck.yml` supplied is `PROJECT_CONFIG`,
and an `auto` scheme that resolved itself is `BUILT_IN_DEFAULT`. Nothing
about a relevance decision, a closure, or the receipt's per-finding map
changes; the gate stays `NOT_APPLICABLE` to contract membership.
(2) `suppression_config_for` returned `None` for any `SuppressionList`
without a `source_sha256` — but the public constructor and `merge()` both
produce exactly that digest-less, fully *active* form (`compat/_helpers.py`
builds every ABICC `-skip-*` list that way, and `merge()` drops both halves'
digests even when each was file-loaded), so a run whose findings were being
suppressed persisted "no suppression source was selected at all." The digest
now falls back to a content digest of the rule identities the same block
persists. That is not a fabricated stand-in for the file digest: it
authenticates the rules that actually ran, computed with the ledger's own
`content_digest`, and nothing in this codebase re-reads a suppression file
to check this field against its bytes.

**A tenth round found the ninth round's own two fixes each had a sibling
one module over.** (1) The gate fix landed in the CLI front end only —
`mcp_server.abi_compare` resolves its own scheme and severity the same way
(any `severity_*` argument opts into the severity-aware scheme) and rendered
the context without refreshing it, so `report.contract_context`'s gate stayed
`GateConfig()`'s defaults there too. It now calls the same
`with_resolved_gate()`, recording `API_REQUEST` rather than `EXPLICIT_CLI`
for a stated value (a typed API caller, not a typed flag) and resolving a
`"scoped"` scheme back to the `legacy`/`severity` one it came from, since
that is not a value `GateConfig` accepts. (2) `measure_contract_shadow.py`
walked only `changes`/`out_of_surface_changes`, while `checker` stamps and
records five collections — a dropped decision or a missing receipt entry for
a *suppressed*, *redundant*, or *reconciled* finding left the `fact_losses`
gate at zero. All five are measured now, the three audit buckets under their
own rows; `_is_delta` answers `False` for them deliberately, since a
suppression, a display dedup, and a build-context reconciliation are
policy/presentation decisions about an already-decided finding, not claims
about contract membership. Not done: adding corpus cases that populate those
buckets. A suppression case would mean threading a `SuppressionList` through
the *shared* FP-rate corpus (`check_fp_rate.CASES`, whose own gate would
have to be re-validated against the changed fixture shape), and redundancy
and reconciliation are produced by the pipeline rather than selected by
corpus input — so the three rows stay empty on today's corpus, and the gate
becomes live for them the moment such a case exists rather than the moment
someone remembers to add the collection.

**An eleventh round found two identity defects in the persisted type graph
itself, both of them the same "a string is not an identity" mistake at
opposite ends of an edge.** (1) `decl:` nodes were keyed by *linker*
identity, whose fallback for a producer that recorded no mangled symbol is
the bare display name — which every overload of a name shares. A public
`over()` and a private `over(Secret *)` therefore landed on one node, the
public root inherited the private overload's edge to `Secret`, and a
re-evaluation answered `IN_CONTRACT` for a private `Secret` layout change
the live evaluator — which walks each declaration object separately —
proved out of contract. The node key now falls back to
`name + "(params)->return"` for the one case where the plain name names
more than one unmangled declaration, the same "most specific available
identity, ambiguity-safe" tiering `finding_identity.py` and
`diff_helpers.TypeMap` already use. Deliberately a tie-break rather than a
new encoding: `_without_shared_export_aliases` exempts a spelling that is a
root's *own* node identity from its alias pruning, so refining an
unambiguous name would prune a genuine export root — the same strengthening,
one corner over. Two unmangled declarations agreeing on name, parameters
*and* return type still share a node, which is lossless (their signature
edges and their `owner_class_of` result are derived from exactly those
fields), so no ledger-level ambiguity flag is needed for a residual case.
Rootness still keys on linker identity, which is what the export table
matched — every member of an unmangled overload group shares one, so they
are rooted or not as a group, exactly as live roots them. (2) The owner-class
identity set was keyed on `RecordType.name` alone, while `owner_class_of`
answers with whatever complete scope chain the producer recorded — for a
castxml/clang class stored as `name="Widget"`/`qualified_name="ns::Widget"`
that is the qualified spelling, which the set never contained, so the
method-to-owner edge was dropped and a live `IN_CONTRACT` replayed as
`PROVEN_OUT_OF_CONTRACT`. The set is now a field-for-field mirror of
`export_surface.compute_export_surface`'s own `owner_seed_by_identity` map
— the map the live exports closure actually seeds through — rather than a
re-derivation of it, which fixed a *third*, unreported defect in the same
stroke: the old set also registered a leaf-only bare `name`, letting an
`api::run()` namespace fragment match an unrelated `other::api` "exactly"
after all, the very collision the exact-match rule exists to close, from the
record side. Both were reproduced end-to-end against a live run before
fixing and re-checked as `compare_decisions(...).strengthened == ()` after.

Observed while mirroring that map, and **not** fixed here: the graph's
general *type-reference* resolution still goes through
`surface._index_surface_types`'s un-augmented index, which keys on `name`
and its `::` tail only, while `compute_export_surface` augments its own copy
with every `qualified_name`. A field spelled `ns::Widget` on the
castxml/clang path therefore reaches the record live-in-`exports` but
produces no edge here. Fixing it means changing the closure for the
`public` domain too, whose live index is *not* augmented — over-linking
there flips a live `PROVEN_OUT_OF_CONTRACT` to a replayed `IN_CONTRACT`,
which `compare_decisions` counts as strengthening just as under-linking
does. Getting both domains right at once needs its own scoped design
(per-domain resolution, or an audit of what `surface.py`'s own bare-tail
lookup would have to become), not a drive-by extension of an owner-edge fix.

**A twelfth round found one more soundness defect of the same shape and two
receipts naming the wrong input.** (1) The persisted graph resolves every
spelling through one flat index, while the live evaluator asks two separate
questions of two different node kinds — "is this symbol an export root"
(declarations) and "is this type in the closure" (types). With an exported
`api(foo *)`, a reachable `struct foo` and an unexported function `foo`, the
spelling `foo` landed on both `decl:foo` and `record:foo`, and the reachable
*record* placed the *function* removal `IN_CONTRACT` against a live
`PROVEN_OUT_OF_CONTRACT`. `_entity_lookups` now carries the admissible node
kinds alongside each spelling: a symbol may reach a declaration node only
under `_symbol_matches`'s own rule (a type-level kind needs a *mangled*
symbol), a type node only when the finding is type-scoped at all, and
`caused_by_type` — a type name by construction — reaches type nodes alone.
Membership is decided on that filtered set; the overlay override and evidence
attribution stay on the full resolution, since both are keyed by the symbol
live too (`_change_matches_symbols` asks nothing about node kind).
(2) `policy.base` attributed the resolved value to `checker.compare`'s
`policy` argument even when a `PolicyFile`'s own `base_policy` had *replaced*
it (`effective_policy`) — naming an input the run ignored. It now records the
policy file, the same rule `policy.overrides` and `surface.internal_namespaces`
already follow from the same file. (3) `gate.severity` was one aggregate
entry for four independently-resolved categories, so
`--severity-abi-breaking` beside an `addition` level only `.abicheck.yml`
supplied labelled both `EXPLICIT_CLI` — and used a key the canonical resolver
does not have (it tracks `gate.severity.<category>`,
`compatibility_evaluation_frontend.SEVERITY_CATEGORY_FIELDS`).
`with_resolved_gate` now takes a per-category mapping, and both front ends
supply one: the CLI per typed flag (with `--severity-preset` counting for all
four), the MCP tool uniformly, since one `SeverityConfig` argument genuinely
is one layer for all four.

**A thirteenth round closed the last standing item — bare `record:<name>`
node identity — and one more provenance gap.** A record/enum node was keyed
by `RecordType.name`, which on the castxml/clang path is the *bare leaf*, so
`ns1::Foo` and `ns2::Foo` collapsed onto one node and their field edges were
unioned; an export rooted in `ns1::Foo` then placed a layout finding on
`ns2::Foo` `IN_CONTRACT` where the live evaluator answered
`UNKNOWN_UNRESOLVED` on exactly that ambiguity. Keying on `qualified_name or
name` (`_type_identity`) separates them, with the bare leaf demoted to an
`alias:` spelling — which is what it honestly is once two records answer to
it. Narrowing the graph this way cannot strengthen anything: a snapshot with
an ambiguous bare tail already reports `identity_coverage=PARTIAL`, and
`can_prove_exclusion` requires `COMPLETE`, so the only conclusive negative is
off the table for precisely those snapshots.

Separating the nodes was necessary but not sufficient, in two steps found in
the same round. First, the *lookup* still resolved the shared leaf to both
nodes and took whichever was reachable; the replay now refuses a spelling
that lands on more than one type node, mirroring `_confirmed_type_matches`'s
"a candidate that matched only ambiguously proves nothing in either
direction". Second — and this is the part a per-lookup node count alone
misses — live rejects a match on *two* clauses, the second being "a qualified
name whose own trailing tail is ambiguous". With a global `Foo` beside a
namespaced `ns::Foo`, a finding on `ns::Foo` resolves to exactly one node,
yet an exported signature spelling the bare `Foo` linked *both*, so the
closure hit proves nothing. The replay now checks that clause too, which
required the bare `::` tail of a qualified identity to become an `alias:`
spelling in the persisted graph — with a DWARF producer, which bakes the
whole path into `name`, nothing else recorded it, so the collision was
invisible on replay. Rootness is still decided *before* ambiguity, matching
live's own order: a declaration that is a root answers its own membership by
its own linker identity.

Also fixed: a typed `--contract exports` was persisted as an `API_REQUEST`
from `checker.compare`, since the core verb receives the value and not the
option that supplied it. `with_field_provenance` is the value-free
counterpart of `with_resolved_gate` for exactly this shape, and the CLI now
refreshes `contract.mode` when — and only when — the flag was really typed,
so the `LEGACY_ALIAS` provenance `resolve_legacy_contract_mode` records for
`--scope-public-headers` still survives untouched.

**A fourteenth round replaced an inference with an observation.** Twice now
the replay had re-derived "is this spelling ambiguous" from the persisted
graph, and twice a case slipped through that the live evaluator's own
`ambiguous_type_names` states outright — first a qualified candidate whose
*tail* collides, then two records sharing one canonical identity, which are
one node in the graph but two entries in `_index_surface_types`'s own count
(it tallies record *objects* per name, not distinct identities — a detail an
earlier docstring here got wrong, and the reason a graph-derived count can
never be equivalent). The provider record now persists
`ambiguous_identities` directly, and `_type_identity_is_ambiguous` answers
both of `_confirmed_type_matches`'s clauses from that set. The inference
helper is deleted. The general lesson, worth stating because it recurred:
an observation the replay needs is cheaper to persist than to reconstruct,
and a reconstruction that is *nearly* faithful reads exactly like a correct
one until a reviewer finds the case it drops.

Two provenance receipts were also naming inputs that did not exist. The CLI
derived every category's severity layer from `ResolvedCompareConfig.
severity_active`, which is deliberately run-wide ("a level was set
*anywhere*") — so a run whose only input was `--severity-abi-breaking`, with
no `.abicheck.yml` at all, recorded the other three categories as
`PROJECT_CONFIG`. `BuildConfig` carries the four levels separately, so the
honest per-field answer was already available. The MCP tool had the same
shape one layer over: `severity_config is not None` marked all four
`API_REQUEST` when the caller had supplied one. Both now record per
category. Notably, *both* were pinned by tests written in the immediately
preceding round — the tests asserted the wrong behaviour as intended, which
is the failure mode a test written from the implementation rather than from
the contract always has.

**A fifteenth round closed the reason all of the previous ones were found by
reviewers rather than by CI.** Every soundness defect this feature has had
was a *replay* that out-claimed the live decision, and there was no
corpus-level gate on that path at all — only hand-written unit cases. The
shadow measurement now re-evaluates each corpus case through the real wire
format and reports `replay_strengthenings`, with baseline 0.

Standing that gate up immediately proved it *vacuous*: two deliberate
regressions (dropping the node-kind filter, then the ambiguity refusal) both
still passed, because no corpus pair had an ambiguous identity — 0 of 32
snapshots had a colliding bare tail, so no replay decision could differ
whatever the implementation did. One case was added (`ambiguous_namespaced_leaf`:
a real break on `ns1::Cache` beside an unrelated `ns2::Cache`, both spelled
bare `Cache` the way castxml records them), after which the same regressions
fail the gate. Both facts are pinned as their own assertions, because "the
gate is green" and "the gate can fail" are different claims.

Two real gaps were found by finally giving the `enum:` node kind any test
coverage — this suite had never constructed an `EnumType`, though enums are
keyed by the same `_type_identity`, aliased the same way, and share the same
ambiguity set. First, every **member-level** finding degraded to
`UNKNOWN_UNRESOLVED`: `_type_candidates` strips `Mode::B` to its owner
`Mode` for the owner-plus-member kinds and the replay did not, so an
`enum_member_added` on an exported enum resolved to nothing. Sound (a
weakening), but it made the replay useless for that whole family. Second,
a fallback declaration key collides across the two identity *tiers*, not
only within its own: a private header-only `foo(Secret *)` beside a public
declaration whose recorded `mangled` is literally `foo` merged onto one
node, and the public root inherited the private overload's edge to `Secret`.
Every recorded linker identity is now reserved, so a display-name fallback
yields to one whichever tier it came from.

**A sixteenth round found the same over-resolution one type kind over.** A
typedef was registered under its bare `::` tail as well as its exact key,
the way records and enums are — but `_walk_type_closure` follows a typedef
with `snap.typedefs.get(name)`, a plain dict lookup with no tail fallback at
all. Records and enums get a tail only because `_index_surface_types` gives
them one; a typedef has no such spelling. So a public signature spelling the
bare `Alias` reached a qualified `ns::Alias -> Secret`, and a private
`Secret` layout change the live evaluator proved out of contract
re-evaluated as `IN_CONTRACT`. The tail registration is removed; the exact
key still resolves, which is what live matches on. Worth noting the shape:
this is the third distinct place where "resolve it the way the neighbouring
kind is resolved" was wrong because the live walk treats the kinds
differently — the mirror has to be per kind, not per intuition.

**A seventeenth round asked what that fix owes the persisted format, and
the answer was a version bump.** Removing the tail registration means the
same `AbiSnapshot` now yields a *different* `TypeGraphSnapshot` — the
graph is persisted evidence a later build re-walks, so a context written
before the fix and one written after it are not interchangeable, even
though every block's *shape* is identical and no schema counter moved.
Leaving `IDENTITY_ALGORITHM_VERSION` at 1 would have had both formats
advertise one identity algorithm while resolving typedefs differently: a
consumer could neither tell them apart nor refuse the newer semantics.
Bumped to 2, which is precisely the concern this counter was split out to
carry (the schema counters version a block's shape; this one versions the
algorithm that filled it). The bump does not reject the older graphs —
D6's rule accepts a version older than or equal to this build's, possibly
degraded, and refuses only a newer one, so a v1 context still replays here
while a v2 context is refused by a build that predates the fix. That is
the direction that matters, and it is also the first time these four
counters have actually diverged, which the version-strategy test now pins
as executable proof that they can.

One finding from an earlier round was **not** taken: replacing
`report_finding_id`'s `"\x1f"` field delimiter with a length-prefixed or
canonical-JSON encoding. The ambiguity it guards against requires a literal
`\x1f` (ASCII unit separator) inside a kind slug, symbol, value, source
location, or description — none of which any producer in this codebase can
emit — while changing the encoding rehashes *every* finding id, which is a
documented-stable, user-visible fingerprint (report schema 2.3) that
consumers key waivers and cross-run correlation on. The cost is certain and
the risk is not reachable, so the delimiter stays.

Two smaller limits worth naming rather than discovering later. The
`evaluation_context` this build assembles is resolved by `checker.compare`,
which sees only its own arguments — so `contract.mode` carries the real D7
`LEGACY_ALIAS`/explicit provenance, while `policy.base` records
`API_REQUEST` (a typed caller stated it) rather than claiming a CLI/recipe
layer this core verb cannot observe. Phase 5, which routes every front end
through one already-resolved config, is what replaces it; under-claiming
until then is the honest encoding, since a wrong provenance layer is exactly
what D7's receipts exist to make impossible. And the decision receipt's
per-finding keys fall back to `"<kind>:<symbol>"` unless a caller supplies
the report's own id — `finding_identity.report_finding_id`, which `checker`
imports directly and every post-`compare()` caller injects as a callable,
since importing the renderer that re-exports it would close a
`checker → contract_context → reporter → checker` cycle the
`import-cycle-growth` gate rejects.

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

**Updated (2026-08-01): the phase's headline item landed for `compare` --
"every front end consumes one `CompatibilityEvaluationConfig`" (§12's
Definition-of-Done item 2) is now true of the native CLI.** Phase 1 built
the canonical resolver and Phase 4 persisted an `evaluation_context` block,
but nothing joined them: the block carried what `checker.compare`
reconstructed from its own arguments, and the CLI patched the two fields it
happened to know about afterwards (`cli_compare_receipt.record_resolved_gate`
for the gate, `with_field_provenance` for a typed `--contract`). Because a
core verb sees values and not the inputs that chose them, every other field
was recorded at `API_REQUEST` -- honest, but useless to an audit consumer
asking *which* layer selected a value. `run_compare` now resolves its own
inputs through `resolve_compatibility_evaluation_config` -- the raw CLI
values (not the already-merged locals several of them are overwritten with),
the set of parameters Click reports as really typed, the discovered
`.abicheck.yml`, and the already-loaded `--policy-file`/`--suppress`
documents (passed in rather than re-read, so one content's digest cannot be
paired with another's rules) -- and installs the result through a new
`contract_context.with_resolved_config`. Every field's provenance is now the
real D7 layer with the path and digest a replay would re-read.

Two seams needed a decision rather than plumbing. **The gate** is resolved
by `resolve_compare_config` *after* `compare()` returns and is what the
verdict and exit code were actually computed from, so its *values* are still
written by `with_resolved_gate` from that resolution, while its *provenance*
comes from the canonical resolver. That split is only sound while the two
agree, so it is asserted rather than assumed:
`tests/test_cli_compare_config_receipt.py::TestGateParityWithTheLiveRun`
resolves both from the same inputs across a parametrized matrix and compares
the scheme and all four severity levels. **Observed overlays** go the other
way: `contract.overlays`/`surface.explicit_scope` are recovered from the
run's own evidence ledger (`overlay_selection`) and name what actually
applied, including `--post-manifest`, which no front-end input model
describes -- so `with_resolved_config` keeps both from the core's version
whenever the ledger recorded any overlay, and takes the resolver's otherwise.

**A real bug fell out of the wiring, in `--profile`.** `apply_compare_profile`
fills a profile's values into the command's own kwargs wherever the user left
the option alone and deliberately does *not* stamp a parameter source
("nothing downstream needs the source" -- its own docstring). Once a receipt
resolves D7 layers, that stopped being true in the worst way: the injected
value is indistinguishable from a typed one, so `--profile ci-gate` first
resolved `gate.exit_code_scheme` as `EXPLICIT_CLI`, and blanking it there
resolved it to `legacy` -- a *wrong value* for a run that really scored under
`severity`, not merely an unnamed source. The profile now records what it
injected in `ctx.meta` (`RUN_PROFILE_META_KEY`), the receipt blanks those
keys from the explicit tier, and a new `RunProfileInputs` re-contributes them
at D7's own `run_profile` layer -- below an explicit flag, above the project
config.

That layer carries **a deliberate ADR deviation, recorded rather than
smoothed over**: D7 scopes `run_profile` to execution fields (depth, format,
budget, workflow) and puts the exit-code scheme in the *gate* namespace, yet
the pre-existing `ci-gate` bundle really does select it. Encoding what the
bundle does today is the honest receipt; both ways to remove the deviation
(move the key out of `ci-gate` into a gate pack, or amend D7) change
user-visible behavior or the ADR, so neither belongs in the wiring change
that found it. Only that one field passes `allow_run_profile=True`, so a
profile assigning any other field still raises -- which is what keeps the
deviation from spreading.

`cli_options.py` crossed the 2000-line hard cap on the way, so ADR-040
Lever 3's run-profile *data* (the table, its `--profile` option, the meta
key) moved to a new leaf, `cli_profiles.py`, re-exported from its former
home. `apply_compare_profile`/`_profile_targets_set_input` deliberately
stayed behind: they reach `cli_resolve`, and moving them would have made the
new module a fresh member of the CLI-registration import cycle -- exactly
what the `import-cycle-growth` gate exists to catch, and unnecessary, since
the split works without one.

**Two Codex review findings on this slice, both real, both fixed.** (1) A
`--required-symbol` contract switches an untouched `--policy` to `plugin_abi`
(ADR-043) — a value that is neither typed, nor a project setting, nor the
built-in default, so a resolution reading only typed flags recorded
`strict_abi` while the run scored under `plugin_abi`, exactly the wrong-receipt
class this wiring exists to remove. `ExplicitCompatibilityInputs` gained
`policy_base_option`: the value is read as stated, and the receipt names
`--required-symbol` rather than a `--policy` nobody passed. It stays in the
`LEGACY_ALIAS` slot, so an explicit `--policy-file` still outranks it — which
is what `effective_policy` does live. (2) Keeping the *observed*
`surface.explicit_scope` provenance wholesale dropped what the resolver had
just captured for `--public-symbols-list`: the layer, the option, the path and
the digest of the file that selected the scope, leaving a replay unable to find
it. Value and receipt answer different questions, so they now follow different
rules — the ledger's value (what applied), the resolver's entry with the
observed hop appended (what selected it), and the core's entry alone only when
the front end modelled no scope at all (a `--post-manifest`-only run).

**Two more Codex findings on the same slice, both about a receipt that names
a source it cannot prove.** (3) The `--required-symbol`-derived policy fix
above hardcoded `--required-symbol` as the selector, but a
`--required-symbols FILE` run never passes that flag — the receipt named an
option the user did not use and left the list that really chose `plugin_abi`
unidentifiable. The file form is now named whenever a file was given, with
its path and a digest taken from `_load_required_symbols`' own single read.
(4) `ProjectCompatibilityInputs` was built with a path and no `sha256`, so
every project-derived entry could name `.abicheck.yml` but not prove which
revision of it supplied the value — unlike the policy and suppression
sources, which carry digests for exactly that reason.
`load_build_config_with_digest` returns the config and the digest of the
bytes it parsed from one read (over raw bytes, so a CRLF file matches what is
on disk rather than its newline-normalized rendering), and
`_resolve_compare_config` threads it through. It landed in a new leaf,
`buildsource/build_config_io.py`, rather than beside `load_build_config`:
`inline.py` is at its 2000-line hard cap, and the dependency runs one way
(the new module imports `BuildConfig`; `inline` does not import it back), so
no cycle forms and every values-only caller keeps using `load_build_config`
unchanged. Hashing the path at the call site instead was rejected —
`ProjectCompatibilityInputs.sha256`'s own docstring refuses to compute the
digest from a path for exactly the pairing reason a second read reintroduces.

**Three more findings, one of them a CI break.** (5) The new module reads
YAML, and `pyproject.toml`'s stubless-PyYAML mypy override lists modules
explicitly — omitting it broke the zero-error typecheck gate in any
environment without `types-PyYAML`. Added to the existing override rather
than an inline ignore, per this file's own rule. (6) `contract.overlays` is
routable from a `kind: contract` pack, so "no front-end input" was wrong:
with a pack assignment *and* an observed overlay, replacing the value
dropped the pack's selection and its provenance. The value is now the union
of both sets and the entries merge, the same rule `surface.explicit_scope`
already followed (CodeRabbit review). (7) `COMPARE_CONFIG_PARAMS` declared
that a renamed option should fail loudly, but nothing checked the caller's
mapping against it — `resolve_cli_config` now rejects a partial one instead
of letting a dropped key resolve as "not stated".

**An eighth finding closed the last re-read.** `--public-symbols-list` was
read twice: once by `_collect_force_public_symbols` for the live overlay, and
again by `compare_cli_inputs` for the receipt. Beyond the digest-pairing risk
every other source was already fixed for, this one had a worse failure mode —
a file deleted after the comparison started failed an *otherwise finished*
run during receipt generation. `resolve_force_public_scope`
(`cli_helpers_compare.py`) now does the one read both consumers share, and
`collect_force_public_symbols`/`compare_cli_inputs` each accept the
already-read list. Test:
`test_a_symbols_list_deleted_mid_run_does_not_fail_the_comparison`, which
deletes the file at the moment the receipt is recorded. Two cleanups fell
out: `with_field_provenance` lost its last caller when the resolver took over
`contract.mode` provenance, so it was deleted rather than left as an uncalled
seam; and `cli_compare_helpers.py` crossed its cap again, which the helper's
new home in `cli_helpers_compare.py` resolved.

**A ninth finding sharpened the file-attribution rule itself.** "Name the
file form whenever a file was given" was still a claim about what was
*passed*, not what *contributed*: for `--required-symbol api_b
--required-symbols empty.txt` the file parses to nothing, so naming it omits
the option that actually made the contract non-empty.
`_load_required_symbols` now also returns what the file itself contributed,
and the file form is named only when that is non-empty — true and
audit-carrying when it holds, the inline form otherwise.

**Updated (2026-08-01, same PR): the MCP front end closed the second half of
Definition-of-Done item 2.** `abi_compare` patched its own gate
(`mcp_server._record_resolved_gate`) instead of resolving a config, leaving
the CLI as the only front end that consumed one. A new leaf,
`mcp_compare_receipt.py`, does for the tool what `cli_compare_receipt.py`
does for the CLI: `resolve_tool_config` hands the tool's real arguments
(`policy`/`policy_file`/`suppression_file` and the four severity levels) to
the same canonical resolver at `FrontEnd.API`, and `record_resolved_config`
installs the result through `with_resolved_config` before the report is
rendered, keeping the same "values from the run, provenance from the
resolver" split the CLI gate documents. The 90-line `_record_resolved_gate`
was deleted rather than left as a second path to the same block.

Resolving through the shared resolver removed a real divergence, not only
duplicated code. The deleted patch recorded `gate.exit_code_scheme`'s
provenance as `api_request` whenever a severity argument was passed --
but no caller ever asks for the `severity` scheme there; it is *derived*
from a severity level being in effect, which the canonical resolver
records as a built-in default with `source_kind: auto`, and whose real
provenance is carried by the severity field itself. `compare`'s receipt
for the equivalent input already read `built_in_default`/`auto`, so the
MCP tool had been over-claiming relative to the CLI on the one field both
front ends compute the same way.
`test_mcp_server_coverage.TestAbiCompareResolvedGate` was updated to the
corrected answer with that reasoning recorded in it.

The receipt states only what this tool can state, which is less than the
CLI's: `abi_compare` has no scope, contract-mode, public-symbol, or
exit-code-scheme parameter, so those resolve as built-in defaults rather
than as an API request. That is not an under-claim -- unlike
`CompareRequest.scope_public`, whose dataclass default is still a caller's
choice, there is nothing here for a caller to have chosen, and
`compare_snapshots`' own `scope_to_public_surface` default agrees with
`BUILT_IN_DEFAULT_CONTRACT_MODE` by construction. New:
`tests/test_mcp_compare_config_receipt.py`, whose last test runs Phase 1's
own gate across the two live front ends -- equivalent CLI and MCP input must
produce zero `cross_front_end_differences`.

**Updated (2026-08-01, same PR): §6.4's cross-command parity Gate is now an
executable field-for-field check.** The suite compared exit codes only, which
cannot tell "both commands found the same break" from "both commands found
*a* break" -- and two of §6.4's named equal fields were not observable from
`scan --against` at all: it emitted no detector provenance, and it had no
`--contract-evaluation`, so no finding of its could carry a contract
relevance/reason/assurance/evidence side to compare. Three changes closed
that:

1. `scan --against` gained `--contract-evaluation`/`--contract`, threaded
   through `run_scan_core`/`_run_baseline_compare` into the
   `compare_snapshots` call it already made, with matching `ScanRequest`
   fields for the Python API. Same advisory contract as `compare`'s, same
   `--contract requires --contract-evaluation` rejection (checked in
   `scan_cmd` so it is a clean exit-64 usage error before any scanning
   work), and both are in `_COMPARISON_ONLY_FLAGS` so passing them without
   `--against` is rejected rather than silently discarded.
2. The scan summary's per-finding dicts gained `finding_id` -- the same
   canonical identity `reporter._change_to_dict` emits, so the two commands'
   findings are *joinable* rather than merely both present -- plus the four
   contract keys under the same "absent means unstamped" rule the reporter
   uses, and the summary gained a `detectors` block with the reporter's own
   shape and "findings or a coverage gap" filter.
3. `tests/test_scan_compare_parity.py`'s new `TestFieldForFieldParity`
   compares the two commands' shared gating findings as records joined on
   `finding_id`, across a matrix of defaults / `--policy` / scope on / scope
   off / `--public-symbol` / all three `--contract` domains /
   `--pattern-verdicts`, on a snapshot pair rich enough to engage the
   function, type, and enum detectors at once. Suppression is compared
   field-for-field on both audit trails (a rule silencing a *different*
   finding on each side would otherwise still report "1 suppressed" on
   both), and §6.4's "scan-only findings may be appended, they cannot
   rewrite the shared comparison findings" is its own assertion.
   `TestBinaryAndMixedInputParity` (`integration`) extends the same
   assertions to two real compiled binaries and to a persisted-snapshot
   baseline against a live binary -- the two commands resolve operands
   through different code paths, so snapshot parity does not imply binary
   parity.

`SCAN_SCHEMA_VERSION` went to `1.6` for the additive `diff`-block keys
(`finding_id` on every finding, the optional `detectors` list, and the four
contract keys under `--contract-evaluation`), with the entry recording
exactly what a run *without* the new flag changes relative to 1.5: the
version marker, `finding_id`, and the `detectors` list -- the last of which,
unlike the contract keys, is emitted regardless of the flag.

Two smaller things the work forced. The helper reads both reports from
`-o` files rather than captured stdout: parsing scan's stdout works for a
snapshot pair and breaks the moment the operand is a real binary, which
writes warnings to the same stream. And because the scan summary
re-implements the reporter's contract-field projection rather than importing
it, a test pins that the two name the same decision with the same keys, so
the duplication cannot drift into two vocabularies for one field.

**Updated (2026-08-01, same PR): the unsuppressible coverage ledger landed
-- §12's item 6 and this phase's last open piece.** Item 6 is two claims,
"suppression is explicit and coverage failures are unsuppressible". The
first was already true (the ADR-013 audit trail, extended to `scan
--against` earlier in this phase); the second had no implementation at all.

`abicheck/contract_coverage_ledger.py` (a new leaf) *derives* §6.1's
`contract_coverage_failures` from what Phase 3's provider ledger already
observed: one entry per provider/domain coverage failure, carrying which
provider and side failed, the record it came from, and **why** -- the
provider's own status (four distinct ways of not having the fact, kept
distinct because they need different fixes), an incomplete search, or
partial identity coverage, which §4.2 requires separately from overall
completeness. `contract_coverage_exit_contribution` states the `0`/`1` §6.1
gives the ledger; it is reported, not applied, since the independent
coverage exit is Phase 7's alongside the default flip.

**Derived, not observed.** A provider record is a fact about what was
searched; whether it is a *failure* depends on the selected domain, which is
policy. §7 is explicit that the two disagree -- under `exports`,
"public-header/manifest/consumer failures are unrelated and advisory" --
so recording a failure at collection time would bake one domain's policy
into a policy-independent block, and would go stale the moment
`reevaluate_from_evidence` re-decides under a different mode. Both inputs
the derivation needs are already in the persisted context, so it answers per
mode instead. Verified end to end: a header-only pair yields two
`export_table` failures and contribution `1` under `--contract exports`, and
none under `public`/`all`, from the identical records.

**Unsuppressibility is structural, not a flag.** §6.2 says an ordinary
change suppression "cannot ... suppress a provider/domain coverage failure".
That holds here because a coverage failure is not a `Change`: no
`ChangeKind`, no symbol, never in `DiffResult.changes`, so
`checker._filter_suppressed_changes` -- the single place suppression is
applied -- cannot see one. `suppression_reaches_coverage_failures()` is the
executable proof rather than the enforcement: it hands each failure to
`SuppressionList.is_suppressed`, the same predicate the filter itself
consults, and reports what matched. A test calls it with a wildcard
`symbol: '.*'` rule -- one that matches every real finding -- and asserts the
answer is still empty; a second asserts the failure carries none of the
attributes suppression selects on.

`REPORT_SCHEMA_VERSION` went to `2.26` for the two additive top-level keys,
with the packaged JSON Schema extended and republished. `[]` is emitted
rather than omitted: it is the checkable answer "this domain closed", which
an absent key could not be told apart from "not computed".

**Updated (2026-08-01, same PR): Phase 5's remaining three items closed --
"same typed config" for the third front end, the `packs` parity axis, and
the Gate's downstream-consumer clause.** A re-read of the phase's own
sentences turned up three things the earlier rounds had not done:

1. **`scan --against` resolved no typed config, and emitted no receipt.**
   Phase 5's body is "route both direct compare and scan baseline compare
   through the same core *and same typed config*"; only the core half was
   done. Worse, the command *computed* a contract context under
   `--contract-evaluation` (its findings were stamped from one) and then
   dropped it, so fixing only the resolution would have changed nothing
   observable. `cli_scan_receipt.py` is the third and last front-end
   receipt: it resolves through the canonical resolver -- reusing
   `compare_cli_inputs` rather than re-implementing normalization, since
   `scan` deliberately shares `compare`'s option destinations and two
   normalizers is exactly how the two would stop agreeing -- and the scan's
   `diff` block now carries `contract_context` plus the coverage ledger,
   serialized by the same encoder `compare` uses. Real D7 layers now
   appear where `API_REQUEST` used to: `--contract` reads `explicit_cli`,
   `--policy` reads `legacy_alias`, `--public-symbol` reads `explicit_cli`.
   A parity test asserts the two commands' *receipts* are byte-identical
   for the same inputs, one level below the findings §6.4 already compared.
   `SCAN_SCHEMA_VERSION` → `1.7`.

2. **Nothing selects packs, and this round did not change that.** The Gate
   lists `packs` among the parity axes, but `pack_paths` is only ever `()`:
   no front end has a `--pack` option, so D8's pack-conflict resolution
   (built and unit tested in Phase 1 slice 2) still has no live caller and
   the axis remains untestable end to end.

   A `--pack` flag *was* added on both commands in this round and then
   removed before merge, because a Codex review round caught what it
   actually shipped: pack assignments reached the resolved configuration and
   the persisted receipt, but never the engine. A `kind: policy` pack
   overriding `func_removed` would have been recorded as active
   configuration while leaving the verdict and exit code untouched, and
   without `--contract-evaluation` the manifest was not even loaded, so a
   malformed one was silently accepted. The parity tests written alongside
   it passed precisely because they asserted the two commands *resolve*
   packs identically and never that a pack changes a result — a flag that
   does nothing satisfies that.

   Exposing configuration that does not configure is worse than not
   exposing it, so the flag is gone rather than papered over. Making packs
   real means feeding `policy.overrides` into the verdict path (the
   `PolicyFile`-shaped overrides `compare_snapshots` already consumes),
   with D8's precedence against an explicit `--policy-file` decided, and
   re-verification against the FP-rate and tier-accuracy gates — its own
   scoped slice, not a drive-by extension of a receipt change. **The
   `packs` axis of the Phase 5 Gate is therefore still open**, and is the
   one item this round did not close.

   **Updated (2026-08-03): the `packs` axis closed — `--pack` is back, and
   it configures the run.** `abicheck/pack_application.py` is the missing
   application layer, and it is deliberately *not* a second resolver: it
   takes the object the canonical resolver already produced and reads back
   only the fields whose `ValueProvenance.source_kind` is `pack_manifest`.
   Nothing in it re-implements D7 precedence or D8 conflict detection, so a
   front end cannot apply a value the resolver ruled out — if
   `--policy-file`, `--exit-code-scheme`, a `--profile`, or `.abicheck.yml`
   stated the field, its provenance names *that* source and the application
   contributes nothing for it. What the packs did supply is folded into the
   two objects the comparison is actually scored from: a `PolicyFile`
   (`policy.overrides` and `surface.internal_namespaces`) and the resolved
   compare config (`gate.exit_code_scheme`, `gate.severity.*`).

   That forced one ordering decision worth recording: the configuration is
   resolved from the *explicitly given* policy file, and only then are the
   packs folded into a new one. Folding first would present a pack's own
   override to the resolver as an explicitly stated `--policy-file` value —
   outranking the packs it came from, and misreported in the receipt. It
   also moved the whole resolution to *before* `compare_snapshots` rather
   than after it (`resolve_and_apply`), since an object that configures the
   run has to exist before the run; `record_resolved_config` now installs
   the same object instead of resolving a second time, which would re-read
   every manifest and be handed the already-folded policy file.

   The half that keeps this from being decoration again is a rule the
   reverted version had no place to put: **a pack may only assign a field
   this build actually applies.** `UNAPPLIED_PACK_FIELDS` names the three
   routable fields with no engine consumer (`contract.unresolved` — Phase
   7's own coverage exit; `contract.overlays` — the real overlays name
   concrete `--post-manifest`/`--public-symbol` inputs a pack has nothing to
   point at; `assurance.require_evidence` — `PolicyFile.require_evidence` is
   a per-layer mapping, not this field's single bool), each with its reason,
   and a manifest assigning one is a usage error naming the field. It is
   the complement of what is applied rather than a second hand-kept list, so
   a newly-routable field is applied or listed, never neither. The same rule
   rejects a `kind: gate` pack on `scan` — whose exit code follows its
   verdict directly, the same reason `cli_scan_receipt._without_gate_settings`
   blanks the gate rather than reporting one the run never used — plus
   `--pack` without `--against` (a pack's only application there is the
   baseline comparison's policy) and `--pack` on a directory/package
   compare, whose fan-out dispatches before the configuration is resolved.

   **Two review findings on the first revision, both real, both about the
   same principle the module states and then broke in one place.** (1)
   `apply_to_compare_config` re-derived the exit-code scheme locally
   (`"severity" if severity_active`) instead of reading the resolved one, so
   a gate pack assigning only a severity level silently overrode an explicit
   `--exit-code-scheme legacy` — turning a BREAKING run's exit 4 into 0,
   which is exactly the "a pack never overrides a stated value" rule D8
   exists to enforce. The resolver already had the right answer (its `auto`
   default folds in the pack's own levels via `_severity_active`, while an
   explicit flag contributes an outranking `EXPLICIT_CLI` candidate), so the
   fix was to read it. (2) `--pack` was validated *after* `compare`'s
   `--dry-run` emit, so a dry run reported "ok" for a manifest the identical
   real run rejects with 64 — against a convention this same file states
   twice for its other guards. Manifest validity moved ahead of the emit;
   pack-vs-pack *conflict* detection deliberately did not, since D8 exempts a
   field another layer states and those layers are not resolved that early —
   checking there would make a dry run *stricter* than the real run, the same
   divergence in the other direction.

   `tests/test_pack_application.py` leads every behavioural test with an
   exit code that *differs* with and without the pack, deliberately: the
   parity tests written alongside the reverted flag passed precisely because
   they only asserted that the two commands resolve packs identically, which
   a flag that does nothing satisfies. The §6.4 receipt-parity assertion is
   still there, but second, and now with a real pack on both sides.

   The `cli.py`/`cli_options.py` hard-limit split it forced is kept:
   `cli_contract_options.py` now holds the three ADR-049 contract options,
   which is a real improvement independent of packs.

3. **No downstream consumer understood the new schema** (Phase 6's Gate).
   §6.1 names two specifically. SARIF now emits the coverage ledger as
   `invocations[].toolExecutionNotifications` -- SARIF's own channel for a
   tool-level statement, rather than more `results[]` entries a consumer
   would count as findings -- with `executionSuccessful`/`exitCode`
   deliberately untouched, since the independent coverage exit is Phase 7's.
   JUnit emits an `abicheck.contract_coverage` suite whose every case is an
   `<error>`, which is precisely JUnit's error-vs-failure distinction ("the
   test could not run" vs "it ran and failed") and satisfies "never as a
   passed compatibility test"; the errors roll into the document totals so a
   dashboard reading only the root counts still sees them. Both are absent
   -- not empty -- without `--contract-evaluation`, and both emit an *empty*
   ledger when the domain closed, because "checked, nothing missing" and
   "never checked" are different states. `aggregate` needed no change: it
   already keeps ADR-042's three orthogonal axes, and folding the advisory
   contract ledger into its coverage axis would *apply* an exit contribution
   the plan reserves for Phase 7.

### Phase 6 — opt-in public mode and corpus validation

Expose `--contract public|exports|all`. Preserve
`--no-scope-public-headers` as the exact alias for `all`; migrate
`--scope-public-headers` to intentionally stricter `public` semantics. Keep the
old default while running case97, pvxs, real-world corpus, ELF/Mach-O/PE,
stripped, versioned, C/C++, snapshot, package, and downstream
renderer/aggregate lanes.

**Gate:** zero unexplained public-break losses; reviewed FP reductions; measured
and accepted unresolved rate; all downstream consumers understand new schema.

**Updated (2026-08-01): the Gate's corpus axis closed.** The measurement's
three zero baselines were already green, but on one corpus: the FP-rate
corpus, which is built to exercise public-header scoping and so carries no
export tables at all. The consequence was recorded honestly in the previous
note and is the thing this slice fixes -- `exports` measured **100%
unresolved on every case**, so "measured and accepted unresolved rate" was,
for that domain, measuring the absence of evidence rather than the domain.

`scripts/contract_platform_corpus.py` supplies the lanes the Gate's own list
names and this measurement can reach without a toolchain: ELF, PE, Mach-O
(hand-built snapshots carrying the real `.dynsym` / export-directory /
export-trie shapes `export_surface.observed_exports_by_platform` reads, so
the same provider code a real binary exercises runs on every host), plus
stripped (exports with no header provenance at all), versioned (ELF symbol
versions), and C (`extern "C"`, the name-based identity tier rather than the
mangled-primary one). It is a separate corpus rather than more FP-rate cases
deliberately: that corpus is a gate with its own 0/0 FP/FN baselines, and a
Mach-O export-table shape has nothing to say about false-positive rate.

Each case is tagged with its lane, and the measurement reports unresolved
rate **by lane** alongside the existing by-domain and by-provider-state
axes. That is what makes the number acceptable-or-not rather than merely
reported: a domain unresolved exactly on the lanes carrying no evidence for
it is working as designed, and one unresolved on a lane that *does* carry
evidence is a defect the aggregate hides. Result — the gate stays at
0/0/0/0, and `exports` goes from 100% unresolved to resolving on every lane
with an export table (76.8% aggregate, entirely attributable to the
evidence-free FP-rate corpus), with 2 deltas and 2 proven exclusions where
it previously concluded nothing.

One case earns its place specifically: a public-header type whose layout
changes but which no export reaches. `public` calls it `IN_CONTRACT` (the
header committed to it); `exports` proves it out (nothing linkable reaches
it). Without a case where the two domains are *supposed* to disagree, the
`exports` domain could pass every baseline while concluding nothing at all
— the same vacuity the `public` domain already has a guard against, now
given to `exports` too.

**Updated (2026-08-08): "zero unexplained public-break losses" is not
literally zero for `public`, and the root cause of the three named losses
was found to be deeper than a spelling mismatch.**
`scripts/measure_contract_shadow.py`'s own gate is
`UNRESOLVED_LOSS_BASELINE = {"public": 3, "exports": 20, "all": 0}`, with
three specific, individually pinned cases in
`UNRESOLVED_LOSS_KNOWN_PUBLIC_CASES` (`ambiguous_namespaced_leaf`,
`public_std_string_typedef_alias_layout_changed`,
`public_stdlib_type_used_directly_layout_changed`) — the baseline this
section's own "Gate" line calls zero is a *tracked, named* three, not an
oversight, but this plan text never previously said so explicitly. A
status-review follow-up asked for a fix; investigating it found the
tracked-but-unexplained losses are genuinely two different problems, not
one:

The two `..._layout_changed` cases (a `std::vector<int, ...>`/`std::string`
taken directly by a public function) were suspected to be the same
bare-vs-qualified spelling gap `AGENTS.md`'s "Type reachability" section
documents at length for `type_reachability.py` — but empirically running
`compute_public_surface()` against the exact corpus fixtures (a synthetic
snapshot built the same way `scripts/check_fp_rate.py` does, RecordType
named `"vector<int, std::allocator<int> >"`) showed `public_types` comes
back **empty**, not merely missing the right spelling. The actual cause is
one level upstream: `surface.py`'s `_type_identifiers()` tokenizes a type
spelling with `_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_:]*")`, which
splits `"vector<int, std::allocator<int> >"` into the three disconnected
identifier tokens `{"vector", "allocator", "std::allocator"}` and discards
the template-argument structure entirely — while `record_by_name` (built by
`_index_surface_types`) indexes records by their *exact* `RecordType.name`
string, template arguments included. A signature seed can therefore never
exact-match a template-instantiated record's own key; this is not specific
to `std::` types, it reaches any public function taking *any* template
instantiation by value, and it predates this plan (`_type_identifiers` is
older than the contract-relevance feature). `type_reachability.py`'s own
`directly_referenced_stdlib_types()` computes the right answer today
(confirmed empirically: it correctly returns
`{"std::vector<int, std::allocator<int> >"}` for the same fixture) but nothing
threads it, or an equivalent template-preserving candidate, into
`surface.py`'s seed/closure computation or into `contract_evaluation.py`'s
`_type_candidates`/`_confirmed_type_matches`.

The third case, `ambiguous_namespaced_leaf`, is unrelated to the above: it is
exactly what `_confirmed_type_matches`'s own docstring already says (§ "An
'indecisive ambiguity' shortcut was tried here and reverted") — proving a
bare-tail collision (`ns1::Cache`/`ns2::Cache` both spelled `Cache`) is
resolvable one way or the other needs **per-identity reachability**
(distinguishing "reached via an exact qualified match" from "reached only
via the ambiguous bare-tail fallback") that `PublicSurface`'s closure does
not record at all today — a change to the shared closure's data model
every other `public`-domain consumer depends on, not a local fix.

**Sizing, not attempted here.** The two `..._layout_changed` cases are a
contained fix: thread a template-argument-preserving candidate (either
`type_reachability.directly_referenced_stdlib_types()` generalized past
`std::`-only, or a new seed derived the same way) from
`contract_pipeline.build_contract_stage()` (which already holds both
`AbiSnapshot`s) through `ContractEvaluationStage` into
`evaluate_change_contract_relevance`/`evaluate_snapshot_pair_contract_relevance`,
consulted in `_confirmed_type_matches`/`_type_candidates` the way
`diff_types._is_abi_surface_type` already consults a `directly_referenced`
set for its own, narrower purpose (emission gating, not contract
relevance). `ambiguous_namespaced_leaf` is the larger half: it needs new
per-record provenance in `PublicSurface`/`_walk_type_closure` (which record
records depend on many `public`-domain consumers beyond contract
evaluation — `classify_change_surface`, `_confirmed_type_matches`, and
every other detector than reads `public_types`/`ambiguous_type_names`), so
it needs its own scoped, independently-verified design and a full
FP-rate/tier-accuracy/mutation-score re-verification, not a same-round
extension of the smaller fix. Neither is implemented as part of this
update; this note exists so a future round starts from the actual root
cause instead of re-deriving it.

**Bounded honestly.** Two lanes the Gate lists are named in
`UNCOVERED_LANES` with their reason and reported in the output rather than
silently omitted: `package` (`compare` rejects `--contract-evaluation` for
directory/package operands, so there is no contract decision on a package
pair to measure) and `real_binaries` (needs a compiler; covered by the
integration lanes, `tests/test_scan_compare_parity.py` and
`tests/test_abi_examples.py`, not by this always-on measurement).

### Phase 7 — default flip

After release notes and a migration window, set the three independent defaults
to `public`, `strict_abi`, and `not_checkable`. Keep `contract=all` and
`--no-scope-public-headers` as the exact forensic rollback. Do not make a
`public_contract` enum/preset permanent.

**Updated (2026-08-03): the coverage-exit slice landed; the default flip and
the authoritative evaluator have not.** The maintainer directed a full flip
and stated explicitly that **no migration window was run** — recorded here
because this section's own precondition ("after release notes and a migration
window") is therefore not satisfied, and that was a deliberate decision rather
than an oversight. Release notes ship with the change instead of ahead of it.

What is done: `contract_coverage_exit.py` turns the ledger's long-computed
`0`/`1` into a real exit code, folded with `max` in exactly one place per
command (`cli._exit_with_severity_or_verdict` for `compare`,
`cli_scan_baseline` for `scan --against`, §6.4). The orthogonality claims are
now executable rather than stated: a coverage failure raises a clean `0` to
`1` and provably cannot lower a gate's `2`/`4`, and a run without
`--contract-evaluation` has no selected domain and so is bit-for-bit
unaffected. `contract.unresolved` gained its first engine consumer and
therefore left `pack_application.UNAPPLIED_PACK_FIELDS`; `warn` zeroes the
floor and changes nothing else, with the failures still listed and still
unsuppressible. `reporter.py` now emits the *applied* number rather than a
hypothetical one — a field named "exit contribution" that disagreed with the
actual exit status would be a trap once the number bites.

**Updated (2026-08-04): the contract decision is now authoritative.** The
reorder landed: `contract_pipeline.py` holds the stage, `checker.compare`
builds it immediately after post-processing (canonical identity, dedup and
the explicit consumer/manifest scope are settled by then, which is the input
D9's order calls for), and every path that computes or recomputes a verdict
goes through `_compute_verdict_for(..., stage)` — which classifies first and
then scores the `EVALUATED` findings alone. "Before the verdict" turned out
to be several points rather than one, since `--surface-metrics` and
`--pattern-verdicts` append findings and recompute, so classification is
idempotent per finding and called at each of them rather than assumed to
have happened once.

The gate follows the same predicate (`contract_gating.is_evaluated`, shared
by `severity.compute_exit_code`/`compute_gate_decision`), and D1's canonical
per-finding shape is emitted: `compatibility_evaluation_status`,
`compatibility_decision` (JSON `null` when policy did not run) and
`gate_contribution` (the number the run's own gate folded, not a
re-derivation). Two display consequences were part of the same change rather
than deferred, because leaving them would have made reports
self-contradictory: the four compatibility buckets (`DiffResult.breaking` and
siblings, and therefore every summary built from them) are over the evaluated
findings only, and markdown gained a "Not Evaluated (Contract)" section plus
a headline count — a `NO_CHANGE` verdict printed above a "Breaking Changes"
section listing the finding it deliberately did not score is worse than
either half alone.

Verified against the gates this reorder is most likely to move: the full unit
suite (22844 passed) and golden tests, the FP-rate gate (0/0, no delta), and
the per-tier accuracy gate (top-tier correct, under-call monotonic). Twelve
existing tests asserted the pre-Phase-7 advisory contract and were updated to
the normative one rather than made to pass — the substantive ones being that
`exports` on a header-only breaking pair now exits `1` (`UNKNOWN_UNRESOLVED`,
`NOT_CHECKABLE`) instead of `4`, and that selecting a domain *does* change a
verdict. The orthogonality claim ("coverage never lowers a real break") moved
to `fold_coverage_exit` itself: once relevance is authoritative, a domain
short of the evidence to close is by construction also short of what it takes
to resolve that domain's findings, so the two conditions no longer co-occur
end to end for an entity finding, and the fold is where the claim actually
lives.

What remains, in dependency order: flipping `--contract-evaluation` on by
default (this reorder is its precondition, not a substitute for the migration
corpus §10.3 calls for).

**Updated (2026-08-08): the release/package half of this paragraph is
done; `aggregate`'s own item was stale even before this update, and the
MCP caveat narrows to a specific known gap rather than a blanket one.**
The directory/package `compare` fan-out previously hard-rejected
`--contract-evaluation`/`--contract` outright — it now threads both
straight into each library pair's own `service.run_compare()` call, the
identical Tier-2 chokepoint a single-pair `compare` uses, so a library
compared through the fan-out gets the same ADR-049 decision it would from
comparing it individually; ADR-049 Phase 7's orthogonal contract-coverage
floor is `max()`-aggregated across every library into the release's own
exit code the same way. `aggregate` folding the coverage ledger into its
own exit axis (`AggregateResult.contract_coverage_exit`, schema 1.3) was
already implemented before this note — this paragraph's own "and
`aggregate` folding the ledger into its coverage axis" trailer was
already inaccurate as a *remaining* item, just never corrected; a
separate, later addition (`finding_matrix` entries carrying each
profile's own `contract_relevance`/`compatibility_decision`/
`gate_contribution`, schema 1.4) is a reporting-detail improvement over
an already-working coverage fold, not the fold itself. The MCP tool does
call `checker.compare(..., contract_evaluation=..., contract_mode=...)`
directly (`mcp_server.py`), so its actual verdict/gate computation already
goes through the same authoritative pipeline a single-pair CLI `compare`
does — the still-real gap is narrower than "different semantics": the
tool has no `--pack` equivalent, so `contract.unresolved=warn` and any
other pack-sourced field are unreachable from it, and
`mcp_compare_receipt.resolve_tool_config` resolves a config only for the
persisted receipt, never applies one to the run (unlike the CLI's
`resolve_and_apply`) — investigated this session and deliberately not
attempted, since a rushed fix risks the same "decorative `--pack`" bug
class CodeRabbit already caught twice on the CLI side.

SARIF's invocation exit code is **no longer** on that list: it publishes its
own machine-readable exit contract, so leaving it unfolded meant an artifact
saying `exitCode: 0` beside a process that exited 1, which a consumer reading
the artifact would accept. It is folded with the same `max`.
`executionSuccessful` deliberately stays `true` — per the SARIF spec it
reports whether the tool ran to completion, not whether it found blocking
issues, and the spec's own example pairs `exitCode: 1` with
`executionSuccessful: true`.

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
