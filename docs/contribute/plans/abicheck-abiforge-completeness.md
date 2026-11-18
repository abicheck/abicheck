---
doc_type: contributor
level: expert
lifecycle: active
generated: false
---

# abicheck completeness by minimum evidence level, and abiforge as generator-validator

**Status:** Proposed (2026-11-14). No code changes yet. Every workstream in
§11 is **not started**. Claims below were re-verified against `main` at
`91c4fb83` (after #2063). The workstream status table in §11.2 is the single
place where progress is recorded. Every PR that lands a slice updates its row
in the same PR.

**What this plan owns:**
- the evidence-level model of change kinds (`observable_at`);
- the declared relations between kinds;
- making the primary configuration (stripped binary + headers) correct;
- abiforge's evidence axis, configuration-space coverage and self-testing;
- splitting all of this into workstreams that independent sessions can run
  in parallel (§11).

**What it does not own (cross-links, not duplicates):**

- [Formal methods and generator completeness](formal-methods-and-generator-completeness.md)
  owns the *proved* fragments:
  - E0–E10;
  - `formal/FRAGMENT.md` and `formal/EXPERIMENTS.md`;
  - the Lean layout table;
  - the bijective generator for the layout fragment;
  - the edit-class model in `abiforge/model/edit_model.py`.

  This plan *consumes* those results. B3 below extends the space outside the
  proved fragments, with *argued* completeness. New fragments, in that plan's
  stated order, stay in that plan.
- [Header backend parity and client matrix](header-backend-parity-and-client-matrix.md)
  and [g28](g28-castxml-clang-l2-parity-hardening.md) own castxml/clang/gcc
  parity. A2.6 only points there.
- [Evidence entity model](evidence-entity-model.md) and
  [g39](g39-per-finding-evidence-provider-model.md) own the per-finding
  evidence provenance. `observable_at` is the per-*kind* claim. A finding's
  provider record says which source actually produced it.

**Related:**
- `vision.md`;
- ADR-028, 049, 050, 061, 063, 064, 065, 067, 090, 091;
- `docs/contribute/testing.md` § "Generated ABI edit corpus coverage";
- `abiforge/CLAUDE.md`.

---

## 1. Goals and principles

### 1.1 Two tracks

- **Track A — abicheck.**
  - Catch every change that is physically observable in the evidence
    provided.
  - Produce no false results: neither a false "clean" nor a false break.
  - Scale to new kinds without manual tables growing and without results
    depending on detector order.
- **Track B — abiforge.**
  - For a language and configuration, generate a set of edits whose
    completeness is *proved* (formalized fragments) or *argued* (everything
    else).
  - Validate abicheck's checks against independent ground truth.
  - Find new scenarios.
  - Later: other languages and configurations (MSVC/PE fully, Rust
    `repr(C)`, Fortran `bind(C)`, Cython).

### 1.2 Principles (binding from the first PR, even while coverage grows)

1. **A level is a set, not a rank.**
   - A kind's minimum level is a set of alternatives (an antichain) over the
     lattice of evidence sources, not one `min_evidence` rank.
   - L1 (DWARF) and L2 (headers) are siblings above L0. DWARF sees compiler
     facts that headers never carry: producer flags, char signedness, ISA,
     base-type sizes after `-m` switches. Headers see declaration facts DWARF
     never carries: default arguments, macros, `explicit`, deprecation,
     inline and template bodies.
2. **The primary configuration is stripped binary + headers, `{B,H}`.**
   - This is what is actually distributed.
   - Everything physically visible in a header must be caught in `{B,H}`
     without DWARF, and abiforge must generate that cell.
3. **Catch at the cheapest level that can observe the fact, never at the
   price of a false "clean".**
   - If a fact is caught at L0 or L1, claim that and gate on it.
   - If a fact is not visible at a level, the report lowers assurance
     (ADR-090: an unresolved surface is not a clean verdict) rather than
     saying NO_CHANGE.
4. **"Headers cannot see it" is decided case by case.**
   - Each such case gets a row in the correspondence table (§8).
   - The row gives the reason (compiler flag, vtable home, body in the TU, …)
     and the level that sees it (L1/L3/L4/L5).
5. **Truth comes from the edit's semantics and from execution, never from
   the checker's answer** (`abiforge/CLAUDE.md` "Ground truth").
6. **No xfail or known-gaps entry as an end state.**
   - A divergence is fixed at its cause.
   - If the expectation is wrong, change ground truth and amend the ADR in
     the same PR (AGENTS.md "Long-term right over current behavior").
   - `abiforge/known.py` and `docs/contribute/known-gaps.md` are only a
     transitional state, with an owner and a closing PR, and a gate forbids
     them growing (§7.2).
7. **Facts before policy.**
   - Relations between kinds (variant, same fact, subsumption, implication)
     are declared in the catalog and resolved in one pass.
   - Manual deduplication tables are deleted (AGENTS.md "Record before
     disposing", "Finish the workflow").

---

## 2. Vocabulary

**Evidence source.** An atom:

| Source | Meaning | Level |
|---|---|---|
| `B` | stripped binary: symbols, versions, sections, notes | — |
| `D` | DWARF (PDB on Windows) | — |
| `H` | public headers, *without* build flags | — |
| `BD` | build data: `compile_commands.json`, flags | L3 |
| `S` | sources / TU | L4 |
| `G` | multi-TU source graph | L5 |

**Evidence configuration.** A subset of sources. It always contains `B`,
except for header-only targets:

| Configuration | Name |
|---|---|
| `{B}` | L0 |
| `{B,D}` | L1 |
| `{B,H}` | L2s, the primary configuration |
| `{B,D,H}` | what abiforge's `headers` cell builds today |
| `{B,H,BD}` | L3 |
| `{B,H,BD,S}` | L4 |
| `+G` | L5 |

**`observable_at`.** For a (change kind, context) pair: the set of *minimal*
configurations in each of which the fact is physically present and abicheck
must report it.
- Examples:
  - `CHAR_SIGNEDNESS_CHANGED: {{B,D},{B,BD}}`;
  - `VAR_ALIGNMENT_CHANGED: {{B,D},{B,H}}`;
  - `FUNC_VIRTUAL_BECAME_PURE` as an API break: `{{B,H}}`; as an ABI break:
    `{{B,H,S}}`.
- Monotone: for any configuration `C ⊇ c` with `c ∈ observable_at`, the kind
  must be caught in `C`.

**`invisible_at`.** An explicit claim that the fact is physically absent from
a configuration (used by the reverse check, §6.6). By default, any
configuration that is not a superset of an `observable_at` element counts as
invisible.

**False clean.** NO_CHANGE or COMPATIBLE for an edit whose expected verdict
is BREAKING or API_BREAK, in a configuration that is a superset of an
`observable_at` element. This is the most severe category.

**False break.** A kind with a worse verdict than expected, including noise
kinds (for example, `PUBLIC_NOT_EXPORTED` under `short_wchar`).

**Identity duplicate.** One fact reported as different kinds at different
levels. Example: `VALUE_ABI_TRAIT_CHANGED` at L1 versus
`TRIVIALLY_COPYABLE_LOST` at L2.

**Cell.** One (edit, seed, build configuration, producer, consumer, evidence
configuration, platform) tuple.

**What `observable_at` replaces in abiforge.** Three fields:
- `EditOp.min_evidence` (`abiforge/model/opkit.py`);
- `evidence_floor` (`abiforge/model/expect.py`);
- the ledger's per-tier `verified_floor` (`abiforge/coverage.py`).

**`scripts/evidence_tiers.py` is a different axis.**
- It is per *example case* in `examples/`, not per kind. It is consumed by
  `benchmark_comparison.py --evidence-tiers` and stored in
  `catalog/ground_truth.json`.
- `docs/AGENTS.md` and `docs/_meta/topics.yaml` (three topics) name it the
  fact owner of per-kind minimum evidence. The ladder study found that it
  repeats the chain assumption and misplaces kinds (`public_macro_removed`,
  `template_body_changed` and `inline_function_removed` at L4).
- It is **not deleted**. WS-1 moves the per-kind fact to
  `ChangeKindMeta.observable_at`, makes `evidence_tiers.py` derive from or be
  checked against it, and updates `docs/AGENTS.md` and `topics.yaml` in the
  same PR.

---

## 3. Track A — abicheck

**Owners per ADR-061:**
- kind metadata: `model/change_catalog/`;
- detectors: `compare/` and the root `diff_*.py` modules;
- relation resolution and classification: `policy/`;
- extraction: `extract/`.

### A0. ADR-093 "Declared change-kind relations and observability"

**One ADR.** The next free number is 093; the last accepted is 092.

**New fields on `ChangeKindMeta`** (fields today: `kind`, `default_verdict`,
`impact`, `is_addition`, `policy_overrides`, `description_template`,
`entity`, `operation`, `entity_from_field`, `is_informational`,
`contract_subject`):
- `observable_at: frozenset[frozenset[Source]]`, optionally with a platform
  or language condition, plus `observable_rationale: str`;
- `variant_of` / `same_event_as`: a twin kind that encodes the evidence level
  points to its canonical kind;
- `subsumed_by`, with fixed matching keys (entity + field);
- `implies`: chains such as virtual → pure → abstract → vtable. It suppresses
  derived findings *while keeping their disposition visible* (ADR-067).

**One shared resolution pass.** `policy/kind_relations.py` (tentative name)
resolves the relations after all detectors run. Public `ChangeKind` values
stay flat, for report-schema compatibility.

**Acceptance:**
- the ADR is accepted;
- ADR-049 (`contract_subject`), ADR-063 (evidence levels as sets, `{B,H}`
  primary) and ADR-064 are amended in the same PR;
- an `adr-surface-registry.yaml` row is added.

### A1. Metadata: fill `observable_at` for all 434 kinds

- An initial-fill script seeds the field from the ladder data (Appendix A),
  followed by manual review.
  - Kinds without data get an explicit `UNCLASSIFIED` sentinel, which is
    visible in the metric.
  - A new kind without the field fails the test.
- New test `tests/test_change_kind_observability_complete.py`:
  - every kind has the field;
  - no set is empty;
  - each set is an antichain (no nested alternatives).
- `scripts/evidence_tiers.py`, `docs/reference/detector-spec.*` and
  `TIER_LABELS` derive from the metadata. `docs/AGENTS.md` and the three
  `topics.yaml` `fact_sources` entries move to the registry.
- AGENTS.md "Adding a new ChangeKind" gains one step: fill `observable_at`.

**Acceptance:**
- `UNCLASSIFIED` count ≤ the number of kinds outside
  `abiforge/coverage_scope.py`;
- mypy 0 errors;
- `check_architecture.py` and `check_docs_contract.py` green.

### A2. Close the defects of the primary `{B,H}` configuration

One PR per class. Each comes with a class regression test: several
independent siblings, with the compiler's layout probes as the oracle, not an
abicheck helper.

| PR | Defect | Root cause (hypothesis, to verify) |
|---|---|---|
| A2.1 | `var_alignment_raised` → NO_CHANGE (false clean of a BREAKING edit); `object_alignment_reduced` has no kind | variable alignment is read only from DWARF. Fix: extract `alignas`/`aligned` from the header AST for all three backends |
| A2.2 | `record_became_packed` → `TYPE_ALIGNMENT_CHANGED` + noisy `PUBLIC_NOT_EXPORTED` | L2 does not derive the packing kind; the noise is the A2.5 defect |
| A2.3 | `returned_struct_grew_past_registers` without `STRUCT_RETURN_CONVENTION_CHANGED` | psABI classification runs only on the DWARF layout. Fix: move it into `model/` as a function of a layout from any source |
| A2.4 | identity duplicates: `VALUE_ABI_TRAIT_CHANGED`/`TRIVIALLY_COPYABLE_LOST`; `DEFAULT_ARGUMENT_CHANGED`/`PARAM_DEFAULT_VALUE_CHANGED` (different verdicts); `UNINSTANTIATED_TEMPLATE_REMOVED`/`INLINE_FUNCTION_REMOVED` | one fact, two kinds. Fix: canonicalize with `same_event_as` (A0) |
| A2.5 | `PUBLIC_NOT_EXPORTED` noise in stripped cells (`short_wchar`, packed) | header declarations are matched against exports differently without DWARF. First decide whether some hits are legitimate without a version script |
| A2.6 | four castxml backend misses on `headers` operators (contract attributes, `va_list` params, alignment lower bound) and gcc plugin misses (`va_list`, dynamic exception spec) | backend parity; owned by g28 and the header-backend parity plan |
| A2.7 | `func_became_ms_abi` c_elf BD: `CALLING_CONVENTION_CHANGED` missing (NO_CHANGE at DWARF, a consumer breaks) | DWARF calling-convention facts are not read, so an ABI change of the same function's convention is invisible at DWARF. Hypothesis: the attribute is not extracted; verify before fixing |
| A2.8 | `field_became_const` c_elf BD and `field_access_narrowed` cxx_more BD (the latter appears once FIELD_ACCESS_CHANGED is observable at DWARF): `FIELD_BECAME_CONST` (catalog default API_BREAK) reported as COMPATIBLE_WITH_RISK on a binary-only-consumer ELF cell | a verdict downgrade in policy for this kind; the kind is found, its severity is not kept. Hypothesis: policy demotes source-only breaks on ELF; verify |
| A2.9 | `record_lost_standard_layout` cxx_more BD: `STANDARD_LAYOUT_LOST` not emitted at DWARF; `FIELD_ACCESS_CHANGED` (COMPATIBLE_WITH_RISK) emitted instead | detector gap for the standard-layout kind, plus `FIELD_ACCESS_CHANGED` is declared {B,H} only while it is emitted at DWARF (a catalog declaration to correct, orchestrator's call) |
| A2.10 | `rebuild_pack_struct` c_core and cxx_classes BH: a -fpack-struct rebuild that breaks consumers (layout alignof 8 -> 1, offsets move) reads NO_CHANGE on stripped headers | a flag-induced layout change is invisible to a stripped header dump, so the checker must report it evidence-limited, never clean (ADR-066: unknown is not clean). Detector gap, not an expectation error: the oracle shows a real binary break |
| A2.11 | `rebuild_optimized` cxx_stdlib BH below-minimum: COMPATIBLE_WITH_RISK with STACK_CANARY_REMOVED and STACK-protector/import kinds at -O2; the B cell is a known.py xfail, the BH cell is not | the same false positive as the known.py entry, one configuration up: the headers configuration carries no protector census. Add no xfail: fix the census or record the gap as A2.11 |

Each is accepted when the `{B,H}` cell for its operators is green on gcc and
clang and the matching `known.py` entries are deleted.

### A3. "Not visible in headers" → an honest assurance downgrade

- **Applies to:** `char_made_unsigned`, `target_isa_raised`,
  `long_double_made_64bit`, `short_wchar`, `no_common_spelled` and
  `vector_math_abi_changed`. All have `observable_at = {{B,D},{B,BD}}`.
- **Required behavior:** in `{B,H}` the report carries an assurance fact,
  "ABI-affecting build flags unknown" (ADR-090/063), not an empty clean
  result.
- **`debug_info_dropped`:** not applicable when both sides are stripped.

**Acceptance:**
- the abiforge `{B,H}` cell for these operators expects `reduced_assurance`,
  a new expectation category, neither NO_CHANGE nor BREAKING;
- a golden report shows the row.

### A4. Lower the level where DWARF already sees the fact

Thirteen operators labeled `headers` are already caught at L1:
- `field_became_volatile`, `field_lost_volatile`, `field_lost_const`;
- `ctor_explicit_removed`;
- `type_lost_abstract`;
- `polymorphic_type_added_without_virtual_dtor`;
- `struct_became_class`;
- `fixed_enum_became_opaque`, `enum_class_became_opaque`;
- `serialization_tag_swapped`;
- `internal_type_grew`;
- `internal_template_instance_dropped`;
- `cxx_record_added`.

They become `{{B,D},{B,H}}`, so an L1 regression is gated.

### A5. Header facts currently gated behind sources (~11 operators)

Reviewed case by case, using Appendix A.2:

- **Macros (`src_macro_*`).** The `#define` is in the public header. Move to
  L2: the clang backend can read it; castxml cannot, which is a parity gap.
  Fix `docs/learn/what-each-level-sees.md` and `docs/start/first-check.md`,
  which say macros need sources.
- **Inline and template bodies.** Need a body fingerprint at L2 for
  header-defined functions.
- **`tree_internal_rtti_exported`.** Already caught at L2 on every backend:
  drop the sources precondition.
- **`tree_private_declaration_exported`.** Split:
  - `EXPORTED_NOT_PUBLIC` is owed at L2;
  - the private-header attribution stays at L4.
- **`tree_pointee_type_renamed`.** The rename is not paired at L2, and L1
  reports it as BREAKING when it is ABI-neutral. Fix both.
- **`tree_type_definition_diverges_per_tu`.** An L1 detector is missing (two
  CUs, one name, two sizes).
- **Call-graph kinds and `l3_*` mode kinds.** Correctly L4/L5 and L3. Their
  L0 consequences (sanitizer `DT_NEEDED`, imports) are expected at L0 in the
  same operator.

### A6. Pure virtual, key function, vtable ownership

- **Background.** A pure virtual in a class with no key function is
  binary-invisible: consumers own COMDAT copies of the vtable and typeinfo.
  gcc's DWARF never marks a member pure. Where the vtable lives is knowable
  only from the library's `.cpp` files.
- **L4 extractor.** It records `virtual`, `pure`, `inline`, `defined_in_tu`,
  the key function, and the vtable home (the TU that emits `_ZTV`).
- **`FUNC_VIRTUAL_BECAME_PURE`.** An ABI break only with a proven
  library-owned definition; otherwise an API break.
- **New kind `TYPE_KEY_FUNCTION_CHANGED`.** Added per AGENTS.md "Adding a new
  ChangeKind": `types.py`, `canonical_identity_contract.py`.
- **False positives in the header path.** `func_lost_inline` on `= 0`;
  `func_added`/`overload_added` pairing; `inline_function_removed`. Fix and
  cover with siblings.
- **Persist binary purity evidence in header-built snapshots.** Generation 71
  proves purity from a binary-only dump via the vtable slot bound to
  `__cxa_pure_virtual`. Header-built snapshots should keep that evidence too.
  Then remove the `_DEBUG_DECLARATIONS` excusal for `func_virtual_became_pure`
  and `type_became_abstract` in `tests/test_depth_projection_matches_shallow_dump.py`,
  which #2063 added as a stopgap.

### A7. Platform facts: Mach-O and PE

- **Mach-O vtable and thunk facts.**
  - Today `elf_layout` reads the `_ZTV` size and `_ZThn` thunks from ELF only.
  - Read the same facts from the Mach-O symbol table (`extract/`).
  - Then restore strict expectations for `pure_virtual_added`
    (`TYPE_BECAME_ABSTRACT` + `TYPE_VTABLE_CHANGED`) and `primary_base_grew`
    (`BASE_CLASS_OFFSET_CHANGED`) on macOS, and delete the platform rules
    #2063 put first in `abiforge/operators/cxx.py`.
- **PE header pass and MSVC mangling.**
  - The DLL exports MSVC-mangled names, while castxml computes Itanium names.
    The result is `public_not_exported` plus `exported_not_public` noise on
    Windows `case47` and `case99`, which are red on `main`.
  - Fix: compute MSVC decorated names (or match through demangling) in the
    header pass for PE.
  - The vtable symbol `??_7` is a follow-up.

### A8. Detector order and relations

- **An explicit detector dependency DAG.**
  - It replaces `DETECTOR_MODULE_ORDER` in `abicheck/detector_registry.py`.
  - The `detect_*` steps in `abicheck/post_processing.py` become registered
    detectors.
- **Order-independence test.** A permutation of the registry
  (property-based, fixed seed) yields an identical `DiffResult` on the
  corpus.
- **Move the manual relation tables into metadata as their relations land,
  then delete them:**
  - `compare/finding_dedup.py` (`_DWARF_TO_AST_EQUIV`);
  - the redundancy rules used by `diff_filtering.py` and `post_processing.py`;
  - `policy/binary_gated_contract.py` and
    `policy/finding_disposition/binary_gate.py` (`BINARY_GATED`);
  - `model/change_catalog/kind_variants.py`.
- **Twin kinds become `variant_of`:** `*_ELF_ONLY`, `FUNC_DELETED_DWARF`, and
  the `STRUCT_*`/`TYPE_*` pairs.

### A9. Fact diff by family

- Work incrementally, one family at a time (record layout, enum, vtable).
- For each family: one normalized fact from any source, and one diff. The
  source changes only the assurance.
- This removes the class "kind derived only from DWARF" (A2.1–A2.3) by
  construction.

### A10. Loose ends

- The #2063 review follow-ups: export evidence for `func_became_inline` on
  every platform, plus the ADR-061 owner moves. They are in
  [abicheck/abicheck#2065](https://github.com/abicheck/abicheck/pull/2065)
  and should land before WS-1's metadata PR so the two don't diverge.

---

## 4. Track B — abiforge

**Boundary (already enforced by `tests/test_abiforge_boundaries.py`).** Only
`abiforge/checkers/` imports `abicheck`. Core abiforge reads kind metadata
through `checkers/abicheck.py` (`kind_catalog`, `kind_names`). Every Track B
change keeps this boundary, because abiforge is meant to move to its own
repository and to evaluate other checkers.

### B0. Evidence axis: a lattice of sets

- **`abiforge/model/config.py`.** `EVIDENCE = ("binary", "dwarf", "headers")`
  and `EVIDENCE_RANK` become an `EvidenceSet` over the sources in §2.
- **`abiforge/run/harness.py`.**
  - Build with `-g` only when `D` is in the set. Today every non-`binary`
    side gets `-g`.
  - `{B,H}` is really stripped: `strip --strip-debug` plus a check that no
    `.debug_*` section remains.
- **The header dumper gets only `-std`, `-I` and public `-D`.** Layout flags
  (`-fshort-wchar`, `-mlong-double-64`, `-fpack-struct`) go only to cells
  that contain `BD`. Unit test: in `{B,H}` the dumper command contains no
  `layout_flags`.

### B1. `observable_at` on operators

- **`EditOp.min_evidence` becomes `EditOp.observable_at`.**
  - By default it is inherited from the expected kind's metadata, read
    through the checker adapter.
  - An override needs a written reason.
- **No level encoding in preconditions.** `build_data`/`sources` operators
  stop encoding the level through `precondition`.
- **Per-cell expectation.**
  - If the configuration is a superset of an element, the kind is expected.
  - Otherwise the expectation is `reduced_assurance` or not applicable,
    never "kind optional".

### B2. Cell matrix by minimum level

- **For every operator and every element of `observable_at`:**
  - one cell of exactly that configuration, which proves the edit is caught
    at the minimum;
  - one cell one level below each element (the reverse check, §6.6).
- **Higher levels are sampled.**
- **The ledger.**
  - It records `minimal_levels` per kind.
  - `verified_floor` is keyed by (kind, level).
  - `check_floor` fails when a kind is not verified *in a cell at its
    minimal level*.

### B3. Completeness of the edit space (outside the proved fragments)

- **Language standards.** Extend `model/space.py` and `model/edit_model.py`
  with them (§5.2).
- **Proved fragments.** These stay owned by the formal-methods plan.
- **Argued completeness everywhere else.** Enumerate grammar constructs ×
  operations (add, remove, change, move, rename, qualify). Each pair is an
  operator or a recorded "not applicable" with a reason.
- **The metric.** `python -m abiforge.coverage` reports the §5.3 metric.

### B4. Scenario discovery

- **Operator composition.** Compose operators (pairs of edits) and minimize
  with `run/cells.py`, to find interactions such as subsumption and masking.
- **Differential comparison.** Compare with libabigail and ABICC as a source
  of *candidates*, never of truth.
- **What a found scenario becomes.** A new operator or edit class, plus a
  regression.

### B5. Other languages and configurations (later)

- **A `Language` interface:** construct grammar, seeds, compilers, execution
  oracle.
- **First candidates:** C with MSVC/PE fully, then Rust `repr(C)`.

---

## 5. Configuration-space model and coverage metric

### 5.1 Dimensions

| Dim | Values (first wave) | Where in code |
|---|---|---|
| E edit | `edit_model` classes × `space.py` constructs | `operators/`, `model/edit_model.py` |
| L language standard | C89, C99, C11, C17, C23; C++98/03, 11, 14, 17, 20, 23, 26 where supported | `Config.standard` |
| F library build flags | ABI-affecting: `-fshort-wchar`, `-fshort-enums`, `-funsigned-char`, `-mlong-double-64/128`, `-fpack-struct`, `-m32`/`-mx32`, `-march`, `-fvisibility`, `-fno-rtti`, `-fno-exceptions`, `_GLIBCXX_USE_CXX11_ABI`, `-D_FILE_OFFSET_BITS=64`, `-fabi-version`, `/Zp`, `/vd`, … | `layout_flags`, `probe_flags` |
| P producer | gcc (N, N-1, N-2), clang (N, N-2), Apple clang, MSVC | `run/toolchains.py` |
| C consumer + flags | the same compilers; flags that change what the consumer sees (`-std`, feature `-D`, `-fabi-version`, `-fms-extensions`) | `Config.consumers`, `run/consumers.py` |
| V evidence | the configurations of §2 | `EvidenceSet` (B0) |
| O platform/format | ELF x86-64/aarch64, Mach-O arm64/x86-64, PE x64 | `config.objfmt` |

### 5.2 Language standards

- **Declared ranges.** Each construct and operator declares
  `standards: range`. Examples:
  - `[[no_unique_address]]` ≥ C++20;
  - `_BitInt` ≥ C23;
  - inline variables ≥ C++17.
- **Per-standard completeness.** The share of (construct, operation) pairs
  introduced or semantically changed in standard S that have an operator run
  in at least one cell with standard S.
  - The list lives in `model/space.py`, with the standard's section or paper
    number.
  - It is maintained by hand: argued completeness, not proved.
- **A standard transition with unchanged text is itself an edit class.**
  Example: `-std=c++17 → c++20`, which can change aggregate-ness or
  triviality.

### 5.3 Coverage metric

- **Per-dimension coverage.** `cov_X` = values executed / values declared.
- **t-wise coverage.** The share of t-tuples over {E-class, L, F, P, C, V, O}
  that appear in at least one executed cell.

  | Tier | Required coverage |
  |---|---|
  | PR | t=1 in full; t=2 on {E-class, V}, i.e. every operator × every element of `observable_at` (mandatory) |
  | Nightly | t=2 on all dimensions, via a covering array. Implement IPOG in `abiforge/run/tiers.py` with no new dependency, or justify `allpairspy` |
  | Weekly | t=3 on {E-class, F, V} and {E-class, P, C} |
- **Weighting.** Cells whose expectation is BREAKING at the minimum level are
  mandatory, never sampled.
- **Report.** `python -m abiforge.coverage report` writes JSON with `cov_X`,
  the t-wise shares and the uncovered tuples. It is published as a nightly
  artifact, with a non-decreasing gate.

### 5.4 Budgets (estimates, to be measured in B0)

| Tier | Volume | Time | Where |
|---|---|---|---|
| PR | 364 operators × elements of `observable_at` (≈1.6 average) × 1 gcc producer, plus clang selectively ≈ 600–900 cells | ≤ 25 min at 4×, sharded | `tests/test_abi_edit_corpus.py` |
| Nightly | t=2 covering array, ~5–10k cells, `ABICHECK_EDIT_CORPUS_SHARD=K/N` | ≤ 2 h on 8 shards | `tests/test_abi_edit_corpus_matrix.py` |
| Weekly | t=3 subsets, macOS and Windows runners | ≤ 6 h | new workflow |

**Measured in B0 (WS-6 PR 1, gcc 13, `c17`/`c++17`, `-O0`, one serial process, 4 vCPU shared).** 357 applicable (operator, seed) cells of the default matrix, built and asked of the checker: mean 0.73 s per cell for the default `{B,D,H}` cell and 0.75 s for the stripped `{B,H}` cell (the first pass ran alongside a second suite, so these are upper bounds). The gating PR tier (`tests/test_abi_edit_corpus.py -m integration`) takes about 21 min serially, 1413 passed / 736 skipped / 16 xfailed. The PR-tier budget above (≤ 25 min at 4×, sharded) holds with the current per-cell cost; the `{B,H}` cell is not in the PR budget yet (it is a nightly, non-gating lane).

Follow-up owned by WS-2: the default `{B,D,H}` cell still receives the layout flags only because the four cells `rebuild_pack_struct`, `object_alignment_reduced` and `rebuild_cxx11_abi_off` turn red without them (header layout without build flags contradicts the DWARF fact); a WS-2 PR fixes that defect, after which the default cell also drops the flags.

### 5.5 Exhaustive versus sampled

- **Exhaustive:**
  - the formalized fragments;
  - the mandatory (operator × minimum) pairs;
  - the reverse check.
- **Sampled:** everything else, with covering arrays plus a random seed
  recorded in `ABICHECK_EDIT_CORPUS_SEED`.
- **Failures:** every failure found is minimized into a deterministic
  regression.

---

## 6. abiforge self-testing (a strategy, not one-off tests)

1. **Properties of operators** (Hypothesis; `slow` marker for the heavy
   ones):
   - (a) an edit applies to every compatible seed;
   - (b) both sides compile for every declared standard;
   - (c) minimality: the source diff touches only the declared construct
     (AST diff);
   - (d) reversibility: `inverse(op)` exists for add/remove/raise/lower, and
     `apply(inv, apply(op, s)) ≅ s`. The inverse's expectation is the
     mirrored verdict (addition ↔ removal).
2. **Metamorphic tests.**
   - None of these change the expectation or the checker's answer:
     - renaming unrelated entities;
     - reordering independent declarations;
     - adding an unrelated header;
     - switching to another seed of the same class.
   - Two independent edits composed yield the union of their findings.
3. **Independent oracles.** Any conflict between an oracle and the
   operator's expectation is an `oracle-conflict`, meaning the expectation is
   wrong.
   - (a) Execution: a consumer built against the old library runs with the
     new one (`run/exec.py`).
   - (b) Layout probes: `sizeof`, `offsetof` and `alignof` from the compiler.
   - (c) API truth: compile the consumer against the new headers.
   - (d) The Lean layout table.
4. **Mutation testing of abiforge.**
   - Mutate operator expectations (verdict, kind), the `observable_at` rules,
     and the operator itself (a no-op edit).
   - The suite (oracles + checker) must kill each mutant.
   - A no-op edit that yields a green cell is a generator defect.
   - Reuse the existing mutation infrastructure (`quality-gates.md`).
5. **Determinism.**
   - Two runs of one cell with one seed give byte-identical expectation
     reports and the same answer.
   - Cell generation order does not affect results (see A8).
6. **Reverse check of `observable_at`.**
   - For each element `c`, run every maximal sub-configuration `c' ⊂ c`.
   - If the checker catches the kind in `c'`, the claim is overstated: lower
     the level, like the thirteen A4 operators.
   - If abicheck reports "clean" in `c'` without `reduced_assurance`, that is
     an A3 defect.
7. **Generator coverage of the declared space.**
   - The existing coverage ledger plus the §5.3 metric.
   - Gate: every `edit_model` class has an operator or an explicit "not
     applicable", and every in-scope kind has an operator.

---

## 7. Process, gates, documentation

### 7.1 The process rule

**The rule.** Every abiforge edit must be caught at the cheapest evidence
level that can observe it.

**The ladder:**
1. stripped binary (L0);
2. + debug info (L1);
3. binary + headers (L2s, the primary configuration);
4. build data (L3);
5. sources (L4/L5), where everything is eventually catchable.

**When it does not hold:**
- *Fact is in the header but missed in `{B,H}`:* an abicheck defect. Fix it;
  never raise the level to make the cell pass.
- *Fact placed above L2:* needs a written reason why it is absent from every
  header and from the binary.
- *Below the minimum:* the run must not read as a confident clean pass.

**Where it is written:**
- now: this plan, and a pointer in `abiforge/CLAUDE.md` "Ground truth";
- WS-1: ADR-093;
- WS-6: `docs/contribute/testing.md`, with the pure-virtual / no-key-function
  case as the worked example of a justified non-L2 placement.

### 7.2 Gates (CI)

| Gate | Script / test | Tier | Workstream |
|---|---|---|---|
| Every kind has `observable_at` | `tests/test_change_kind_observability_complete.py` | PR (unit) | WS-1 |
| Caught at the minimum configuration | B2 cells in `tests/test_abi_edit_corpus.py` | PR (integration) | WS-6 |
| `{B,H}` dumper gets no layout flags | harness unit test | PR | WS-6 |
| Reverse check | `reverse` cells | nightly | WS-7 |
| No growth of `abiforge/known.py` / abiforge known-gaps rows | new `check_ai_readiness.py` check, decreasing-only baseline | PR | WS-7 |
| Detector-order independence | `tests/test_detector_order_independence.py` | PR (`slow` → nightly if costly) | WS-5 |
| No manual relation tables outside the catalog | `check_architecture.py` rule | PR | WS-5 |
| Coverage metric does not decrease | `abiforge.coverage report --check-baseline` | nightly | WS-8 |

Every step goes through `scripts/verify.py`. `tests/test_verify_profiles.py`
reports what else must change.

### 7.3 Fix rule

When a cell fails:
1. Check it against the oracles.
2. If the expectation is wrong, fix the ground truth (`catalog/ground_truth.json`
   and the operator) and amend the ADR in the same PR.
3. Otherwise, fix the root cause in abicheck, with a class regression test
   (`tests/regressions/manifest.py`, reusing a `BugClass`).

New xfails are not allowed.

---

## 8. Correspondence table

**Final form.** The table is generated from `ChangeKindMeta` plus
`abiforge.coverage report`. The "why not lower" column lives in the metadata
(`observable_rationale`).

**Initial rows (from Appendix A):**

| Kind | Fact | `observable_at` | Why not lower | Operator | `{B,H}` today | Action |
|---|---|---|---|---|---|---|
| VAR_ALIGNMENT_CHANGED | `alignas` on a variable | {B,D}, {B,H} | L0 has no type | `var_alignment_raised`, `object_alignment_reduced` | **false clean** / no kind | A2.1 |
| STRUCT_PACKING_CHANGED | `packed` | {B,D}, {B,H} | — | `record_became_packed` | wrong kind + noise | A2.2, A2.5 |
| STRUCT_RETURN_CONVENTION_CHANGED | psABI return class | {B,D}, {B,H} | — | `returned_struct_grew_past_registers` | no kind | A2.3 |
| VALUE_ABI_TRAIT_CHANGED ≡ TRIVIALLY_COPYABLE_LOST | non-trivial copy/dtor | {B,D}, {B,H} | — | `by_value_struct_became_nontrivial`, `record_lost_trivially_copyable` | duplicate | A2.4 |
| DEFAULT_ARGUMENT_CHANGED ≡ PARAM_DEFAULT_VALUE_CHANGED | default argument | {B,H} | DWARF has none | `src_default_argument_changed` | duplicate, weaker verdict | A2.4 |
| UNINSTANTIATED_TEMPLATE_REMOVED vs INLINE_FUNCTION_REMOVED | header template | {B,H} | — | `src_uninstantiated_template_removed` | wrong kind (castxml: wrong verdict) | A2.4, A5 |
| CHAR_SIGNEDNESS_CHANGED | `-funsigned-char` | {B,D}, {B,BD} | header unchanged | `char_made_unsigned` | NO_CHANGE | A3 |
| LONG_DOUBLE_ABI_CHANGED | `-mlong-double-64` | {B,D}, {B,BD} | `e` mangling same on x86-64 | `long_double_made_64bit` | **false clean** | A3 |
| WCHAR_MODEL_CHANGED | `-fshort-wchar` | {B,D}, {B,BD} | flag | `short_wchar` | noise only | A3, A2.5 |
| TARGET_ISA_CHANGED | `-march` | {B,D}, {B,BD} (L0 with ISA notes) | flag | `target_isa_raised` | NO_CHANGE | A3; investigate L0 via `.note.gnu.property` |
| VECTOR_ABI_CHANGED | vector math ABI | {B,D}, {B,BD} | flag | `vector_math_abi_changed` | NO_CHANGE | A3 |
| TOOLCHAIN_FLAG_DRIFT | `-fno-common` | {B,D}, {B,BD} | flag | `no_common_spelled` | NO_CHANGE | A3 |
| DWARF_INFO_MISSING | DWARF removed | {B,D} (old side) | — | `debug_info_dropped` | n/a | not applicable when both sides are stripped |
| (13 kinds: cv fields, `explicit`, abstract, struct→class, opaque enum, …) | cv / DWARF attributes | {B,D}, {B,H} | — | A4 list | ok | A4 |
| FUNC_VIRTUAL_BECAME_PURE (API) | `= 0` | {B,H} | binary: consumer COMDAT | `pure_virtual_*` | ok, but false `func_lost_inline` | A6 |
| FUNC_VIRTUAL_BECAME_PURE (ABI) | library-owned definition | {B,H,S} | header does not know the vtable home | — (new) | not distinguished | A6 |
| TYPE_KEY_FUNCTION_CHANGED (new) | key function / vtable home | {B,H,S} | inline-ness of the TU definition | — | — | A6 |
| TYPE_VTABLE_CHANGED, BASE_CLASS_OFFSET_CHANGED on Mach-O | `_ZTV`, `_ZThn` sizes | {B} on ELF and Mach-O | — | `pure_virtual_added`, `primary_base_grew` | relaxed on macOS | A7 |
| (PE) PUBLIC_NOT_EXPORTED / EXPORTED_NOT_PUBLIC | MSVC decoration | {B,H} | — | Windows `case47`, `case99` | noise | A7 |
| PUBLIC_MACRO_* | `#define` in a public header | {B,H} | — | `src_macro_*` | NO_CHANGE | A5 |
| INLINE/TEMPLATE_BODY_CHANGED | body in a header | {B,H} | — | `src_inline_body_changed`, `src_template_body_changed` | NO_CHANGE | A5 |
| CALL_GRAPH_*, PUBLIC_*_DEPENDENCY | out-of-line call edges | {B,H,S} / +G | bodies are in `.cpp` | `src_public_entry_*` | n/a (correct) | none |
| (4 header operators, castxml) | — | {B,H} | — | `known.py` | castxml miss | A2.6 |
| (CET) | `cet_enabled` | {B} | — | `cet_enabled` | miss: toolchain default | a host capability, not the ladder |

---

## 9. Risks and open questions

- **Matrix cost.** A lattice of configurations × platforms can blow up the PR
  tier. The PR tier is therefore limited to mandatory cells (minimum level,
  one compiler). The §5.4 figures are estimates.
- **Completeness outside the proved fragments is argued, not proved.**
  - The list of standard constructs is maintained by hand, so items can be
    missed.
  - Mitigation: B4 differential search, and review by standard section.
- **Header backends diverge.** The claim `observable_at ∋ {B,H}` is about the
  fact, not the backend; a backend miss is a parity defect.
- **MSVC/PE.**
  - PDB is the PE counterpart of `D`, and the source model must allow it.
  - Mangling and vtable symbols differ.
  - Windows runner availability for the weekly tier is unknown.
- **Apple clang / Mach-O runners.** Their cost and availability in CI.
- **Public `-D` for `{B,H}`.** Which macros count as "part of the headers"
  needs a policy (ADR-091 vocabulary).
- **`implies` versus suppression.** It risks hiding findings; the disposition
  must stay visible (ADR-067).
- **Migrating `min_evidence`** touches all 364 operators.
  - Do it mechanically.
  - Add a before/after expectation-equivalence test on the current cells.
- **The noise threshold.** Some `PUBLIC_NOT_EXPORTED` hits may be legitimate
  for a stripped binary without a version script. Analyze this before the
  A2.5 fix.
- **`api_undeprecated`** failed to compile in the stripped cell in one run,
  probably because the disk filled. Re-check in B0.

---

## 10. Ordered PR sequence

| # | PR | Track | Depends on | Acceptance |
|---|---|---|---|---|
| 1 | #2063 review follow-ups (#2065) | A10 | — | CI green |
| 2 | This plan + index row | doc | — | ai-readiness and docs-contract green |
| 3 | ADR-093 + ADR-049/063/064 amendments | A0 | 2 | ADR accepted, surface registry row |
| 4 | `EvidenceSet`, a really stripped `{B,H}`, no layout flags in the dumper | B0 | 3 | harness unit test; the ladder run reproduces |
| 5 | `observable_at` on `ChangeKindMeta` + completeness test; `evidence_tiers.py` derives from it | A1 | 3 | zero kinds without the field (`UNCLASSIFIED` explicit) |
| 6 | `EditOp.observable_at`; remove `min_evidence`/`evidence_floor`/`verified_floor`; cells at the minimum | B1, B2 | 4, 5 | expectation equivalence on old cells; new cells red exactly on the known defects |
| 7–12 | A2.1 … A2.6, one at a time | A2 | 6 | `{B,H}` cell green, `known.py` shrinks |
| 13 | `reduced_assurance` category (A3) | A3 | 6 | the 6 operators expect the assurance row |
| 14 | Lower the 13 labels (A4) | A4 | 6 | L1 cells gated |
| 15 | Mach-O vtable/thunk facts; PE MSVC decoration (A7) | A7 | — (strict expectations: 6) | strict expectations on macOS; Windows case47/99 green |
| 16 | Review the ~11 source-gated operators (A5), per family | A5 | 6 | §8 rows filled; operators on `{B,H}` where justified |
| 17 | L4 virtual/pure/key function/vtable home + `TYPE_KEY_FUNCTION_CHANGED` + pure gating; purity evidence in header snapshots (A6) | A6 | 5 | ABI/API distinguished; projection excusal removed |
| 18 | Shared relation pass; move manual tables (A0 implementation, A8 part 1) | A0/A8 | 5 | tables deleted, `DiffResult` identical on the corpus |
| 19 | Detector DAG + permutation test (A8) | A8 | 18 | independence test green |
| 20 | abiforge property, metamorphic and determinism tests | §6 | 6 (1–3 and 5 can start on today's axis) | new `tests/test_abiforge_*` |
| 21 | Reverse check + abiforge mutation | §6.4, §6.6 | 6, 20 | nightly job; kill threshold set after measurement |
| 22 | Space model (standards, flags, consumer), covering arrays, metric + gate | §5, B3 | 4 | `coverage report` with a baseline |
| 23 | `known.py` no-growth gate; docs (`abiforge/CLAUDE.md`, `testing.md`) | §7 | 6 | ai-readiness |
| 24+ | A9 fact diff per family; B4 compositions; B5 other platforms and languages | A9/B4/B5 | 19, 22 | per family |

---

## 11. Parallel execution

This section lets several sessions work at once with few merge conflicts.
Each workstream (WS) owns a set of files, has explicit prerequisites, and has
a self-contained brief (§11.4) that a new session can start from.

### 11.1 Dependency DAG

```
WS-0 (#2065, plan PR) ──┬──> WS-1 ADR-093 + observable_at (A0, A1)
                        │        ├──> WS-2 {B,H} defects (A2, A3, A4, A5)   [also needs WS-6 B0/B1]
                        │        ├──> WS-3 pure virtual / key function (A6)
                        │        ├──> WS-5 relations pass + detector DAG (A8, A9)
                        │        └──> WS-6 abiforge B1/B2 (after its own B0)
                        ├──> WS-4 platform facts (A7: Mach-O, PE/MSVC)     [no prerequisite]
                        ├──> WS-6 abiforge B0 (EvidenceSet, stripped {B,H}) [no prerequisite]
                        ├──> WS-7 abiforge self-testing (§6 items 1–3, 5)   [no prerequisite; 4, 6 need WS-6]
                        └──> WS-8 config space / covering arrays (B3, §5)   [needs WS-6 B0]
```

**Can start today, in parallel:**
- WS-1;
- WS-4;
- WS-6 (B0 slice);
- WS-7 (properties, metamorphic, determinism).

**Start after WS-1's metadata PR merges:**
- WS-2;
- WS-3;
- WS-5.

**WS-8 starts after WS-6 B0.**

### 11.2 Workstream status (update in the same PR as the slice)

| WS | Scope | Plan items | Status | Current PR |
|---|---|---|---|---|
| WS-0 | Review follow-ups; this plan | A10, PR 2 | in review | #2065; this PR |
| WS-1 | ADR-093, `observable_at` on all kinds | A0, A1 | in review (schema, fill); ADR merged | #2067 merged; #2071 schema; fill PR (#2071 merged): 293 of 434 kinds classified, 141 UNCLASSIFIED (decided entries of four overflowing family modules moved into numbered siblings to stay under the 800-line cap) |
| WS-2 | `{B,H}` correctness | A2, A3, A4, A5 | in progress (A2.1 in review) | `fix/ws2-a2-1-var-alignment-raised`: A2.1 done (castxml reads the declared variable alignment from the clang AlignedAttr join; extractor generation 79; `var_alignment_raised` and `object_alignment_reduced` leave `known.py`); A2.2–A2.6, A3–A5 not started |
| WS-3 | Pure virtual, key function, vtable home | A6 | not started | — |
| WS-4 | Platform facts | A7 | in progress (PR 3, PE variables) | #2068 (PR 1, Mach-O); #2073 (PR 2, PE, merged); `fix/ws4-pe-variable-linkage` (PR 3: plain C++ globals bound to MSVC data exports by decoded type, own generation bump) |
| WS-4 follow-up | PE builder consolidation: `_dump_pe` option handling / single PE builder; needs Windows CI snapshot-equality | A7 | not started (follows #2098) | — |
| WS-4 follow-up 2 | Retire `dumper._dump_pe`: route the legacy `dumper.dump()` PE path through `workflows/dump/pe.extract_pe` (header-built PE dumps become header-scoped; `CompileContext` carries the gcc/sysroot options). Needs a generation bump after #2115 and #2122, and a changelog naming `workflows/dump/pe.py` as the owner. Deferred: Linux/L2 focus, no Windows investigation | A7 | not started (follow-up to #2122) | — |
| WS-4 follow-up 3 | PE i686 decorated exports without the leading underscore: the identity decoder strips `@N` only from the `_`-prefixed spelling; lld's unprefixed form keeps it. Known gap with a canary: [known-gaps.md](../known-gaps.md#pe-i686-decorated-exports-without-the-leading-underscore-2026-11-17). Deferred: i686/PE, outside the Linux focus | A7 | open gap (found in #2122) | — |
| WS-5 | Relations pass, detector DAG, fact diff | A8, A9, A0 implementation | in progress (PR 1 `variant_of` pass merged #2078; PR 2 `subsumed_by` collapse merged #2096; PR 3 ADR-093 D5 related finding in review: each subsumed drop names its retained finding (`related`) and the relation code (`relation`) on the in-memory disposition record; the public report does not expose per-record entries yet, a follow-up; PRs 4+: remaining relation tables, detector DAG) | this PR (PR 3, related finding); #2107 (`fix/dedup-exact-same-kind`: same-kind `_dedup_exact` key derived from canonical identity). First relation candidate (confirmed on macOS, #2068): an exported thunk renamed by its offset (`_ZThn`/`_ZTv`) is reported as FUNC_REMOVED_ELF_ONLY/FUNC_ADDED, duplicating VTABLE_THUNK_OFFSET_CHANGED (`test_macho_cell_meets_its_strict_expectation[primary_base_grew-dwarf]`). |
| WS-6 | abiforge evidence axis, `observable_at` on operators, minimum-level cells | B0, B1, B2 | PR 2 in review (per-kind floor, min-level nightly baseline, triage pending); G6 evidence-scoped expectations (`tag_type_renamed` binary, `secondary_base_became_polymorphic` binary): three cells fixed; the two `nonpod_field_added_in_tail_padding` DWARF cells stay red until the WS-1 `observable_at` batch lands | #2069 (PR 1, B0); this PR (PR 2, B1+B2); #2104 (G6 operator expectations); `fix/ws6-evidence-limited-clean` (A2.10 build-flag carrier, A2.11 binary licence; min-level red cells shrink); `fix/ws6-dwarf-cc-stdlayout` (A2.7 producer-capability row for `ms_abi`, A2.9 DWARF standard-layout derivation; extractor generation 80) |
| WS-7 | abiforge self-testing, no-growth gate | §6, §7.2 | in review (PR 2 of 3) | this PR (PR 1: #2070, merged); nightly corpus fix `fix/nightly-builtin-probe-target` (clang -m32 builtin probe; time64 typedef chain landed in #2085) |
| WS-8 | Configuration space, standards, covering arrays, metric | B3, B4, §5 | PR 1 (standards axis) in review | this PR |

**WS-6 PE follow-up (open).** `object_became_weak` on PE (`tests/test_edit_corpus_platform_expectations.py`) owes `VAR_REMOVED` only where `observable_at` admits it at the cell's evidence (catalog `{B,D}`/`{B,H}`, so not at binary). Whether abicheck emits `VAR_REMOVED` on PE at binary is **unverified**: there is no PE corpus lane, so no run shows it. A real PE run decides whether `VAR_REMOVED` gets a PE `{B}` alternative. Until then the test derives the owed group from the catalog.

**WS-1 DWARF `is_inline` reader gap (open, follow-up row).** The DWARF reader never sets `is_inline` (`is_inline_fact` stays `not_collected`), so `func_lost_inline` and `func_became_inline` are declared `{B,H}` and not `{B,D}`. This is a reader gap, not a physical absence: DWARF carries `DW_AT_inline` on an inline function's abstract instance, so a DWARF-reader change could make the twins observable from debug info. Oracle for the current declaration: a DWARF-only pair emits neither twin in either direction, and the same pair with headers emits both (`tests/test_observable_at_matches_emission.py`, inline tests). Re-decide the declaration when the reader sets the fact.

**WS-1 binary-only value reader gap (closed by G7, PR #2115).** A binary-only dump now records an exported read-only object's own section bytes (`Variable.initializer_hex`, declarations section v2, extractor generation 82), and `var_value_changed` compares them hex to hex when both sides have equal-length bytes. The kind is declared `{B}` on that evidence: the binary-only oracle (`tests/test_var_value_initializer_bytes.py`) emits the finding from bytes alone. The gap was that a stripped snapshot carried no type or value for the object, so the detector had nothing to compare.

**WS-1 dead detector: `FUNC_DELETED_ELF_FALLBACK` (closed: not dead).** This row used to say `diff_platform._diff_elf_deleted_fallback` was unreachable from `compare()`. That was wrong. The detector is registered (`@registry.detector("elf_deleted_fallback")`) and fires on the namesake loss: the operator `declared_function_export_became_c_namesake` on `cxx_more`, built with clang 18 at default visibility, emits `FUNC_DELETED_ELF_FALLBACK` (BREAKING). Under `-fvisibility=hidden` the loss is `FUNC_VISIBILITY_CHANGED`. A declared function that the new library exports under neither spelling, with no namesake, is `FUNC_VISIBILITY_CHANGED` (BREAKING), not the fallback. The oracle is `tests/test_func_deleted_elf_fallback_real_build.py`, which runs the real cell and asserts the kinds. The kind stays; retiring it was considered and rejected. Its `observable_at` is still undeclared and belongs to the WS-1 observability seeding (its taxonomy entry in `platform_1.py` is WS-1's), not to this row.

**WS-1 Mach-O gap for `unnamed_type_in_public_abi` (open).** The kind is declared `{B:ELF}`. On a macOS runner the same real-compile pair (`g++ -std=c++17`, exported closure-typed function added) reports only `LC_ID_DYLIB: modified`: the binary-only surface never shows the new exported closure function, so the kind is not emitted. Clang's ELF target does not reproduce the kind at all (the closure-typed function gets internal linkage), so the oracle is g++-specific and gated on the GNU ELF producer. Whether the Mach-O export reader misses the symbol or the detector does not handle Mach-O's leading-underscore mangling is open; the detector session decides. Re-declare for Mach-O only with a passing oracle.

**WS-1 batch 5 (source/platform, open rows).** Declared `{B:ELF}` with a real g++ oracle (`tests/test_observable_ws1_source_platform.py`): `soname_bump_recommended`, `soname_bump_unnecessary`, `visibility_leak`. Still UNCLASSIFIED, with the reason each:

- `exported_object_alignment_reduced` emits from binary-only ELF pairs (`value_alignment` shrinks), but its taxonomy entry lives in `platform_1.py`, outside this batch's file ownership. Its `{B:ELF}` declaration is owed by whichever session owns `platform_1.py`.
- `sanitizer_runtime_required` does not emit through the public path: a `-fsanitize=address` shared object dumps with no ELF identity (`elf` absent), so `_diff_runtime_requirements` never runs. Reader gap (the dump does not capture ELF identity for that build); declare only after the reader or the oracle is fixed.
- `library_version_downgraded` (PE `VS_FIXEDFILEINFO`) and `compat_version_changed` (Mach-O) have platform-only emitters. This container has no MinGW or macOS toolchain, so no oracle can run here; declare from the Windows or macOS lane.
- `rtti_mode_changed` and `sanitizer_mode_changed` fire from build data (`buildsource/build_diff.py`, compile flags), so their oracle needs a compile-commands input, not a binary pair. Next batch.
- `undocumented_export_ratio_increased` and the `source_*` / `declaration_*` L4-L5 kinds need the header or source pipeline; not probed in this batch.
**WS-1 arch axis (ADR-093 D7, amendment).** `ObservabilityCondition` gained `arch` (`x86_64`, `aarch64`). `struct_return_convention_changed` is conditioned on ELF x86-64 (`DH:ELF_X86_64`). `struct_packing_changed` is not arch-conditioned: its detector (`dwarf_advanced._diff_struct_packing`) has no architecture gate, so it stays `DH` in `types_2.py` unconditioned, owned by the types session.

**WS-1 batch 6 (source/platform, Linux first).** Declared with real-build oracles (`tests/test_observable_ws1_source_platform_2.py`): `sanitizer_runtime_required` `{B:ELF}`; `rtti_mode_changed` `{B,BD}` (the `-frtti`/`-fno-rtti` flag is compile data, visible only through a compile database); `undocumented_export_ratio_increased` `{B,H}` (the ratio needs public-header declarations on the new side).

**Correction to the batch-5 row.** The batch-5 row above that `sanitizer_runtime_required` has a reader gap (a `-fsanitize=address` shared object dumps with no ELF identity, so the detector never runs) was **wrong**. The probe read the wrong JSON key (`elf`); a binary-only pair of an ASan-instrumented and a plain build does emit the kind. The batch-6 oracle is the evidence; there is no reader gap for this kind.

**Open (WS-1, not declared).**
- `sanitizer_mode_changed`: a compile database carrying `-fsanitize=address` did not emit it at `--depth build`, while the same pair emitted `sanitizer_runtime_required`. Probable gap in how the build-data reader parses the sanitizer option; re-decide after the reader is checked.
- `func_deleted_elf_fallback`: not reproduced in three shapes (header `= delete`, plain header removal, binary-only). Each emits `func_removed`, `func_visibility_changed` or `func_removed_elf_only` instead. Needs a reproducer that makes the header attribution fail; until then no declaration.
- `exported_object_alignment_reduced`, `inline_namespace_moved` and `abi_surface_explosion` are declared in `platform_1.py`, outside this workstream's ownership. Oracles: the first two emit from binary-only ELF pairs (`value_alignment` shrinks; two or more symbols move between inline namespaces); the third was not probed. Owed to the `platform_1.py` owner.
- `platform_baseline_floor_raised` (build data), the `kabi_*` family and the `sycl_*` family were not probed in this batch.
- Non-Linux, deferred (per the priority from the orchestrator): `library_version_downgraded` (PE `VS_FIXEDFILEINFO`) and `compat_version_changed` (Mach-O) stay UNCLASSIFIED until their own lane runs; no MinGW or macOS toolchain in this container.

### 11.3 File ownership and conflict hotspots

**Owned paths.** Edit another workstream's paths only through a small,
named hunk, and say so in the PR body.

| WS | Owns |
|---|---|
| WS-1 | `abicheck/model/change_catalog/registry.py` (new fields); `observable_at` values in the taxonomy modules (`symbols*.py`, `types*.py`, `platform*.py`, `build.py`, `source*.py`); `scripts/evidence_tiers.py`; `docs/contribute/adr/093-*.md`; `docs/AGENTS.md` evidence paragraph; `docs/_meta/topics.yaml` evidence `fact_sources`; new `tests/test_change_kind_observability_complete.py` |
| WS-2 | `abicheck/extract/` header-path modules (alignment, packing, macros, bodies); psABI classification moved into `abicheck/model/`; the matching `abiforge/known.py` entries (deletions only) |
| WS-3 | the L4 extractor (`abicheck/buildsource/`, `abicheck/extract/` source replay); `dwarf_snapshot.py` purity persistence; `tests/test_depth_projection_matches_shallow_dump.py` (removing the excusal); the new kind `TYPE_KEY_FUNCTION_CHANGED` |
| WS-4 | `abicheck/extract/` Mach-O symbol reading; `diff_elf_layout.py` platform generalization; the PE header pass; the platform rules in `abiforge/operators/cxx.py` |
| WS-5 | `abicheck/detector_registry.py`; `abicheck/post_processing.py`; new `abicheck/policy/kind_relations.py`; the relation tables listed in A8 (deletions); `model/change_catalog/kind_variants.py`; `architecture/` rules |
| WS-6 | `abiforge/model/config.py`, `opkit.py`, `expect.py`; `abiforge/run/harness.py`; `abiforge/coverage.py` (ledger floors); `abiforge/checkers/abicheck.py` (metadata adapter) |
| WS-7 | new `tests/test_abiforge_*` property, metamorphic and determinism tests; the `known.py` no-growth check in `scripts/check_ai_readiness.py` |
| WS-8 | `abiforge/model/space.py`, `edit_model.py` (standards axis); `abiforge/run/tiers.py` (covering arrays); `abiforge/coverage.py` report subcommand |

**Hotspots.** These are touched by several workstreams; rules to follow:

- **`kind_names_{1,2,3}.py`, `kinds.pyi`.** Only WS-3 adds a kind in this
  plan's first wave. Append to the shortest file, regenerate with
  `python scripts/gen_changekind_stub.py`, and merge `main` before pushing.
- **Taxonomy modules (`symbols*.py`, `types*.py`, …).** WS-1 owns the
  `observable_at` values. Any other workstream that changes a kind's level
  (WS-2's A4 and A5) edits only the `observable_at=` line of that entry, in
  its own PR, after WS-1 merges.
- **`abicheck/storage/generation_fingerprints.json`.** Never hand-merge it.
  After merging `main`, re-record with the fingerprint script
  (`--no-semantic-change` when only internals moved), then re-run
  `tests/test_generation_fingerprints*`.
- **`catalog/ground_truth.json`, `examples/`.** Change them only together
  with the ADR amendment that justifies them (AGENTS.md rule). One workstream
  per PR. Regenerate derived docs (`scripts/gen_examples_docs.py`).
- **`tests/perf_repeat_baseline.json` and other perf baselines.** Regenerate
  with the owning script (`--write-repeat-baseline`) after merging `main`.
  Never hand-edit.
- **`abiforge/known.py`.** Deletion-only for every workstream; WS-7's gate
  enforces it.
- **`architecture/debt.yaml` no-growth baselines.** Never raise them. Shrink
  by moving a responsibility instead.
- **This plan's §11.2 table.** Each PR edits only its own row.

### 11.4 Session briefs

Every brief assumes the house rules in §11.5. Paste one into a new session.

**WS-1 — ADR-093 and `observable_at`**
> Read `docs/contribute/plans/abicheck-abiforge-completeness.md` §1–3 (A0,
> A1), §7 and §11. Branch `feat/kind-observability`. First PR: ADR-093
> ("Declared change-kind relations and observability") with ADR-049/063/064
> amendments and an `adr-surface-registry.yaml` row. Docs only. Second PR:
> add `observable_at` and `observable_rationale` to `ChangeKindMeta`; fill
> all 434 kinds from Appendix A plus review (`UNCLASSIFIED` explicit); add
> `tests/test_change_kind_observability_complete.py`; derive
> `scripts/evidence_tiers.py` and `docs/reference/detector-spec.*` from it;
> update `docs/AGENTS.md` and `docs/_meta/topics.yaml`; add one step to
> AGENTS.md "Adding a new ChangeKind". Done when: the completeness test,
> `check_docs_contract.py`, mypy and `verify.py --profile pr` are green, and
> §11.2 is updated.

**WS-2 — `{B,H}` correctness** (after WS-1's metadata PR and WS-6's B0)
> Read the plan's §3 A2–A5, §8 and Appendix A. One PR per row of A2, then A3,
> A4 and A5 per family. For each: reproduce in the stripped `{B,H}` abiforge
> cell; find the root cause; fix it in `extract/`, `model/` or `compare/`;
> add a class regression test with ≥3 independent siblings and an oracle
> that is not abicheck's own helper (compiler layout probes); delete the
> matching `known.py` entries; update the kind's `observable_at` line if the
> level changes. Start with A2.1 (`var_alignment_raised`, a false clean).
> Done when: the operator's `{B,H}` cell is green on gcc and clang, and §11.2
> is updated.

**WS-3 — pure virtual, key function, vtable home** (after WS-1's metadata PR)
> Read the plan's §3 A6. First PR: persist binary purity evidence
> (`__cxa_pure_virtual` slots, generation 71) in header-built snapshots and
> remove the `_DEBUG_DECLARATIONS` excusal for `func_virtual_became_pure`
> and `type_became_abstract` in
> `tests/test_depth_projection_matches_shallow_dump.py`. Bump the snapshot
> generation if stored facts change. Second PR: the L4 facts (`virtual`,
> `pure`, `inline`, `defined_in_tu`, key function, vtable home); gate
> `FUNC_VIRTUAL_BECAME_PURE` as BREAKING only with a library-owned
> definition (API_BREAK otherwise); add `TYPE_KEY_FUNCTION_CHANGED` per
> AGENTS.md "Adding a new ChangeKind". Third PR: the header-path false
> positives (`func_lost_inline` on `= 0`, `func_added`/`overload_added`
> pairing, `inline_function_removed`). Done when: ABI and API are
> distinguished in abiforge cells, there are no test excusals, and §11.2 is
> updated.

**WS-4 — platform facts** (no prerequisite)
> Read the plan's §3 A7. PR 1 (Mach-O): read the `_ZTV` size and `_ZThn`
> thunks from the Mach-O symbol table so the layout detectors in
> `diff_elf_layout.py` work on Mach-O; delete the macOS platform rules at the
> top of `abiforge/operators/cxx.py` for `pure_virtual_added` and
> `primary_base_grew`, restoring strict expectations. The macOS CI lane is
> the acceptance. PR 2 (PE): in the header pass, match header declarations
> against MSVC-decorated DLL exports (compute the decoration, or demangle the
> exports), so Windows `case47` and `case99` stop reporting
> `public_not_exported` + `exported_not_public`. Update ground truth only
> with an ADR reason. Done when: macOS and Windows CI lanes are green without
> platform-relaxed expectations, and §11.2 is updated.

**WS-5 — relations pass and detector DAG** (after WS-1's metadata PR)
> Read the plan's §3 A0 (relations), A8 and A9. PR 1: implement
> `policy/kind_relations.py` resolving `variant_of`, `same_event_as`,
> `subsumed_by` and `implies` from metadata, keeping every disposition
> visible (ADR-067); migrate one manual table (start with `kind_variants.py`)
> and delete it; prove `DiffResult` is identical on the corpus. Further PRs:
> one table each. Then replace `DETECTOR_MODULE_ORDER` with a declared DAG,
> register the `post_processing.py` `detect_*` steps, and add
> `tests/test_detector_order_independence.py` (Hypothesis permutation, fixed
> seed). Add an `architecture/` rule forbidding new relation tables outside
> the catalog. Done when: the tables are deleted, the permutation test is
> green, and §11.2 is updated.

**WS-6 — abiforge evidence axis and minimum-level cells**
> Read the plan's §4 B0–B2 and §5.4. Keep the boundary: only
> `abiforge/checkers/` imports `abicheck` (`tests/test_abiforge_boundaries.py`).
> PR 1 (B0, no prerequisite): replace `EVIDENCE`/`EVIDENCE_RANK` with
> `EvidenceSet`; build with `-g` only when `D` is present; make `{B,H}`
> really stripped (assert no `.debug_*`); give the header dumper only `-std`,
> `-I` and public `-D`; add a harness unit test; measure per-cell cost and
> record it in §5.4. PR 2 (B1+B2, after WS-1's metadata PR): replace
> `min_evidence`, `evidence_floor` and `verified_floor` with `observable_at`
> inherited through `checkers/abicheck.py`; add minimum-level cells and the
> (kind, level) ledger floor; add a before/after expectation-equivalence
> test over current cells. Done when: new cells are red exactly on the known
> A2/A3 defects (each tracked by a WS-2 PR, not an xfail), and §11.2 is
> updated.

**WS-6 minimum-level red set (`tests/data/min_level_red_cells.json`).** A ratchet, not a gate: a new red cell fails the nightly lane, a fixed one must leave the file in the same PR.

| Category | Cells | Owner |
|---|---|---|
| unclassified-triage-pending | 56 | this PR (triage E or D before merge) |
| E: expectation wrong | 0 | fixed in the PR that triages it |
| D: detector gap, A-numbered | 0 | the WS-2 PR named by the A-number |

**WS-7 — abiforge self-testing**
> Read the plan's §6 and §7.2. PR 1 (no prerequisite): Hypothesis properties
> for operators (applicability, both sides compile, AST-diff minimality,
> inverse ops), metamorphic tests, and a determinism test, as new
> `tests/test_abiforge_*` files (`slow` for heavy ones). PR 2: a
> decreasing-only no-growth gate on `abiforge/known.py` in
> `scripts/check_ai_readiness.py`. PR 3 (after WS-6 PR 2): the reverse-check
> nightly job and abiforge mutation testing (mutate expectations,
> `observable_at` and no-op edits; set the kill threshold after measuring).
> Done when: the tests and gates are wired through `scripts/verify.py`, and
> §11.2 is updated.

**WS-8 — configuration space and coverage metric** (after WS-6 PR 1)
> Read the plan's §5 and §4 B3–B4. Coordinate with the formal-methods plan,
> which owns the proved fragments. PR 1: add a standards axis
> (`standards: range` on constructs and operators, a section/paper reference
> in `abiforge/model/space.py`). PR 2: IPOG covering arrays in
> `abiforge/run/tiers.py` (no new dependency, or justify one) and
> `python -m abiforge.coverage report` with per-dimension and t-wise
> coverage plus uncovered tuples. PR 3: a non-decreasing nightly gate. Then
> B4: operator composition with minimization, and differential candidates
> from libabigail/ABICC. Done when: the metric is published nightly with a
> baseline gate, and §11.2 is updated.

### 11.5 House rules for every session

- **Read first:** `AGENTS.md`, the scoped `AGENTS.md`/`CLAUDE.md` of every
  layer you edit, and `abiforge/CLAUDE.md` for abiforge work.
- **Fix the cause.** No new xfail, `known.py` entry, known-gaps row or test
  excusal as an outcome. When a default, ground truth or ADR is wrong,
  change it in the same PR (amendment + ground truth + migration).
- **One branch per PR,** named `feat/<name>` or `fix/<name>`. Merge `main`
  instead of rebasing a pushed branch.
- **Never merge a PR.** The maintainer merges.
- **Before opening a PR:**
  - run `python scripts/verify.py --profile pr`, in the background with a
    timeout; it takes 13–25 min;
  - write the body with the `pr` skill;
  - run `BUGFIX_CONTRACT_BODY_FILE=<file> python scripts/check_bugfix_test_contract.py`.
- **Commits:**
  - Conventional Commits;
  - signed, with author and committer `Claude <noreply@anthropic.com>` for
    agent sessions;
  - a changelog fragment (`scriv create`) when `abicheck/**/*.py` changes.
- **Snapshot extractor changes:** bump or re-record the generation
  fingerprint (§11.3).
- **Update your row in §11.2** in the same PR that lands the slice.
- **Report test results faithfully.** Name skipped steps; an incomplete
  `pr` run is not CI-equivalent.

---

## Appendix A. Input data: the minimal-evidence ladder study

**Method.** A research run, not committed as data. Base: the #2063 branch
before squash-merge. Two runs:
- all 364 operators, gcc, default configuration, at `dwarf` and at `headers`
  built **without `-g`**;
- the 22 `src_*`/`tree_*` operators at `dwarf` and at L2 with castxml and
  clang, with the sources precondition bypassed.

To reproduce the data, re-run under WS-6 B0, where a stripped `{B,H}` cell
exists natively.

### A.1 Structural findings

- **The primary configuration is never tested.** abiforge's evidence axis is
  the chain `binary < dwarf < headers`, and `run/harness.py` builds every
  non-`binary` side with `-g`. So the `headers` cell is really
  `{B,D,H}`, and "stripped binary + headers" is never generated.
- **`min_evidence` can't express the ladder.** It cannot say "L1 or L2",
  "L2 but not L1", or "L3/L4". `build_data`/`sources` operators therefore use
  `min_evidence="headers"` plus a `precondition`, which encodes the ladder
  position as "cell not applicable".
- **The floors don't check minimality.**
  - `evidence_floor` turns "below the floor" into "require nothing", so an
    overstated floor silently waives a cheaper-level gap.
  - `coverage.check_floor` / `verified_floor[tier]` mean "verified in any
    cell of the tier", not "verified at the minimal level".
- **L3 build data leaks into the L2 cell.** The harness hands the header
  dumper the build's layout flags (`-std` + `layout_flags` + `-D/-U`). A real
  distributed header carries no record of them.

### A.2 Matrix results (364 operators, gcc)

| `min_evidence` | dwarf | stripped L2 | n |
|---|---|---|---|
| binary | ok | ok | 110 (+5 vacuous at dwarf) |
| dwarf | ok | ok | 95 |
| dwarf | ok | **miss** | 12 (+1 floor-only), see A2/A3 |
| headers | below floor | ok | 69 |
| headers | **ok (caught at L1)** | ok | 20 (13 with real expected kinds), see A4 |
| headers | below floor | **miss** | 4 (castxml backend), see A2.6 |
| any | n/a (L3/L4 precondition) | n/a | 39 |
| binary (`cet_enabled`) | miss | miss | 1 (host capability) |

**Source-gated operators.** Of the 22, these are header-observable and
should move to L2: macros, default argument, uninstantiated template, inline
and template bodies, virtual dispatch set, public surface grew/shrank,
undocumented export ratio, internal RTTI. Split (L2 part, plus L4
attribution): private-declaration export, private-pointee rename. L1-derivable:
per-TU type divergence, coordinates shift. Genuinely L4/L5: call edges; L3:
the `l3_*` modes.

### A.3 Initial debt the gates will surface

- 13 + 4 stripped-L2 misses (A2, A3, A2.6);
- 13 overstated `headers` labels (A4);
- ~11 header-observable operators gated behind sources (A5);
- 3 identity-duplicate pairs (A2.4).
