# Public-contract default: contract-aware compatibility gates

**Status:** Proposed
**Scope:** `scan`, `compare`, service API, reports, and compatibility migration
**Related:** ADR-010 (severity policy), ADR-024 (public-header surface), ADR-028/033 (evidence and coverage), ADR-037/043 (CLI contract), PR #494 / case97

## 1. Problem

The current default mixes two independent questions:

1. **Contract relevance:** is the changed entity part of the ABI/API promised to consumers?
2. **Severity:** if it is relevant, is the change compatible, risky, an API break, or an ABI break?

`strict_abi`, `sdk_vendor`, and `plugin_abi` answer only question 2. ADR-024's public-header filter partially answers question 1, but the CLI orchestration can bypass it: `fold_l0_hard_removals()` and `scan --against` perform an unscoped symbols-only comparison and fold every `func_removed_elf_only` back as `BREAKING`.

That protects case97, where a macro-conditioned declaration disappears from the parsed header AST, but it also makes an undocumented private export a release blocker. Libraries such as pvxs intentionally export implementation symbols; removing one must not fail a public-contract gate unless some evidence says consumers were promised it.

The new default must therefore preserve artifact facts without equating every exported symbol with the public contract.

## 2. Proposed default

`public_contract` is a user-facing preset composed from two orthogonal settings:

```text
public_contract := contract = public
                   policy   = strict_abi
                   unresolved_contract = not_checkable
```

- `contract=public` decides which findings participate in the compatibility gate.
- `policy=strict_abi` classifies the severity of participating findings.
- `unresolved_contract=not_checkable` distinguishes a complete search with no proof from missing/failed evidence; the latter exits 1 rather than silently treating all exports as public or returning a false-green compatibility result.

This is **not** another `policy_kind_sets()` profile. A `ChangeKind` such as `func_removed_elf_only` can be blocking, audited as private, or unresolved depending on evidence for that particular symbol.

For forensic/all-export analysis, users can select:

```text
contract = exports
```

This preserves the current conservative behavior: every exported symbol is considered contract-relevant. The existing `--no-scope-public-headers` option remains as a compatibility alias for `contract=exports`; `--scope-public-headers` maps to `contract=public`.

## 3. Core model

Every finding receives a contract classification before severity is aggregated:

```text
ContractRelevance := PUBLIC | PRIVATE | UNKNOWN_UNPROVEN |
                     UNKNOWN_UNRESOLVED | NOT_APPLICABLE
```

| Value | Meaning | Default gate behavior |
|---|---|---|
| `PUBLIC` | Positive evidence ties the entity to the supported consumer contract. | Apply the selected severity policy; may fail. |
| `PRIVATE` | Positive evidence proves the entity is implementation-only, and no stronger public evidence contradicts it. | Do not gate; retain in the audit ledger. |
| `UNKNOWN_UNPROVEN` | The configured evidence search completed, but found neither public nor private proof for the entity. | Do not gate; retain in an unresolved ledger. Exit contribution is 0. |
| `UNKNOWN_UNRESOLVED` | Required evidence is missing, stale, failed, contradictory, or the entity cannot be identified reliably. | Analysis state is `NOT_CHECKABLE`; retain in the unresolved ledger and contribute exit 1 unless a proven break already contributes 2/4. |
| `NOT_APPLICABLE` | The finding is not entity-surface scoped, e.g. SONAME, `DT_NEEDED`, architecture, loader, security, or an always-public leak finding. | Keep in the normal gate and apply policy. |

`PRIVATE` and both unknown states are intentionally different. “Not found in a successfully parsed public contract” is absence of proof (`UNKNOWN_UNPROVEN`), not proof of privacy. Failure to parse or obtain the required contract is `UNKNOWN_UNRESOLVED`, not a greener form of the same result.

### 3.1 Contract assurance

The compatibility verdict and the completeness of contract resolution are separate outputs:

```text
contract_assurance := complete | partial | unavailable
```

- `complete`: every surface-scoped finding was classified `PUBLIC`, `PRIVATE`, or `UNKNOWN_UNPROVEN` after a complete evidence search.
- `partial`: at least one finding is `UNKNOWN_UNRESOLVED`, but enough evidence exists to classify other findings.
- `unavailable`: no usable public-contract evidence exists for the side required by the operation.

A complete result may be `NO_CHANGE`/exit 0 while disclosing `UNKNOWN_UNPROVEN` exports: the tool searched the declared contract successfully and found no proof that they were promised. Reports must say **“no proven public-contract break; N unproven exports retained for audit”**, not silently call them private.

