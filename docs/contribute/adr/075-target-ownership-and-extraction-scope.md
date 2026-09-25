# ADR-075: Target Ownership, Extraction Scope, and Ownership in the Graph

**Date:** 2026-09-24
**Status:** Accepted — not yet implemented. Decides Phase 2 ("Persist") of
[`plans/target-ownership-and-extraction-scope.md`](../plans/target-ownership-and-extraction-scope.md)
and Phase 3 ("Ownership in the graph", invariant I5) of
[`plans/evidence-entity-model.md`](../plans/evidence-entity-model.md), which
depends on it. Adds one snapshot field (`AbiSnapshot.extraction_scope`,
snapshot schema v52), one comparability rule, one configuration-digest
field, and three derived graph relations. It changes no verdict, finding or
exit code for a run whose two sides were classified under the same rules,
and it drops no declaration: retention is the plan's Phase 3, not this ADR.

## Context

Three questions are answered today by one value, `ScopeOrigin`
(`public_header` / `system_header` / `unknown` / …), derived from header
paths: **whose** declaration it is (owner), **what** the target promises
about it (contract), and **why** it is in the snapshot at all (retention).
The target-ownership plan measured the consequences on SVS and oneDAL and
concluded that ownership must come from *files*, never names (its Decision
3), and landed a pure classifier for it in Phase 1:

- `model/ownership_rules.py` — the configured rules: target roots
  (`scope.public_header_dirs` plus any `-H` directory), named dependency
  roots (`scope.dependencies`), and the private narrowing
  (`scope.private_headers`, `scope.private_namespaces`).
- `extract/ownership.py` — `classify(site, rules)`: a declaration's file,
  qualified name and `artificial` bit → `(owner, contract, rule_id,
  diagnostics)`, with the plan's seven precedence rules.

Nothing applies it. A dump does not record which rules it was classified
under, no declaration carries its owner, and two snapshots classified under
different rules compare as if they had not been.

The evidence-entity-model plan's Problem #3 is the downstream consequence:
"no graph-level owner says which package, component or namespace promises
a declaration", so a release-wide header tree compared with one member
binary can produce public-vs-export findings for a declaration another
component provides (the MKL/oneDAL shape). The release fan-out already
answers the *provider* half of that for a multi-member release —
`model/release_surface.py`'s one contract, `compare/bundle_export_index.py`'s
`symbol → member` index, `policy/release_contract_reconciliation.py`'s fold
(`AGENTS.md` § "Release product model") — but a graph query cannot see any
of it, and the scalar path has no way to say that a declaration belongs to
someone else.

## Decision

### D1 — `AbiSnapshot.extraction_scope`: the rules a snapshot was classified under

One new optional snapshot field, `extraction_scope`, holding:

| Key | Meaning |
|---|---|
| `ownership_rules` | `target_roots`, `dependencies` (`name` + `header_roots`), `private_headers`, `private_namespaces`. Roots are normalized POSIX spellings **relative to the project root** when they lie under it, absolute otherwise; lists are sorted and de-duplicated. |
| `dependency_evidence` | What was kept of dependency declarations. `full` only (D6). |
| `prefilter` | A frontend prefilter (the plan's Phase 4). Always `null` in this ADR; the key is reserved so a reader recognises it. |
| `fingerprint` | `sha256:` over the canonical JSON of the three keys above. The one identity every comparison and digest reads. |
| `entity_ownership` | The per-entity decisions of D2, interned. |
| `diagnostics` | The classifier's rule-7 diagnostics (sorted, unique). |

**Who writes it.** Every snapshot *extracted in this run* whose
declarations come from a header parse (`from_headers`) — through the one
exit of `workflows.input_resolution.resolve_input`, the same choke point
`record_achieved_header_exclusions` already uses, so no resolution branch
can forget it. A snapshot **loaded** from storage is never restamped: it
already carries the rules it was built under, and overwriting them with this
run's request would forge its provenance (the same
`extracted_now=False` rule the header-exclusion record applies). A binary-
or debug-only snapshot has no header declarations to classify and records
nothing.

**Absent is unknown, never `full`.** A snapshot without the field — every
snapshot written before v52 — loads with `extraction_scope = None`, which
every reader treats as *unrecorded*. It is not defaulted to "no rules,
`full`": that would assert a classification nobody ran. This is the rule
`model/header_exclusion_record.py` applies to an unrecorded matching mode.

### D2 — Per-entity ownership is a `Fact`, stamped once in `extract/`

`Function`, `Variable`, `RecordType` and `EnumType` gain
`ownership_fact: Fact[EntityOwnership] | None`, where `EntityOwnership` is
`(owner, contract, rule_id)` from the Phase 1 vocabulary:

