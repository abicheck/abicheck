# G28 — CastXML/Clang L2 header-AST parity: hardening and remaining phases

**Origin:** a CastXML-vs-Clang L2 header-AST comparison design document
(PR #582). Its tractable, near-term recommendations (Phase 0 — the original
field const/volatile/mutable bug fix — and Phase 1 — CastXML
schema-completeness) shipped first; Phase 2 (a real parity-test-matrix
between the two backends) was finalized afterward and, by design, kept
finding real parser bugs via Codex review rather than stopping at "tests
pass." This plan records what shipped, the hardening rounds that followed,
and scopes the phases that remain multi-week architectural projects rather
than parser fixes.
**Effort:** Phase 0–2 + hardening — done (see below). Phase 3 — L (new
snapshot-merge architecture). Phase 4 — XL (new compiled C++ tool, ABI
stability risk). Phase 5 — M, partially subsumed by
[G4](g4-header-ast-extractor.md).
**Risk:** low for anything already landed (additive, test-gated). Phase 3 is
medium (provenance bookkeeping can silently regress if a merge rule is
wrong). Phase 4 is high (new heavyweight dependency, a compiled artifact
inside a "pure Python" tool, and Clang's internal AST API has no
cross-release ABI stability guarantee the way CastXML's XML schema does).

---

## Done — Phase 0: the original bug fix

CastXML's field parser never populated `TypeField.is_const`/`is_volatile`/
`is_mutable`, leaving the existing `FIELD_BECAME_CONST`/`VOLATILE`/`MUTABLE`
detectors silently dead on every CastXML-parsed snapshot (the default L2
backend). Fixed by deriving these from the real CastXML type chain
(`CvQualifiedType`, `mutable="1"`), including through `Typedef` indirection
and anonymous struct/union flattening, and extending `_type_name`'s
`CvQualifiedType` handling to `volatile`/`restrict` (previously `const`
only).

## Done — Phase 1: CastXML schema-completeness

CastXML's own XML schema already exposed several facts the header parser
discarded. Landed as 18 new `ChangeKind`s + detectors (2 initializer, 2
abstractness, 2 scoped-enum, 2 override, 10 deprecation):

- Default member initializers (`FIELD_DEFAULT_INITIALIZER_REMOVED`/`_CHANGED`).
- `abstract` records, `enum class` scoping, explicit `override`.
- `[[deprecated]]` on functions/variables/types/enums/**fields** (including
  the bare, message-less form — castxml only emits the dedicated
  `deprecation="..."` attribute for a non-empty message; a bare
  `[[deprecated]]` is recorded solely as a token in the compound
  `attributes` string).
- `Param.is_restrict` wired up (previously dead code).
- `func_signature_cv_only_differ`: a function's own by-value parameter/
  return-type cv qualifier is neutralized (zero ABI/mangling effect),
  distinct from the *intentionally* breaking treatment of a by-value
  **field**'s own cv change (`case30_field_qualifiers` ground truth).
- `AbiSnapshot.ast_producer` (`"castxml"`/`"clang"`) + the `_both_castxml_backed`
  gate, so comparing a castxml-parsed snapshot against a clang-parsed one
  doesn't misread "clang doesn't populate this fact yet" as "the fact was
  removed."

**Explicitly declined as infeasible** (real CastXML schema limits, confirmed
against upstream source — not a scoping choice): `_Atomic` inner-type
recovery, and comment *text* extraction (CastXML only stores a location
reference, never the text).

## Done — Phase 2: the CastXML↔Clang parity gate

`tests/test_castxml_clang_parity_gate.py` runs both backends over the same
real, compiled corpus (functions/overloads/constructors, variables/
constants, namespaced and composite records, bitfields, templates,
`[[deprecated]]`, a plain-C corpus) and classifies every compared fact as
`equal` / `semantically_equal` / `expected_producer_difference` /
`unsupported_on_one_producer` / `unexpected_mismatch`. `tests/test_clang_header_backend_integration.py`
covers the clang backend directly. Both self-skip via `shutil.which()`
rather than an `integration` marker.

Building and then hardening this gate (three full rounds of Codex review)
found and fixed real, previously-undiscovered bugs:

1. **Virtual destructor visibility** — CastXML's `<Destructor>` carries the
   bare class name (identical to its own `<Constructor>`) and usually no
   `mangled` attribute, defaulting a genuinely `PUBLIC` virtual destructor to
   `HIDDEN`. Fixed via a `"~ClassName"` display-name synthesis and
   generalizing the constructor visibility fallback to destructors.
2. **C-linkage variable identity** — the same "case141" pseudo-mangling
   issue already fixed for functions, extended to `parse_variables()`.
3. **Pointer-sigil spacing in `canonicalize_type_name`** — CastXML spells
   `"char const*"` (no space), clang spells `"char const *"`; a real,
   systematic cross-producer spelling difference that could misreport an
   unchanged pointer parameter as a breaking type change.
4. **Destructor ELF-filtering gap** — visibility alone was necessary but not
   sufficient: `_public_functions()`'s ELF-export narrowing still dropped a
   synthetic `"~ClassName"` key whenever real ELF metadata was present
   (the normal case). Fixed via `is_synthetic_dtor_key()`, mirroring the
   existing constructor exemption.
5. **Blank old/new values in initializer-change descriptions** —
   `FIELD_DEFAULT_INITIALIZER_CHANGED` passed `old_value=`/`new_value=`
   instead of `old=`/`new=`, so `make_change()`'s template rendered every
   occurrence as `"(None → None)"`.
6. **Synthetic constructor/destructor keys were not namespace-qualified** —
   two public classes sharing a leaf name in different namespaces
   synthesized the identical key, silently colliding in
   `AbiSnapshot.function_map` ("first-wins"). Fixed by qualifying the
   synthetic key with the enclosing class's fully-qualified name.
7. **Legacy-snapshot CV-fact false positives** — a snapshot *persisted*
   before the Phase 0 fix has real-but-wrong data (permanently `False`
   booleans, qualifier-less type spelling), not merely absent data, so
   comparing it against a fresh dump of unchanged headers misreported false
   `FIELD_BECAME_CONST`/`VOLATILE`/`MUTABLE` and `TYPE_FIELD_TYPE_CHANGED`/
   `UNION_FIELD_TYPE_CHANGED` findings purely from a tool upgrade. Fixed via
   `AbiSnapshot.header_cv_facts_reliable` (derived from `schema_version` on
   deserialization, bumped to v9 for this fix) gating the affected
   detectors — the same "lose one axis of detection to avoid a systematic
   false positive" trade-off `_both_castxml_backed` already makes elsewhere.

Full detail for each fix: `CHANGELOG.md`'s Fixed section under this PR.

## Known, deferred limitation: pointer-vs-pointee CV qualifier position

**Confirmed real** (CodeRabbit review): `_CastxmlParser._type_name`'s
`CvQualifiedType` rendering always emits the qualifier as a *prefix*, so a
volatile pointer *value* (`int * volatile`) and a pointer to a volatile
*pointee* (`volatile int *`) both render as the identical string
`"volatile int*"` — a real transformation between the two (changing which
side of the declarator the qualifier binds to) is invisible to any
string-spelling comparison. This predates this PR's work (`_type_name` is a
general-purpose recursive renderer used everywhere — return types, params,
fields — not something newly introduced here); the constructor-identity
code added earlier in this PR works around the SAME ambiguity for its own
narrow purpose by reading the real XML structure directly
(`_ctor_param_identity_type`) rather than fixing the renderer itself.

**Deliberately not fixed here**: correctly distinguishing the two forms
means rendering an outer pointer-value qualifier as a suffix (`int*
volatile`) while keeping a pointee qualifier prefixed — a change to
`_type_name`'s core recursion touching every call site that spells a
pointer type (params, returns, fields), with real regression risk across
the parity gate's golden comparisons and the `examples/` ground truth. Scope
this as its own follow-up investigation (verify no downstream code depends
on the current collapsed spelling before changing it) rather than a rushed
fix folded into an already-large hardening pass.

---

## Phase 3 — hybrid multi-producer snapshot with per-field provenance

**Problem.** Today a snapshot is parsed by exactly one L2 backend
(`--ast-frontend {castxml,clang}`); the two backends have non-overlapping
blind spots (e.g. concepts/`explicit`-on-converter/ctor-mangled-names are
clang-only-reachable via deeper tooling — see Phase 4/5 — while some facts
above are castxml-only today). A hybrid producer would run both, merge
their `AbiSnapshot`s field-by-field, and record which backend contributed
each fact, upgrading the many `_both_castxml_backed`-gated detectors from
"disabled when producers differ" to "always available, backfilled from
whichever backend saw it."

**Confirmed concrete motivating case (Codex review, PR #582).** A synthetic
constructor/destructor key (`__abicheck_ctor__ns::Class(...)` / `~ns::Class`,
built when castxml omits a real mangled name) has no shared identity with
the SAME entity's real Itanium-mangled key on the clang backend. Comparing
a castxml-produced snapshot against a clang-produced snapshot of the
*same, unchanged* source reports a false `FUNC_REMOVED` + `FUNC_ADDED` pair
for every such unmangled constructor/destructor — verified with a real
castxml+clang dump of an unchanged corpus (both a constructor and a virtual
destructor). This is symmetric, pre-existing behavior — the constructor
case predates the destructor work in this PR — not a new regression, and
is deliberately left unfixed here (see
`tests/test_castxml_clang_parity_gate.py::TestCrossProducerUnmangledIdentityKnownLimitation`,
which documents today's behavior). A sound fix needs exactly this phase's
per-fact provenance/reconciliation: matching a synthetic key against a real
mangled symbol via structural equivalence (same qualified class, compatible
signature, same access/virtuality) without risking a false match between
two coincidentally-same-signature but genuinely different entities.

**Design sketch.**

- A per-field/per-record provenance map (`{field_path: producer}`) alongside
  the merged `AbiSnapshot`, analogous to the existing per-declaration
  `source_header`/`origin` provenance (ADR-015) but keyed by *fact* rather
  than by declaration.
- A merge policy per fact: "prefer castxml, backfill from clang when castxml
  is null" for castxml-only facts (defaults, deprecated messages, abstract/
  scoped-enum/override), and the mirror for any clang-only fact Phase 4/5
  add.
- Detectors currently gated on `_both_castxml_backed`/`ast_producer` equality
  would instead check per-fact provenance for the *specific* fields they
  read, not a whole-snapshot producer tag.
- Merge must be conservative: a fact present on neither backend stays
  `None` (unknown), never silently defaulted, mirroring the tri-state
  conventions already used for `is_final`/`is_abstract`/`param.default`.

**Files & surfaces.** A new `abicheck/dumper_hybrid.py` (or a `merge_snapshots()`
helper in `dumper.py`) sitting after both `dumper_castxml.py`/`dumper_clang.py`
produce their snapshots; `AbiSnapshot` gains the provenance map; every
`_both_castxml_backed`-gated detector in `diff_types.py`/`diff_symbols.py`
needs a per-fact-provenance equivalent — this is the bulk of the migration
cost, not the merge itself.

**Out of scope for this phase.** Layout facts (offsets, vtable slots) are
not part of the merge — CastXML remains the sole layout source until
Phase 4 gives clang an independent one.

## Phase 4 — a Clang `ASTRecordLayout` plugin

**Problem.** `clang::ASTRecordLayout` (`clang/AST/RecordLayout.h`) is what
Clang's own Sema/CodeGen use internally to compute a record's *actual
compiled layout* for the target ABI — `getSize()`, `getFieldOffset(i)`,
`getBaseClassOffset()`, vtable-pointer/thunk placement, and so on. It is a
**C++ API**, reachable only from a Clang tool built with LibTooling/libclang
— not exposed by any `clang` command-line flag, and *not* exposed by
`clang.cindex`'s stable C API either (the [G4](g4-header-ast-extractor.md)
plan's `clang.cindex`-based extractor gets concepts/`explicit`/mangled
names, but no layout facts — that is a materially different capability).
Today `abicheck`'s direct-clang backend (`-ast-dump=json`) gives rich
declarations but zero layout data, which is precisely why CastXML — which
runs its own bundled Clang internally and exports the layout it computed —
remains the stronger layout source.

**Design sketch.** A standalone, small C++ tool linked against libclang/
LibTooling that parses the same headers, walks every `RecordDecl`, calls
`ASTContext::getASTRecordLayout()`, and serializes offsets/vtable-slot/
thunk info to a JSON sidecar `dumper.py` can merge in (via Phase 3's
per-fact provenance) — making the direct-clang backend a fully
self-sufficient layout source instead of depending on CastXML or DWARF
backfill.

**Why this is XL/high-risk, not a parser fix.**

- It is a **new compiled build target**, not a Python change: needs
  libclang/LibTooling dev headers, a CMake/LLVM link step, and a packaging
  story for a compiled binary living inside an otherwise "pure Python"
  tool (ADR-001's core stance) — likely an optional extra, not a default
  dependency.
- Clang's internal C++ AST API has **no cross-LLVM-release ABI stability**
  guarantee the way CastXML's versioned XML schema does; this needs a
  version-compatibility matrix (which LLVM releases the plugin is built
  against) before it can be trusted the way CastXML is today.
- Only worth doing once Phase 3's provenance-merge plumbing exists to
  actually consume a second, independent layout source usefully.

**Files & surfaces.** A new top-level tool directory (e.g. `tools/clang-layout-plugin/`,
outside the `abicheck/` Python package), a JSON sidecar schema, `dumper_clang.py`
gains an optional layout-merge step, `pyproject.toml`/packaging for the
optional extra.

## Phase 5 — concepts / `requires` / template-default normalization

**Problem.** CastXML emits `<Unimplemented kind="Concept"/>` with no body for
a C++20 concept, so concept tightening/loosening is invisible end-to-end
(`case105`), and `explicit` on a conversion operator isn't captured either
(`case106`). This is **already the primary scope of
[G4](g4-header-ast-extractor.md)** (a `clang.cindex`-based extractor
targeting exactly concepts, `explicit`, and ctor mangled names —
`case78`/`case105`/`case106`/`case111`); G4 should be read as this phase's
concrete plan rather than duplicated here.

**What is additional to G4.** Template *default-argument* normalization
(`template <typename T, typename U = T> struct Box;` — a default template
argument changing) is not currently in G4's stated scope and would need its
own design: whether it belongs in the `clang.cindex` extractor alongside
concepts/`explicit`, or is small enough to add directly to
`dumper_castxml.py` if CastXML's schema turns out to expose default
template arguments after all (unconfirmed — needs the same
schema-verification-against-real-output discipline Phase 1 used
throughout).

**Recommendation.** Pick up [G4](g4-header-ast-extractor.md) as-is for the
concepts/`explicit`/ctor-mangled-names slice; scope template-default
normalization as a small follow-up investigation once G4's extractor
exists, rather than a separate phase with its own new module.

---

## Cross-references

- [ADR-001](../adr/001-technology-stack.md) — Technology Stack (castxml vs.
  clang AST tradeoffs).
- [ADR-003](../adr/003-data-source-architecture.md) §D8/D9 — dual L2 backend
  rationale (`header_backend`/`--ast-frontend`).
- [ADR-037](../adr/037-cli-interface-contract.md) D8 — the
  `--ast-frontend {auto,castxml,clang}` flag surface.
- [G4](g4-header-ast-extractor.md) — the concrete plan for most of Phase 5.

## Out of scope

- Re-litigating Phase 0–2's already-shipped detection behavior (e.g.
  `cv_qualifiers_only_differ`'s deliberate by-value-field exclusion,
  `case30_field_qualifiers` ground truth) — those are settled product
  decisions with dedicated regression tests, not open questions.
- MSVC/PDB layout parity — tracked separately under
  [G24](g24-linux-abi-gap-closure.md)'s deferred Windows items.