`partial`/`unavailable` sets an orthogonal top-level `analysis_status=NOT_CHECKABLE` and contributes exit 1. The compatibility `verdict` remains the verdict over proven findings, avoiding an incompatible expansion of the existing `Verdict` enum. Proven `API_BREAK`/`BREAKING` exits 2/4 still win. Uncertainty never uses 2 or 4 because it is not proof of a break. `unresolved_contract=warn` may be offered as an explicit permissive override; `unresolved_contract=exports` is an explicit fail-conservative migration mode that promotes unknown exports to `PUBLIC` and reproduces all-export gating.

### 3.2 Evidence-search completeness

`UNKNOWN_UNPROVEN` is legal only when every provider required by the resolved
contract plan records a terminal, complete search. The evaluator must consume a
provider ledger rather than infer completeness from an empty result:

```text
EvidenceSearchRecord := id + provider + side + requested_scope + searched_scope
                        + status + completeness + reason_code + input_identity

status       := available | unavailable | failed | unsupported | stale
completeness := complete | partial | not_started
```

Examples:

- a parser that successfully searched all requested public headers and found no
  matching declaration records `available/complete`; the entity may be
  `UNKNOWN_UNPROVEN`;
- an unavailable adapter, unsupported object format, timeout, parse failure, or
  stale input records a non-available status and makes affected entities
  `UNKNOWN_UNRESOLVED`;
- partial package/directory traversal and a provider that returned some facts
  before failing record `partial`, never `complete`;
- when several providers are required, one complete empty search does not hide
  another provider's failure; affected entities remain unresolved;
- contradictory identity joins record both candidates and a stable ambiguity
  reason instead of selecting one by iteration order.

The resolved plan defines which providers are **required** versus optional.
Failure of an optional enrichment provider does not make a result unresolved if
the required domain was searched completely; the report still discloses the
failure. Required-provider selection, searched scope, and input digests are part
of the persisted evidence so snapshot replay can reproduce the completeness
decision.

## 4. Evidence and precedence

Evidence is evaluated per entity. Stronger positive public evidence wins over private evidence. Contradiction yields `PUBLIC` plus a diagnostic when the public evidence is authoritative; otherwise it yields `UNKNOWN_UNRESOLVED`.

### 4.1 Positive public evidence

From strongest to weaker:

1. **Explicit consumer/contract input**
   - `--required-symbol`, required-symbol file;
   - an exact entry in an explicitly supplied ABI/export manifest;
   - a `--used-by` consumer that imports or resolves the symbol;
   - package contract metadata such as an exact Debian symbols entry.
2. **Old-side public declaration** for removals and compatibility changes
   - declaration physically originating in an explicitly supplied public header;
   - declaration found in a public header's guarded/token declaration index even when the active header AST omitted it because of a consumer-controlled macro;
   - an exported function/variable reachable through the public declaration graph.
3. **Public type closure**
   - records, enums, typedefs, fields, bases, template arguments, vtables, and ABI artifacts transitively reachable from a public symbol/type;
   - private-header types leaked through public signatures remain public-contract findings; leak diagnostics are never filtered.
4. **Deliberate exact export commitment**
   - exact names in a version script/export map/`.def` file when that file is supplied or discovered as the project's contract input;
   - wildcard exports alone are not enough: they prove linkage policy, not intentional commitment to every matching name.
5. **Consumer-proven runtime evidence**
   - import table, relocation, symbol-version requirement, or recorded `dlsym`/plugin entrypoint evidence tied to a concrete consumer.

### 4.2 Positive private evidence

A finding may be `PRIVATE` only when all applicable identities are resolved and at least one positive private proof exists:

- declaration origin is `PRIVATE_HEADER` or `SYSTEM_HEADER`, is not reachable from the public closure, and has no public/consumer/manifest evidence;
- symbol is marked local/private by an authoritative project contract manifest;
- a framework-specific surface oracle proves the native entity is implementation detail (for example, the existing CPython extension surface rule);
- an exact public allowlist/POST manifest excludes a concrete export and is explicitly authoritative for that library.

Naming conventions (`_internal`, `detail::`), missing documentation, or absence from the active header AST are hints only and cannot produce `PRIVATE` without another authoritative fact.

### 4.3 Unknown causes

`UNKNOWN_UNPROVEN` means the declared evidence domain was searched successfully but did not commit the entity. Examples: an export absent from a successfully parsed public-header/manifest surface, or matched only by a wildcard export rule.

`UNKNOWN_UNRESOLVED` means the search itself was not authoritative. Examples:

- no headers, manifest, or consumer evidence was supplied/discovered;
- public headers were requested but the parser/backend failed;
- mangled/demangled identity cannot be joined reliably;
- the old snapshot lacks old-side contract evidence required for a removal;
- contradictory public/private declarations cannot be resolved;
- a source path needed for an explicitly required enrichment probe is missing or stale;
- one side resolves but the side required for the operation does not.