- **owner** — `target`, `dependency:<name>`, `toolchain`, `unresolved`;
- **contract** — `public`, `private`, `external` (owned by a dependency or
  the toolchain: the target promises nothing), `unresolved`;
- **rule_id** — the rule that decided, e.g. `target_root:include/`,
  `dependency:fmt:third-party/fmt/include/`, `private_namespace:svs::detail`,
  `system_path`, `builtin`, `no_root`, `no_file`.

`None` (and a `Fact` whose status is not `PRESENT`) is **unknown**: the
declaration was never classified. The `unresolved` owner is different — it is
a positive answer ("classified, and no root claims this file", rule 5) — and
the two must not collapse, which is why this is a `Fact` rather than a
nullable string.

**One evaluation.** `extract/ownership_stamp.py` classifies every
declaration of a snapshot once, from its recorded declaring file, qualified
name and `is_compiler_generated` (castxml's `artificial="1"`, rule 6), and
every later stage — flat detectors, the L2 graph, the release surface —
reads the stamped fact. Nothing downstream re-runs the classifier against
paths; a reader that needs an owner reads the fact or the D5 relation.

**Stored compactly.** The four declaration lists are encoded as they are
today, without the new field; `extraction_scope.entity_ownership` holds one
interned table of distinct `(owner, contract, rule_id)` triples and, per
list, one integer per declaration indexing it (`-1` for an unclassified
one). A decoder that finds a list whose length disagrees with the
snapshot's own list refuses to attach any decision to that list — it leaves
every declaration in it unknown rather than misattributing one.

`ScopeOrigin` stays, unchanged, as today's compatibility reading. Its
readers migrate as later phases need them to; this ADR moves only the
obligation predicate (D7).

### D3 — Comparability: refuse what changed presence, warn on what changed classification

A sibling of `_check_header_exclusions_comparable` in `comparability.py`,
applied only when both sides carry header-derived declarations:

| OLD | NEW | Outcome |
|---|---|---|
| recorded | recorded, different `dependency_evidence` | **refused** (`ScopeMismatchError`) |
| recorded | recorded, different `prefilter` | **refused** |
| recorded | recorded, different `ownership_rules`, either side `referenced` | **refused** — under `referenced` the rules decide which dependency declarations *exist* |
| recorded | recorded, different `ownership_rules`, both `full` | compared; **warning** plus a report line naming the moved findings |
| recorded | recorded, identical fingerprint | compared, unchanged |
| unrecorded | recorded `referenced` or any `prefilter` | **refused** — nothing proves the unrecorded side kept the same declarations |
| unrecorded | recorded `full`, no prefilter | compared; **report line** naming the unrecorded side |
| unrecorded | unrecorded | compared as today |

The refusal rows cannot fire in this ADR (D6 accepts only `full`, and no
prefilter exists); they are implemented and tested now so the phase that
adds `referenced` does not also have to add its safety rule.

A "moved" finding is one whose subject declaration is classified with a
different owner or contract on the two sides. That is the only effect a
changed rule can have under `full` (presence is unchanged, only the label
moves), so it is what the report line enumerates.

### D4 — The fingerprint is part of the effective configuration digest

`effective_config_fields` gains `surface.ownership`: the comparison's
extraction-scope identity, read off the two snapshots (never off a request,
for the reason `comparison_exclusion_identity` gives: a stored baseline
carries its own rules). One value when the two sides agree,
`old=<a>|new=<b>` when they do not, `""` when neither side recorded one.
Report schema minor bump, additive.

### D5 — Ownership in the graph: three relations, keyed on Phase 1 node ids

The evidence-entity-model Phase 3 relations. Every endpoint on the entity
side is the invariant-I1 node id (`model/graph_entity_identity.py`); an
entity with no resolvable identity keeps its explicit `unresolved://` id.

| Relation | From → to | Evidence class | Producer, inputs, recompute rule |
|---|---|---|---|
| `owned_by` | declaration / type → `owner://<owner>` | `derived` | `compare/ownership_relations.py`, from each entity's persisted `ownership_fact`. Recomputed from the snapshot on every query; never persisted. |
| `in_contract` | declaration / type → `contract://<contract>` | `derived` | same producer and inputs as `owned_by`. |
| `provided_by` | `binary_symbol://…` → `release_member://<member>` | `resolved_join` | same module, from `compare/bundle_export_index.BundleExportIndex` (one observed export table per member, joined on the export spelling). Recomputed per release comparison; never persisted. |

**Why `derived` for the owner edges and not `resolved_join`.** The
classification is an *extraction-time* decision: it needs the declaring
file as the frontend saw it, the project root, and castxml's `artificial`
bit — inputs only the extractor has. That decision is therefore recorded as
a per-entity fact (D2), which is snapshot data. The graph edge is a pure
projection of that record plus the entity's node id, so by invariant I3 it
is `derived`: recomputed from the record, and a persisted copy would only be
a second thing to go stale. The export → member edge joins two independent
observations (a member's export table and the export-table entry node the
Phase 2 `exports` join produced), under a stated rule (the spelling appears
in that member's default-version export set), so it is `resolved_join`.
Following the evidence-entity-model Phase 5 rule ("persist observed, derive
the rest"), **none of the three is persisted**: no graph section, node
vocabulary or schema changes for them.

A declaration whose ownership is unknown gets **no** `owned_by` /
`in_contract` edge; the query answers `unknown` for it, never a fabricated
`unresolved`.

### D6 — `dependency_evidence` accepts only `full`

`scope.dependency_evidence` becomes a recognised `.abicheck.yml` key with
one accepted value, `full` (today's behaviour, now recorded). `referenced`
is rejected at config load with a message naming the phase that will honour
it. Accepting a value the run cannot act on would be inert configuration.

**Classification, not retention.** The `scope.dependencies` /
`private_*` keys now drive classification (D2) and nothing else. No
declaration is dropped, kept or rewritten because of them in this ADR;
`dependency_scope`'s system-header filter is unchanged.

### D7 — The contract is modelled once (invariant I5)

1. **Contract inputs are explicit.** `CompatibilityEvaluationConfig.surface`
   gains `ownership`: the target roots, dependency roots, private headers,
   private namespaces and `dependency_evidence` the run classified under,
   each with D7 (`ADR-049`) provenance (`explicit_cli` for a `-H` directory,
   `project_config` for a `.abicheck.yml` key, `api_request` for a typed
   `InputSpec`), so a receipt says where every contract input came from
   instead of leaving it implicit in paths. `scope.private_namespaces` merges
   into the effective `internal_namespaces`, as the plan specifies.
2. **One obligation predicate.** A public declaration owes an export only
   when its contract is `public` — or unknown, where today's `ScopeOrigin`
   reading still decides. A declaration classified `private` or `external`
   owes nothing, whatever its `ScopeOrigin` says. The per-member
   `public_not_exported` check and the release surface's obligations share
   this one predicate, so the scalar path, a one-member package and a
   multi-member release answer from the same rule.
3. **One provider model.** Export → member attribution in a release comes
   from the `provided_by` relation built over `BundleExportIndex`; no second
   `symbol → member` derivation exists.

## Consequences

- A snapshot written by this build differs from a pre-v52 one in one
  top-level key; declaration lists are byte-identical.
- The first comparison of an existing (pre-v52) baseline against a new dump
  compares as before and carries one report line saying the baseline's
  extraction scope was not recorded. Refusing would reject every existing
  baseline on upgrade for a difference that cannot exist — no build before
  v52 could narrow a dump by these rules.
- A project that declares a component's headers as a dependency (or as
  private) stops receiving `public_not_exported` for them. This is a
  documented fix, not a regression: the declaration was never this target's
  export obligation.
- The snapshot grows by one small integer per declaration plus an interned
  table (measured on oneDAL in the plan's Phase 2 section).

## Relationship to existing decisions

- **ADR-049** — D7's resolver and provenance vocabulary carry the new
  contract inputs; nothing about precedence changes.
- **ADR-050** — the comparability contract gains one sibling check, in the
  same "a post-parse narrowing the fingerprints cannot observe" family as
  `dependency_scope` and the header-exclusion record.
- **ADR-062** — snapshot schema v52; the field lives in the snapshot
  envelope's metadata section; a pre-v52 snapshot loads unchanged.
- **ADR-063** — `ownership_fact` is a `Fact[T]` (D2); the relations are
  graph queries over the D3/Phase 1 identity (D5); the evidence classes are
  the Phase 0 vocabulary.
- **ADR-065** — unaffected: ownership says whose a declaration is, never
  whether a member was selected, produced or retired.

## Verification

- Classifier contract re-asserted through a real `abicheck dump`
  (rule-order independence, most-specific root wins, `-I` never grants
  ownership, a system prefix never beats an explicit root).
- Schema v52 round trip and a pre-v52 migration test (ownership loads
  unknown, never guessed).
- A comparability refusal between differently-configured snapshots, and an
  unchanged verdict when the configurations are equal.
- The MKL/oneDAL scenario end to end through the directory compare, and a
  one-member package matching the scalar path.
- A hypothesis property: ownership is independent of the order headers and
  declarations are visited.
