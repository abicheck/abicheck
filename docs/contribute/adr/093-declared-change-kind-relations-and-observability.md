# ADR-093: Declared change-kind relations and observability

**Date:** 2026-11-14
**Status:** Accepted — design only. The metadata schema and its fill land in
the WS-1 slices of
[`abicheck-abiforge-completeness.md`](../plans/abicheck-abiforge-completeness.md);
the resolution pass lands in WS-5.

## Context

Two facts about every `ChangeKind` live today in hand-written tables or not at
all:

1. **Where the kind can be observed.** `scripts/evidence_tiers.py` assigns a
   single rank per *example case*; abiforge's `EditOp.min_evidence`,
   `evidence_floor` and the ledger's `verified_floor` assign a single rank
   per *operator*. All three assume a chain `binary < dwarf < headers <
   build < sources`. The minimal-evidence ladder study (plan Appendix A)
   shows the chain is false: DWARF (`D`) and headers (`H`) are siblings
   above the binary (`B`). DWARF carries compiler facts headers never carry
   (producer flags, char signedness, base-type sizes after `-m` switches);
   headers carry declaration facts DWARF never carries (default arguments,
   macros, `explicit`, deprecation, inline and template bodies). A rank
   cannot say "L1 *or* L2", nor "L2 but not L1". As a consequence the
   product's primary configuration — stripped binary + public headers,
   `{B,H}` — is neither specified nor tested.
2. **How kinds relate.** One fact is reported under several kinds at
   different evidence levels (`FUNC_REMOVED` / `FUNC_REMOVED_ELF_ONLY`,
   `VALUE_ABI_TRAIT_CHANGED` / `TRIVIALLY_COPYABLE_LOST`,
   `DEFAULT_ARGUMENT_CHANGED` / `PARAM_DEFAULT_VALUE_CHANGED`, the last pair
   with *different verdicts*), and derived findings follow from primary ones
   (virtual → pure → abstract → vtable). These relations live in
   name-derived rules (`model/change_catalog/kind_variants.py`), manual
   deduplication tables and detector order (plan A8). Results can depend on
   which detector ran first, and a new kind silently escapes every table.

## Decision

### D1 — Evidence sources are atoms; a configuration is a set

`abicheck/model/change_catalog/observability.py` (a leaf module in `model/`,
importing nothing outside the standard library) defines:

- `class EvidenceSource(StrEnum)`: `B` (binary: symbols, versions, sections,
  notes), `D` (DWARF, or PDB on Windows), `H` (public headers, *without*
  build flags), `BD` (build data: compile commands, flags), `S` (sources /
  TU), `G` (multi-TU source graph).
- `EvidenceConfig = frozenset[EvidenceSource]`. The named levels are
  configurations, not ranks: L0 = `{B}`, L1 = `{B,D}`, L2s = `{B,H}`,
  L3 = `{B,H,BD}`, L4 = `{B,H,BD,S}`, L5 = L4 + `G`. Every configuration
  contains `B` except for header-only targets.

`{B,H}` is the **primary configuration**: it is what a library actually
distributes. Everything physically visible in a public header must be
caught in `{B,H}` without DWARF.

### D2 — `observable_at` on `ChangeKindMeta`

`ChangeKindMeta` gains, keyword-only and declared after `contract_subject`:

```python
observable_at: tuple[Observability, ...] | Unclassified  # default UNCLASSIFIED
```

```python
@dataclass(frozen=True, slots=True)
class ObservabilityCondition:
    platform: frozenset[Platform] | None = None   # elf / pe / macho
    language: frozenset[Language] | None = None   # c / cxx

@dataclass(frozen=True, slots=True)
class Observability:
    alternatives: frozenset[EvidenceConfig]       # the minimal configurations
    rationale: str = ""
    condition: ObservabilityCondition | None = None
```

- `alternatives` is the set of **minimal** configurations in each of which
  the fact is physically present and abicheck must report it. It is
  non-empty and an **antichain** (no alternative is a superset of another);
  construction rejects anything else.
- `rationale` is required whenever no alternative is `{B}`: it names what
  carries the fact (a compiler flag, the vtable's home TU, a body in the TU,
  …). This is the row of the plan's §8 correspondence table.
- More than one `Observability` per kind is allowed only with pairwise
  disjoint `condition`s (e.g. ELF vs PE), so lookup is unambiguous.
- `UNCLASSIFIED` is an explicit sentinel, not an empty set. Its count is a
  visible metric that may only decrease; a newly added kind may not use it.

**Monotonicity.** `is_observable(obs, config)` is true iff `config ⊇ c` for
some `c ∈ alternatives`. Adding evidence never makes a kind unobservable.

**Verdict-specific levels** (e.g. `FUNC_VIRTUAL_BECAME_PURE` is an API break
visible in `{B,H}` but a proven ABI break only with a library-owned
definition, `{B,H,S}`) are expressed by the kind's existing
`policy_overrides`/evidence-gated classification, not by splitting
`observable_at`: `observable_at` states where the *fact* is visible; policy
decides what it means (AGENTS.md "Policy decides acceptance, not facts").

### D3 — What `observable_at` obliges

For a comparison run with evidence configuration `C`:

- **Observable** (`C` contains an alternative): failing to report the kind
  for a change that occurred is a defect — a *false clean* when the expected
  verdict is a break. abiforge generates these cells and gates on them.