Each unknown row must include a stable reason code, resolution class, evidence examined, side, and symbol/type identity.

## 5. Side-aware rules

Contract membership is temporal. The relevant side depends on the operation:

| Finding shape | Authority rule |
|---|---|
| Removal | Old-side evidence is authoritative. A symbol public in v1 remains a contract removal even if absent from v2 headers. |
| Addition | New-side evidence is authoritative. A new public entity is a public compatible/addition finding. |
| Modification | The old side is authoritative for an existing obligation. An old-public entity remains gated even if new evidence says private/unknown. A `PRIVATE`/`UNKNOWN_UNPROVEN`→`PUBLIC` transition is modeled as a new public commitment/addition rather than retroactively making its prior modification breaking. If old-side authority is `UNKNOWN_UNRESOLVED`, retain the modification as unresolved/`NOT_CHECKABLE`; a separate new-side addition may be reported, but cannot clear the old-side coverage failure. |
| Visibility/version removal | Old-side public/consumer evidence gates. |
| Type/layout change | For an existing type, old-side reachability is authoritative. New-side public reachability creates a new commitment/addition; it does not retroactively turn an old-private layout modification into a break. |
| Private on both sides | `PRIVATE` when both applicable identities are confidently private. |
| Private on one side, unknown on the other | Use the operation's authoritative side. If that side is incomplete, `UNKNOWN_UNRESOLVED`; do not let evidence from the non-authoritative side manufacture confidence. |

Public evidence always wins: an entity declared in a private header but imported by a real consumer is `PUBLIC` for that consumer-scoped check.

## 6. Behavior by command

### 6.1 `scan ARTIFACT` without `--against`

This is a one-build audit, not a compatibility comparison. It cannot report that an ABI was removed because there is no old side.

Under `public_contract` it should:

- build and report the candidate's public-contract evidence index;
- run existing pattern, preprocessor, cross-source, leak, accidental-export, and quality checks;
- apply contract relevance only to findings whose meaning is surface-dependent;
- keep loader/security/build-integrity findings in the normal gate (`NOT_APPLICABLE`);
- report exported-but-uncommitted symbols as audit findings, not ABI breaks;
- report unresolved contract coverage explicitly;
- never synthesize `func_removed*` from a single artifact.

Exit behavior extends the current scan contract: 0 for advisory-only or completely searched unproven exports, 1 for `NOT_CHECKABLE` contract evidence, 2 for policy-promoted source/API findings, 5 for budget overflow, and 64 for usage errors. Proven ABI breaks from a baseline comparison remain exit 4. An explicit permissive `unresolved_contract=warn` can downgrade the coverage-only exit 1 to a warning.

#### Exit-code composition

The feature does not create a new exit-code scheme. It adds a contract-coverage
contribution to the existing command result:

- invalid invocation/configuration is rejected before analysis with exit 64;
- a scan budget overflow short-circuits with the existing exit 5; no completed
  compatibility result is claimed;
- for a completed single-target analysis, semantic precedence is
  `BREAKING/4 > API_BREAK/2 > NOT_CHECKABLE-or-severity-low/1 > pass/0`;
- under the severity-aware scheme, the existing category mapping remains the
  source of 1/2/4 and the contract-coverage contribution is combined by the
  same numeric worst-of rule; report fields distinguish
  `contract_coverage` from `addition`/`quality_issues`, which may also produce
  exit 1;
- release comparison preserves both existing schemes: in legacy mode an
  operational `ERROR` exits 4 and a nonzero verdict exit 2/4 wins before the
  removed-library check, so exit 8 is used only when the legacy verdict would
  pass; in severity-aware mode `--fail-on-removed-library` exit 8 takes
  precedence over the aggregated severity 0/1/2/4 (and over the operational
  floor), while an operational `ERROR` without a removed-library exit still
  floors the result at 4;
- output serialization/write failures are command failures, not contract
  classifications; they use the command's existing operational error path and
  must not emit a successful result with a substituted semantic exit.

Thus 5, 8, and 64 are command-specific short-circuit/aggregation states, not
severity levels. Tests must cover each legal combination rather than sorting all
integers globally.

### 6.2 `scan ARTIFACT --against BASELINE`

This must use the same contract evaluator and comparison core as raw `compare`; `scan` may add source intelligence but must not implement a second surface policy.

Pipeline:

1. Resolve old and new snapshots with side-specific evidence.
2. Run all detectors, including a symbols-only L0 pass when richer extraction can omit exports.
3. Deduplicate L0 and rich findings by stable entity/change identity.
4. Annotate every remaining finding with contract relevance.
5. Gate only `PUBLIC`/`NOT_APPLICABLE`; ledger `PRIVATE`; disclose both unknown classes and set `NOT_CHECKABLE` for unresolved evidence.
6. Add scan-only source/cross-check findings and aggregate the final verdict/exit.

For the comparison-derived portion, `scan ARTIFACT --against BASELINE` and
`compare BASELINE ARTIFACT` must produce identical normalized finding identity,
contract relevance, reason, evidence side, gated bit, and compatibility
contribution when given equivalent inputs and options. Scan-only findings may be
additional, but cannot rewrite those shared decisions.

The current unconditional fold of every L0 `func_removed_elf_only` must be removed. The L0 pass still supplies an authoritative **removal fact**, but contract relevance is decided separately.

#### By effective scan depth

| Depth/mode | Expected behavior |
|---|---|
| `binary` / symbols-only | Explicit manifests, exact export contracts, package metadata, and consumer imports can prove public membership. A bare export removal is `UNKNOWN_UNPROVEN` only if the configured contract domain was completely searched; with no contract source it is `UNKNOWN_UNRESOLVED`/exit 1. `contract=exports` gates it. |
| DWARF/debug-aware | Declaration location and type reachability may prove public/private. Missing or ambiguous provenance stays unknown. |
| Header/source | Public-header origin, guarded declaration index, preprocessor/build context, and source graph enrich classification. This is the preferred `public_contract` mode. |
| Full/graph | Same gate semantics; more evidence may move an unknown to `PUBLIC` or `PRIVATE`, never change policy meaning. |
| Budget overflow | Exit 5; never silently fall back to a shallower all-export or public-only conclusion. |

### 6.3 `compare OLD NEW` on binaries

- Resolve side-specific evidence exactly as today.
- Run the L0 export delta even when headers are present, but send its findings through contract classification.
- If headers prove an old removed symbol public, gate it.
- If provenance proves it private, audit it.
- If only the export fact exists, mark it unresolved under `public_contract`.
- `contract=exports` gates all exported removals and reproduces forensic behavior.

### 6.4 `compare OLD.json NEW.json` on snapshots

Comparison must be reproducible from persisted evidence.

- Use public/private provenance, declaration indexes, manifests, and evidence coverage embedded in snapshots.
- Do not silently read current files from `source_path` to change the verdict.
- If an optional live re-probe is retained, its result is enrichment only, must
  pass strong identity checks (prefer digest over mtime/size), and must be
  disclosed. Its failure does not alter a classification reproduced from
  complete persisted evidence. Only when the resolved plan marks the re-probe
  required because persisted evidence is incomplete does failure leave affected
  evidence `UNKNOWN_UNRESOLVED`.
- Older snapshots without contract metadata remain readable; their export-only entities become `UNKNOWN_UNRESOLVED` in `public_contract`, or public in `contract=exports`.
- A write→read→compare round trip must preserve the evidence-search ledger,
  provider requirements, declaration/manifest identities, assurance, and every
  per-finding contract decision.
- Schema readers branch on an explicit contract-evidence schema version.
  Unknown future versions fail closed as unsupported evidence; mixed old/new
  snapshots expose side-specific coverage rather than silently dropping the
  newer block.

### 6.5 Mixed snapshot/binary inputs

Use persisted evidence for the snapshot side and freshly resolved evidence for the binary side. Side asymmetry must be shown in coverage. For a removal, lack of old-side evidence cannot be repaired merely by new-side headers.

### 6.6 Directory/package/release compare

Apply contract resolution per library before release aggregation. Aggregate three independent axes:

- worst gated compatibility verdict;
- contract coverage (`complete`/`partial`/`unavailable` per required library);
- operational availability.

A removed whole library remains a release-level contract event under the existing `--fail-on-removed-library` rules; it must not be hidden because entity-level evidence is unavailable.

### 6.7 Consumer- and manifest-scoped compare

Explicit scope is stronger than inferred public headers:

- `--used-by`: imported/required entities are `PUBLIC`; unrelated findings are out of that consumer's gate but remain auditable.
- `--required-symbol`: named entrypoints are `PUBLIC`, including missing-contract synthetic findings.
- `--post-manifest`: the committed set is authoritative for concrete exports; type, loader, and leak findings remain conservative as today.
- Do not apply `public_contract` a second time in a way that can remove a finding already proven relevant by explicit scope.

## 7. Required scenario matrix