- **Not observable** (`C` contains no alternative): the run must not claim
  the corresponding surface is clean. It reports the reduced assurance
  (ADR-090, ADR-064 amendment below); it never fabricates the finding and
  never reports `NO_CHANGE` as a proven result for that surface.

### D4 — Declared relations

`ChangeKindMeta` also gains one keyword-only field, `relations: KindRelations`
(default: no relations), a frozen value holding:

| Field | Meaning | Constraint |
|---|---|---|
| `variant_of: ChangeKind \| None` | the same fact, the kind name encoding the evidence it was detected with (the generalised `*_elf_only`) | target's `variant_of` is `None`; a selector naming the target matches the variant (today's `kind_match_set`) |
| `same_event_as: ChangeKind \| None` | two detectors name one fact | **same `default_verdict` and `contract_subject`** as the target; target's `same_event_as` is `None` |
| `subsumed_by: tuple[Subsumption, ...]` | a finding is redundant given another on the same subject | `Subsumption(kind, key)` with `key ∈ {ENTITY, ENTITY_FIELD}`, a fixed matching key, never a free-form predicate |
| `implies: tuple[ChangeKind, ...]` | a primary finding implies derived ones (virtual → pure → abstract → vtable) | the `implies` graph is acyclic |

Targets are `ChangeKind` values (the catalog module cannot import the enum). Every target is an existing kind. All constraints are checked by a catalog
test, not at run time. `same_event_as` requiring equal verdicts makes the
`DEFAULT_ARGUMENT_CHANGED` / `PARAM_DEFAULT_VALUE_CHANGED` mismatch a catalog
error that must be resolved in the catalog (plan A2.4).

### D5 — One resolution pass, every disposition visible

A single pass, `abicheck/policy/kind_relations.py`, resolves the relations
after all detectors have run:

- It depends only on the set of raw findings, never on detector order; the
  result is invariant under any permutation of detector execution.
- It never deletes a finding. A finding resolved as a variant, same event,
  subsumed or implied keeps its raw record and receives a disposition
  naming the relation, the related finding and the rule (ADR-067 "record
  before disposing"). Counts of resolved findings stay visible on a passing
  run.
- Public `ChangeKind` values stay flat; no report schema field is removed.
- Once the pass owns a relation, the manual table that encoded it is
  deleted in the same PR (`kind_variants.py` first). An `architecture/` rule
  forbids new kind-relation tables outside the catalog.

### D6 — Other per-level tables derive from the catalog

`scripts/evidence_tiers.py` remains the owner of the *per-example-case* axis
(`examples/`, `catalog/ground_truth.json`). Its per-kind data, the detector
spec (`docs/reference/detector-spec.*`) and its tier labels derive from, or
are checked against, `observable_at`. abiforge's `min_evidence`,
`evidence_floor` and `verified_floor` are replaced by `observable_at`
inherited through `abiforge/checkers/abicheck.py` (the only abiforge module
allowed to import abicheck).

### D7 — Processor-conditioned observability (amendment, 2026-11-18)

`ObservabilityCondition` gains a third axis, `arch` (an `Arch` enum: `x86_64`,
`aarch64`), next to `platform` and `language`. `None` on an axis means every
value of it, and an unknown run arch (`None`) matches, as for the other axes.

A kind takes an `arch` condition only when its emission depends on the
processor, not on the binary format or language. The first is the x86-64
System V struct-return convention (`struct_return_convention_changed`): the
rule that decides whether an aggregate is returned in registers or through a
hidden pointer is an x86-64 psABI fact, so an aarch64 cell does not expect the
kind. (A second kind's arch dependence is held until its owner confirms it.)
A kind is conditioned on `arch` only with an oracle that shows it is absent on
the other architecture, and a new `Arch` value is added only when a kind first
needs it.

## Consequences

- Adding a `ChangeKind` gains one step: declare `observable_at` (and any
  relation). AGENTS.md "Adding a new ChangeKind" is updated with the schema
  PR.
- The `{B,H}` configuration becomes testable and gated; the defects the
  ladder study found (plan A.3) surface as red cells owned by named PRs.
- Detector order stops being a semantic input.
- `ChangeKindMeta` grows two keyword-only fields (`observable_at`, `relations`); positional construction
  and pickling are unaffected.

## Alternatives considered

- **A per-kind `min_evidence` rank.** Rejected: it cannot express D and H
  as siblings, which is the defect being fixed.
- **Splitting twin kinds into one kind with an evidence attribute.**
  Rejected for now: it changes public `ChangeKind` values, report schemas
  and user suppressions. Relations achieve the same canonicalisation with
  flat values.
- **Resolving relations inside detectors.** Rejected: it is what makes
  results order-dependent today.

## Relationship to other ADRs

- **ADR-049** — amended: a kind and its `variant_of`/`same_event_as` target
  declare the same `contract_subject`.
- **ADR-061** — the vocabulary is in `model/`, the pass in `policy/`;
  import direction is unchanged.
- **ADR-063** — amended: evidence levels are sets of sources; `{B,H}` is the
  primary configuration.
- **ADR-064** — amended: a surface not observable in the run's configuration
  lowers assurance and cannot yield a clean verdict.
- **ADR-067** — relation dispositions are ordinary dispositions with a rule
  and a reason.
- **ADR-090** — D3 is ADR-090 applied per kind.