| Scenario | `public_contract` | `contract=exports` | Why |
|---|---|---|---|
| Public header function removed | Gate `BREAKING` | Gate `BREAKING` | Old public declaration. |
| Macro-gated public declaration removed (case97) | Gate `BREAKING` | Gate `BREAKING` | Old guarded public declaration index recovers evidence even if active AST omits it. |
| Private-header exported helper removed (pvxs shape) | Audit as `PRIVATE`; exit unaffected | Gate `BREAKING` | Positive private provenance, no public/consumer evidence. |
| Export absent from a successfully searched public contract | `UNKNOWN_UNPROVEN`; audit, exit 0 | Gate `BREAKING` | Complete search found no promise, but absence is still not proof of privacy. |
| Export removed with no usable contract source/provenance | `UNKNOWN_UNRESOLVED`; `NOT_CHECKABLE`/exit 1 | Gate `BREAKING` | The contract could not be checked. |
| Undocumented export imported by `--used-by` consumer | Gate `BREAKING` | Gate `BREAKING` | Consumer proof wins. |
| Exact version-script symbol removed | Gate `BREAKING` | Gate `BREAKING` | Deliberate exact contract entry. |
| Symbol matched only by `global: *` removed | `UNKNOWN_UNPROVEN` unless other evidence | Gate `BREAKING` | Wildcard does not prove intentional commitment. |
| Private type layout changes, unreachable from public API | Audit as `PRIVATE` | Gate per strict policy | Proven implementation detail. |
| Private-header type appears in public signature | Gate leak/layout finding | Gate | Public reachability wins; anti-hiding. |
| Public type private field changes layout | Gate `BREAKING` | Gate | Layout is consumer-observable. |
| Public symbol becomes private/hidden | Gate as removal/visibility break | Gate | Old side defines the promise. |
| Private symbol becomes public | Public compatible/addition finding | Same | New side defines the added promise. |
| No headers/evidence on either side | `UNKNOWN_UNRESOLVED`, assurance `unavailable`, `NOT_CHECKABLE`/exit 1 | Gate all exports | No silent fallback or false-green compatibility claim. |
| Header backend failure | `UNKNOWN_UNRESOLVED`, assurance `partial/unavailable`, exit 1 unless a proven 2/4 exists | Gate all exports | Failure is disclosed, not converted to all-export public evidence. |
| SONAME/NEEDED/architecture break | Gate | Gate | Not entity-surface scoped. |
| Explicit required symbol missing | Gate | Gate | Explicit contract always wins. |
| Python extension internal C++ churn with Python surface oracle | Audit as private | Gate in forensic mode | Existing framework-specific contract proof. |

## 8. Reporting and schema

Add an additive `contract_scope` metadata block and a canonical sibling
`unresolved_contract_changes` ledger to compare JSON/SARIF/JUnit and the scan
report:

```json
{
  "contract_scope": {
    "mode": "public",
    "preset": "public_contract",
    "mode_source": "preset",
    "policy": "strict_abi",
    "policy_source": "preset",
    "assurance": "partial",
    "analysis_status": "NOT_CHECKABLE",
    "unresolved_behavior": "not_checkable",
    "unresolved_behavior_source": "preset",
    "counts": {"public": 3, "private": 8, "unknown_unproven": 1,
               "unknown_unresolved": 1, "not_applicable": 2},
    "evidence": [
      {"id": "old-public-headers", "side": "old",
       "provider": "public_header", "status": "available",
       "completeness": "complete", "requested_scope": ["include/"],
       "searched_scope": ["include/"], "reason_code": "search-complete",
       "input_identity": {"uri": "include/", "sha256": "<digest>"}},
      {"id": "old-guarded-index", "side": "old",
       "provider": "guarded_declaration_index", "status": "available",
       "completeness": "complete", "requested_scope": ["include/"],
       "searched_scope": ["include/"], "reason_code": "search-complete",
       "input_identity": {"uri": "snapshot://old/guarded-index",
                          "sha256": "<digest>"}},
      {"id": "old-contract-manifest", "side": "old",
       "provider": "contract_manifest", "status": "failed",
       "completeness": "partial", "requested_scope": ["abi-contract.json"],
       "searched_scope": [], "reason_code": "manifest-parse-failed",
       "input_identity": {"uri": "abi-contract.json", "sha256": "<digest>"}}
    ]
  },
  "unresolved_contract_changes": [
    {"kind": "func_removed_elf_only", "symbol": "helper", "side": "old",
     "contract_relevance": "UNKNOWN_UNPROVEN",
     "contract_reason": "unknown-unproven-export-only",
     "resolution_class": "complete-no-commitment",
     "contract_evidence": ["old-public-headers", "old-guarded-index"],
     "gated": false},
    {"kind": "func_removed_elf_only", "symbol": "legacy_helper",
     "side": "old", "contract_relevance": "UNKNOWN_UNRESOLVED",
     "contract_reason": "required-manifest-parse-failed",
     "resolution_class": "required-evidence-failed",
     "contract_evidence": ["old-contract-manifest"], "gated": false}
  ]
}
```

Per-finding machine fields:

- `contract_relevance`;
- `contract_reason` (stable code);
- `contract_evidence` (ordered references to evidence records preserving source,
  side, input identity, and confidence/completeness);
- `gated` boolean.

Compatibility/migration:

- Existing `surface_scope` remains during transition and can be derived from the new block for header-only consumers.
- Existing `out_of_surface_changes` holds proven-private findings.
- `unresolved_contract_changes` is the single canonical machine ledger for both
  unknown classes; `contract_scope.counts` summarizes it. Do not introduce a
  second `contract_scope.unresolved` alias, and never mix unknowns into the
  private ledger.
- `--show-filtered` shows both sections, clearly labeled “proven private” and “unresolved”.
- Text output always prints an assurance warning when partial/unavailable, even when the unresolved list is truncated.
- Report ordering and reason codes are deterministic.
- SARIF carries contract relevance/reason/evidence side as result properties;
  unresolved coverage also emits a deterministic tool-level notification.
- JUnit represents `NOT_CHECKABLE` as a skipped/error-style coverage case
  according to the existing JUnit contract, never as a passed compatibility
  testcase; the exact mapping is schema-tested.
- `aggregate` consumes the report's gate/coverage contribution and preserves
  the orthogonality defined by ADR-042: unresolved contract evidence contributes
  coverage exit 1, while compatibility remains the proven verdict.
- Every renderer is tested from the same canonical result object; display
  filtering and truncation cannot alter counts, gate state, or exit code.

## 9. Implementation design

### 9.1 Shared contract evaluator

Introduce a leaf module such as `abicheck/contract_surface.py` containing:

- `ContractMode`, `ContractRelevance`, `ContractAssurance`, `AnalysisStatus`;
- side-specific `ContractEvidenceIndex`;
- `classify_change_contract(change, old_index, new_index, explicit_scope)`;
- stable evidence/reason records;
- aggregation helpers.

It should consume facts from `surface.py`, manifests, package metadata, and consumer scoping without importing CLI modules.

### 9.2 Pipeline order

Required order:

```text
resolve evidence
→ detect rich + L0 facts
→ normalize/deduplicate facts
→ explicit consumer/manifest scope
→ contract relevance classification
→ private/unresolved ledgers
→ policy/severity classification
→ verdict and exit aggregation
→ render
```

Severity must not decide relevance, and relevance must not rewrite `ChangeKind`.

### 9.3 Replace L0 hard-removal fold

Refactor `fold_l0_hard_removals()` into an evidence-preserving collector, for example `collect_l0_export_delta()`:

- return normalized removal facts, not pre-gated breaking changes;
- preserve source identity and evidence coverage;
- deduplicate against rich `func_removed`/versioned removals;
- pass every L0 fact through the shared contract evaluator;
- use the same helper from `cli_compare_helpers.py` and `cli_scan_baseline.py`;
- never call an unscoped compare and then inject its `breaking` bucket after surface filtering.

PR #494's invariant becomes: **a real L0 removal fact must not disappear**. It no longer implies that every L0 removal must block. Case97 remains blocking because the old public header/guarded declaration supplies contract evidence.

### 9.4 Persist enough evidence

Snapshots need an additive contract-evidence section containing:

- resolved public-header identities/digests;
- declaration provenance;
- a lightweight guarded declaration index for exported names omitted from the active AST;
- exact manifest/export-contract entries and their origin;
- public type-closure identities;
- evidence coverage/fallback reason codes.

Do not store only the final `PUBLIC/PRIVATE` label: consumers need facts to re-evaluate old snapshots under newer policy while preserving reproducibility.

### 9.5 CLI/config migration

Recommended configuration shape:

```yaml
contract:
  mode: public          # public | exports
  unresolved: not_checkable  # not_checkable | warn | exports
policy: strict_abi
```

The concrete CLI spelling is `--contract public|exports` plus
`--unresolved-contract not-checkable|warn|exports`; the config values use
`not_checkable` because existing YAML keys use underscores. `public_contract`
is the named composition/preset, not a third contract-mode enum value. Reports
always record the effective mode, unresolved behavior, severity policy, and the
source of each value. The machine-value enum is `built_in_default`, `config`,
`profile`, `preset`, `legacy_alias`, or `explicit_cli`; `preset` is used when
the `public_contract` composition supplied the value, matching the report
example above.

Precedence is: explicit new CLI option > explicit legacy scope flag > selected
preset/profile composition > config > built-in default. Supplying contradictory
explicit new/legacy options is a usage error (64), not last-option-wins. Config
parsing rejects unknown values and contradictory shapes. During migration,
`--scope-public-headers` resolves to `--contract public` and
`--no-scope-public-headers` to `--contract exports`; help and deprecation text
must describe the semantic change and the effective configuration is visible in
text/JSON output. No existing `--policy` or severity option changes meaning.

Migration stages:

1. Ship evaluator/reporting behind an internal rollout feature flag and compare
   old/new decisions in CI telemetry. The flag must not introduce or serialize a
   third `contract.mode`; effective mode remains `public` or `exports`.
2. Make `public_contract` available as a preset; retain current default.
3. Validate real-world false-negative/false-positive corpus, especially case97 and pvxs.
4. Flip default after release notes and one deprecation cycle.
5. Keep `contract=exports` and `--no-scope-public-headers` as permanent forensic escape hatches.

No existing `--policy` value changes meaning.

## 10. Test strategy

### 10.1 Unit tests

**Evidence index**

- exact public/private/system origins;
- old/new side authority;
- mangled/demangled aliases and symbol versions;
- guarded declaration recovery;
- exact versus wildcard version scripts;
- contradictory evidence precedence;
- ambiguous identity produces unknown, never private;
- public type reachability and leak guard.
- provider ledger outcomes: complete-empty, unavailable adapter, unsupported
  format, parse failure, timeout, stale input, partial traversal, one-of-many
  provider failure, optional-provider failure, and contradictory identity;
- for every outcome assert requested/searched scope, status, completeness,
  stable reason, assurance, relevance, and exit contribution.

**Classifier**

Cross-product of operation (`add/remove/modify/visibility/type`) × old relevance × new relevance × explicit consumer evidence. Assert classification, reason, gate bit, assurance, and stable serialization. Include the asymmetric modification case `old=UNKNOWN_UNRESOLVED, new=PUBLIC`: the existing modification remains unresolved/`NOT_CHECKABLE`, even if a separate public addition is emitted.

**L0 normalization**

- L0-only removal survives detection;
- duplicate rich/L0 removal becomes one finding;
- versioned symbols do not collapse incorrectly;
- stale/missing source path yields disclosed unknown/no enrichment;
- collector never assigns gate severity itself.
- rich+L0 reconciliation retains one logical finding with both provenance
  records; conservation assertions fail if either detector fact is dropped.

### 10.2 Integration/CLI tests

Run each with text and JSON, checking verdict, exit, counts, finding identity, ledger, assurance, and warning text:

1. public function removal;
2. case97 macro-gated public removal;
3. pvxs-style private exported removal;
4. export-only unknown removal;
5. the same unknown under `contract=exports`;
6. `--used-by` proving an undocumented export public;
7. `--required-symbol` proving it public;
8. exact manifest/version-script entry;
9. wildcard-only export script;
10. private type churn;
11. public-reachable private type/leak;
12. no headers;
13. old headers missing but new present, and reverse;
14. old-unresolved modification with new public evidence remains
    `NOT_CHECKABLE`, with any new commitment emitted separately;
15. parser fallback/mangling failure;
16. snapshot/snapshot, binary/binary, and mixed inputs;
17. source/full scan and symbols-only scan;
18. scan without baseline (no synthetic removals);
19. directory/package aggregation;
20. Python extension surface oracle;
21. loader/SONAME findings unaffected by contract mode.

For the comparison cases, run equivalent `compare` and `scan --against`
invocations and compare each shared finding field, not only the top-level
verdict. Add explicit combinations for contract coverage exit 1 with severity
1/2/4, scan budget 5, invalid config 64, and both release schemes: legacy
break/API-break plus removed library exits 4/2, legacy pass plus removed library
exits 8, severity-aware removed library wins with exit 8, and operational error
without a removed-library override floors at 4. Exercise text, JSON, SARIF,
JUnit, markdown/GitHub output, and aggregate ingestion from the same canonical
fixtures.

Add a table-driven CLI/config resolution suite that separately proves:

- explicit `--contract` overrides profile, config, and default while recording
  source `explicit_cli`; an equivalent legacy scope flag may coexist and still
  records `explicit_cli`, while an opposite legacy value is a contradiction and
  fails with exit 64;
- `--scope-public-headers` resolves to `public` and
  `--no-scope-public-headers` resolves to `exports`; when no explicit new option
  is present, each overrides profile, config, and default while recording source
  `legacy_alias`;
- profile overrides config/default and config overrides the built-in default,
  with the effective value and source serialized in every case;
- selecting the `public_contract` preset resolves mode `public` with source
  `preset`, and overrides config/default without being serialized as mode
  `public_contract`;
- contradictory explicit new/legacy options and contradictory legacy aliases
  fail with exit 64;
- equivalent values supplied through multiple layers remain deterministic and
  still report the highest-precedence source rather than whichever parser ran
  last.

### 10.3 Regression tests

- Rewrite `tests/test_pr494_scan_regressions.py` to assert both invariants:
  1. case97 L0 removal remains present and blocking due to old public evidence;
  2. deleting/mutating only new-side metadata does not change that result;
  3. removing old-side proof changes the outcome to the appropriate unknown,
     rather than continuing to block all L0 removals;
  4. with rich extraction disabled, L0 alone still blocks from old evidence;
  5. rich+L0 yields one finding carrying both provenance records;
  6. reversing the comparison produces an addition governed by new-side
     evidence;
  7. a proven-private L0 removal remains present only in the private ledger and
     does not block.
- Add a pvxs-derived minimal fixture with authoritative private provenance, not
  merely an internal-looking symbol name. Run the same bytes with private proof
  present, absent, contradicted by consumer evidence, and under
  `contract=exports`; assert exact relevance, reason, ledger, verdict, and exit.
- Update case182: either provide explicit public/consumer evidence if it is expected to stay blocking, or reclassify it as unresolved under `public_contract`; retain its current result under `contract=exports`.
- Preserve ADR-024 property and FP-rate suites.
- Add schema compatibility tests for reports lacking `contract_scope` and snapshots from pre-feature schema versions.
- Add snapshot round-trip and mixed-version tests: current/current,
  legacy/current in both directions, unknown future evidence schema, and
  snapshot/binary with asymmetric old/new coverage.

### 10.4 Property-based tests

- **No loss:** every detector finding appears exactly once in gated, private, unresolved, suppressed, or reconciled output.
- **Evidence monotonicity:** adding authoritative public evidence can move either unknown class or `PRIVATE→PUBLIC`, never `PUBLIC→PRIVATE`.
- **Private-proof monotonicity:** adding authoritative private evidence can move either unknown class to `PRIVATE`, never override public/consumer proof.
- **Forensic superset:** gated findings under `public_contract` are a subset of gated findings under `contract=exports`, excluding non-surface special rules shared by both.
- **Side symmetry:** reversing snapshots maps add/remove rules correctly without reusing the wrong side's evidence.
- **Idempotence/order independence:** evidence and finding order do not change classification/report order.
- **Deduplication:** rich+L0 reconciliation preserves one logical break.
- **Conservation:** reconciliation output references every input detector fact;
  no fact disappears merely because another detector found the same change.
- **Anti-hiding:** loader, leak, explicit required-symbol, and consumer-proven findings cannot become private.
- **Cross-command equivalence:** shared comparison findings from `compare` and
  `scan --against` are field-for-field equal for equivalent evidence.
- **Snapshot reproducibility:** serialize/deserialize and evidence-record order
  permutations do not change contract decisions.

### 10.5 Real-world and rollout gates

Before flipping the default:

- case97 and the full PR #494 regression lane must remain green;
- pvxs private-export removal must stop blocking and remain visible in audit output;
- run both modes over the real-world corpus and manually classify every decision delta;
- require zero unexplained public-break losses;
- measure unresolved rate separately from false positives;
- test Linux ELF, Mach-O, PE/COFF, stripped binaries, symbol versions, C and C++;
- include at least one executable fixture and expected oracle per supported
  object format: ELF visibility/versioning and stripped fallback, Mach-O export
  list, PE/COFF `.def`/ordinal or decorated-name identity; unsupported provider
  paths must assert unresolved coverage rather than being silently skipped;
- validate JSON schema, SARIF, JUnit, markdown, aggregate, and GitHub Action consumers;
- document rollback as `contract=exports` / `--no-scope-public-headers`.

## 11. Acceptance criteria

The concept is ready to become default only when all are true:

1. Contract relevance is represented independently from severity policy.
2. `scan --against` and `compare` use the same classifier and produce equivalent contract decisions for equivalent evidence.
3. No unscoped L0 result can bypass contract classification.
4. Case97 remains a named, blocking public break.
5. A proven-private exported-symbol removal is non-blocking but fully auditable.
6. An export with insufficient evidence is explicitly unresolved, never silently public or private.
7. Old-side evidence governs removals; public→private transitions cannot be hidden by new headers.
8. Explicit manifests/required symbols/real consumers override inferred privacy.
9. All report formats expose assurance, counts, reasons, and unresolved findings.
10. `contract=exports` provides deterministic backward-compatible forensic behavior.
11. Existing policy profiles retain their current severity semantics.
12. The test/property/real-world rollout gates above pass on every supported platform.

## 12. Non-goals

- Proving that no unknown consumer uses an accidental export.
- Treating documentation absence or naming conventions as authoritative privacy.
- Replacing severity profiles with contract modes.
- Silently hiding private or unresolved findings.
- Changing loader/security/package-level findings into header-scoped findings.
- Making a one-build `scan` claim cross-version ABI compatibility.
