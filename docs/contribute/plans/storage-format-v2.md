---
doc_type: contributor
level: advanced
lifecycle: active
---

# Storage format v2 — project packages, occurrence-preserving identity, and explicit evidence availability

**Origin:** A structural review of the storage layer at
`b299afd` (post-#861 multibuild fingerprint work), asking whether the
current format is the right long-term model for real projects,
multi-library releases, and multibuild matrices.

**ADR:** [ADR-062](../adr/062-project-snapshot-storage-v2.md). ADR-059's
physical envelope is kept unchanged; ADR-015's single-document logical
model is what v2 replaces.

**Type:** Initiative plan (cross-cutting). Touches `abicheck/storage/`
(new), `abicheck/serialization.py`, `abicheck/snapshot_io.py`,
`abicheck/model.py`, `abicheck/bundle_facts.py`, `abicheck/bundle_manifest.py`,
`abicheck/bundle_multibuild.py`, `abicheck/bundle_variants_config.py`,
`abicheck/snapshot_cache.py`, `abicheck/comparability.py`, and the
`dump`/`compare`/`scan` front ends.

**Effort:** XL (phased). **Risk:** Phase 0 is additive and inert (new leaf
primitives, no producer or reader wired). Phase 1 introduces a second
storage model behind an opt-in and a permanent import adapter. Phase 2 is
performance and cache work over the Phase 1 model.

**Relationship to [G38](g38-bundle-facts-model-and-multibuild-comparability.md):**
G38 owns *what bundle-level and multibuild comparisons must answer* — the
finding taxonomy, persisted `BundleFacts`, variant pairing that never
unions, and the C-boundary signature gate. This plan owns *how any of that
is stored*. They are complementary and must not both grow a container
format: if G38's Phase 2 lands first it targets today's `BundleFacts`
document and becomes a section under D6 later; if this plan's Phase 1 lands
first, G38's Phase 2 targets the `ProjectSnapshot` package directly. Do not
implement a third persisted bundle shape.

---

## Problem

The current format is a strong v1 single-library interchange format with a
production-grade compression and I/O layer, and a transitional multi-binary
container. It is not yet the right project-scale storage architecture. The
full evidence is in [ADR-062](../adr/062-project-snapshot-storage-v2.md)'s
Context; the seven load-bearing findings are:

| # | Finding | Consequence |
|---|---|---|
| 1 | Whole-document construct/load (`asdict()` deep copy, one parsed document) | Peak memory scales with the whole release, not the compared pair; a large `BundleFacts` can approach the 1 GiB decoded ceiling |
| 2 | First-wins identity in `index()`, bare-name `typedefs`, one-`ElfSymbol` `symbol_map`, `name -> offset` bases | Real, valid evidence is silently dropped from lookup |
| 3 | One `SCHEMA_VERSION` carrying layout + producer epoch + reliability + comparison contract | Every historical producer defect needs a new special case |
| 4 | `False`/`[]`/`None` conflating missing, unsupported, failed, and genuinely-absent | Safety inferred from an empty collection |
| 5 | Canonical form not fully specified (list order significant to the hash; a map whose insertion order carries template-argument order) | Determinism is assumed, not checkable |
| 6 | Shared build/source/graph evidence embedded per library | ~57-59 MB graph repeated per artifact in a multi-library release |
| 7 | Multibuild modelled but not captured; four overlapping persistence shapes | No coherent multi-variant baseline from a normal workflow |

**Explicit non-goal:** solving any of this by storing less evidence, or by
replacing JSON with a new binary codec. Compression already handles the
volume (~264 MB → ~7.7 MB on oneDAL). The cost is construction, retention,
duplication, and lost meaning.

---

## Goal and acceptance criteria

### Phase 0 — correctness and schema-stability primitives

Additive leaf primitives in a new `abicheck/storage/` package, with no
producer or reader wired to them, so every existing snapshot, baseline set,
and `BundleFacts` document is bit-for-bit unchanged.

- **A0.1** `FactAvailability` distinguishes `present` / `partial` /
  `not_collected` / `unsupported` / `failed` / `not_applicable`, carries
  producer, recipe, scope, confidence, and diagnostics, and answers
  "may a comparison rely on this?" as one predicate rather than a
  per-call-site convention. An `AvailabilityLedger` resolves a
  family-level default against per-entity overrides.
- **A0.2** `EntityId` and `OccurrenceId` are separate types. An
  `OccurrenceSet` groups occurrences under an entity **without dropping
  any**, and records an `IdentityConflict` where occurrences cannot be
  reconciled — the replacement for today's warn-and-keep-the-first.
- **A0.3** ELF symbol occurrences are keyed by (artifact, name, version,
  default-ness, binding, type, visibility, definition status), so two
  versioned definitions of one bare name are two occurrences.
- **A0.4** Canonical encoding: unordered collections carry an explicit
  stable sort key, ordered collections are arrays, capture metadata is
  excluded from the semantic-hash domain via one reserved slot at the
  document root (never by key name at arbitrary depth, which cannot tell a
  hostname from a platform or a `pid` field from a process id), and the
  digest is invariant under key order, insertion order, and pretty-printing.
- **A0.5** The version axes of ADR-062 D2 are separate fields. Two fail
  closed — `package_format_version` (the reader may not locate the
  structures) and `comparison_contract_version` (the verdict could be
  wrong) — and **both** also fail closed on a value the package does not
  state validly: absent, malformed, non-integral or non-positive alike,
  re-checked where the reader decides rather than trusted from the
  deserializer. The remaining five are informational and parse defensively.
  Imported legacy snapshots preserve their source schema/producer
  generation.
- **A0.6** Property-style tests state each primitive's contract as
  invariants (not only example cases), per the root `AGENTS.md`
  "Primitive-level property tests" guidance.

**Status: implemented** (this change). See "Landed in Phase 0" below.

### Phase 1 — unified project and multibuild storage

**Status: A1.1 partially implemented** (this change) — the object model
(`PackageManifest`/`VariantRef`/`ArtifactRef`/`ObjectRef`/`ObjectStore`) is
landed; the directory-backed store that actually reads and writes it is not.
See "Landed in Phase 1 (partial)" below.

- **A1.1** `ProjectSnapshotStore` reads and writes the D6 layout over a
  directory abstraction, with a deterministic `.tar.zst` transport form.
- **A1.2** A v1-v25 import adapter maps every existing snapshot into the v2
  in-memory model, preserving source schema and producer generation. No
  existing baseline is rewritten in place.
- **A1.3** A single-library snapshot is representable as a one-artifact
  project, and round-trips through the store unchanged at the semantic-digest
  level.
- **A1.4** Baseline sets and `BundleFacts` are expressed as sections of one
  package rather than parallel top-level formats.
- **A1.5** `BuildSourcePack`, project source graphs, and toolchain profiles
  are stored once per project/variant and referenced by digest.
- **A1.6** `bundle_variants:` is wired into `.abicheck.yml` discovery, the
  capture pipeline is told which variant it is producing, and both declared
  and captured coordinates are stored and verified.
- **A1.7** Stored/live and stored/stored release comparison is reachable
  from the standard CLI.
- **A1.8** Non-ELF artifacts (PE, Mach-O, Python-visible, header-only) are
  retained as project members with bundle-level *resolution* declared as an
  ELF-only capability rather than silently excluded.

### Phase 2 — scale and performance

- **A2.1** Section/chunk lazy loading; a comparison loads two manifests, all
  L0 binary sections, then one library pair at a time.
- **A2.2** Streaming object encoding replaces whole-document `asdict()` +
  one JSON string.
- **A2.3** The snapshot cache moves onto the content-addressed store, keyed
  per ADR-062 D11, with a byte-quota LRU.
- **A2.4** Optional, rebuildable SQLite indexes; never canonical truth.
- **A2.5** **Measured**: peak RSS for a project comparison no longer scales
  with the total decoded size of the package.

---

## Phases

### Phase 0 (implemented)

New `abicheck/storage/` package — the ADR-061 `storage` layer, which was
declared in `architecture/modules.yaml` but had no package yet. Its modules
are leaves: they import nothing from `abicheck` except the public root
surfaces, so the layer's dependency direction is satisfied trivially and
nothing in the existing pipeline changes behavior.

### Phase 1

1. `storage/package.py` — manifest, refs, and the object-store abstraction.
   **Object model landed** (this change); the directory-backed store
   implementing `ObjectStore` against real files is still open.
2. `storage/import_v1.py` — the v1-v25 adapter (A1.2), including the
   `*_facts_reliable` flags becoming `FactAvailability` records.
3. Express a single-library dump as a one-artifact project (A1.3), behind
   an opt-in writer flag.
4. Fold baseline sets and `BundleFacts` into sections (A1.4/A1.5),
   coordinating with G38 Phase 2 so only one persisted bundle shape exists.
5. Variant capture and CLI wiring (A1.6/A1.7).

`AvailabilityLedger.declare` and `.override` rebuild, revalidate, and
re-sort the whole mapping per call, so building a ledger of *n* overrides
costs O(n² log n) (CodeRabbit review). That is deliberate for Phase 0,
where nothing calls them in a loop and the validating, canonically-ordered
reassignment is what makes the ledger's state impossible to corrupt in
place. Step 3 above is where it starts to matter — the first producer that
calls `override` per entity — so the container decision belongs with that
producer, which knows the insertion pattern, rather than being guessed at
now. Whatever replaces it must keep both properties the current shape buys:
every stored key validated, and iteration order a function of content
rather than of insertion.

### Phase 2

Lazy loading, streaming encode, cache migration, indexes, transport, and
the measurement work that sets D8's chunk size and D12's level table.

---

## Validation corpus

A storage redesign is not complete on round-trip unit tests alone. These
are acceptance tests, not nice-to-haves.

**Identity preservation.** Two same-bare-name typedefs in different
classes; several versions of one ELF symbol; mixed `GLOBAL`/`WEAK` bindings
across versions; repeated and virtual base subobjects; TU-local functions
and variables with identical spellings; uninstantiated template methods
with identical bare names; MSVC functions whose decorated identity differs
only by return type; one source declaration observed by Clang, CastXML,
DWARF, and PDB.

**Determinism.** Randomizing producer traversal, dictionary insertion, TU
completion, and parallel extraction order must produce the same semantic
digests, the same package root digest, and the same comparison result.
Pretty-printed bytes need not match across output styles; semantic digests
must.

**Stored-versus-live parity.** For every bundle-level finding family, all
four of `live/live`, `stored/live`, `live/stored`, and `stored/stored` must
produce equivalent findings, verdicts, evidence status, and attribution.

**Real-project scale.** oneDAL (multi-library, CPU and DPC variants); a
template-heavy C++ project (LLVM/Clang, Qt, Boost, or PyTorch C++); a
symbol-version-heavy C project (OpenSSL); a Windows DLL/PDB project; a
Mach-O dylib or framework; a pybind11 extension; a header-only target mixed
with compiled libraries. Record decoded size, stored size, deduplication
ratio, peak RSS, write time, manifest load time, full compare time, and
bytes/objects actually loaded.

---

## Out of scope

- A new binary or columnar wire encoding inside an object.
- Dropping any evidence layer to reduce size.
- New bundle-resolution semantics for PE/Mach-O (D6 requires membership and
  capability declaration, not a new resolver).
- The `ChangeKind`/report-schema consequences of newly explicit
  `NOT_COMPARABLE` outcomes (ADR-050/ADR-042 surfaces).
- Making the SQLite index mandatory.

---

## Landed in Phase 0

| Module | Contract |
|---|---|
| `abicheck/storage/availability.py` | `FactStatus`, `Confidence`, `FactAvailability`, `AvailabilityLedger` (A0.1) |
| `abicheck/storage/fact_availability.py` | `FactAvailability` — internal record leaf, re-exported by `availability.py` |
| `abicheck/storage/availability_status.py` | `FactStatus`, `Confidence`, `COMPARABLE_STATUSES`, `GAP_STATUSES`, `ASSERTS_NO_PRODUCER`, `STATUS_ORDER`, `CONFIDENCE_ORDER`, `worse_status`, `worse_confidence` — internal vocabulary leaf, re-exported by `availability.py` only for `FactStatus`/`Confidence` |
| `abicheck/storage/identity.py` | `EntityKind`, `ObservationKind`, `EntityId`, `OccurrenceId`, `OccurrenceSet`, `IdentityConflict`, `elf_symbol_occurrence` (A0.2/A0.3) |
| `abicheck/storage/entity_ids.py` | `EntityId`, `EntityKind`, `ObservationKind`, `OccurrenceId`, `elf_symbol_occurrence` — internal identifier leaf, re-exported in full by `identity.py` |
| `abicheck/storage/canonical.py` | `canonical_form`, `canonical_json`, `semantic_digest`, `strip_capture_metadata`, `CAPTURE_METADATA_KEY` (A0.4) |
| `abicheck/storage/versioning.py` | `PACKAGE_FORMAT_VERSION`, `COMPARISON_CONTRACT_VERSION`, `UNSTATED_VERSION`, `StorageVersions`, `ProducerIdentity`, `ReaderCompatibility`, `check_reader_compatibility` (A0.5) |
| `abicheck/storage/guards.py` | `identity_text`, `binary_buffer`, `decision_key`, `key_collection`, `required_field`, `row_sequence`, `item_iterable`, `provenance_text`, `diagnostics_from`, `mapping`, `enum_member`, `instance_of` — internal, not re-exported by the package |

`guards.py` is the one row the package does not re-export: it holds the value
guards each module used to restate at its own doors. Three copies of one rule
is how the rule drifts, and review found that drift four separate times on
this branch — always as one site missing a check its siblings already had —
so `AGENTS.md` invariant 6's deferred unification was done here rather than
in Phase 1. Its surface is still pinned by the same table, because "internal"
describes who imports it, not whether it may go unadvertised.

Each row is exactly that module's `__all__`, and
`tests/unit/storage/test_landed_surface.py` asserts it — a row advertising a
primitive that no longer exists (`VOLATILE_KEYS`, replaced by
`CAPTURE_METADATA_KEY`) sent a reviewer looking for an API that was never
there, so the table is checked rather than maintained by hand.

Tests live in `tests/unit/storage/`, stating each primitive's contract as
invariants alongside the example cases (A0.6).

## Landed in Phase 1 (partial): A1.1's object model

The first Phase 1 slice — the object model half of "1. `storage/package.py`
— manifest, refs, and the object-store abstraction" — is implemented.

| Module | Contract |
|---|---|
| `abicheck/storage/package.py` | `MANIFEST_RELPATH`, `SECTION_KINDS`, `ObjectRef`, `VariantRef`, `ArtifactRef`, `PackageManifest`, `ObjectStore`, `InMemoryObjectStore`, `object_relpath`, `variant_ref_relpath`, `artifact_ref_relpath` (A1.1) |

`ObjectRef`/`VariantRef`/`ArtifactRef`/`PackageManifest` are the in-memory
document model of D6's `manifest.json` plus the ref documents it names.
`ObjectStore` is D7's digest-addressed `put`/`get`/`has` abstraction, kept a
`Protocol` rather than a filesystem client: this migrated layer may import
only `model` (`storage/AGENTS.md`'s "Permitted imports"), so it cannot itself
wrap ADR-059's `snapshot_io.py` envelope — a concrete, `.tar.zst`-transportable
store is a separate implementation, outside this package, built over both
this module and `snapshot_io`. `InMemoryObjectStore` is the one reference
implementation this module ships, exercised by its own tests and usable
on its own for a one-process comparison that never needs to persist a
package.

**Not yet implemented, and still open**: an actual directory-backed
`ObjectStore`/`ProjectSnapshotStore` (A1.1's other half — "reads and writes
the D6 layout over a directory abstraction, with a deterministic `.tar.zst`
transport form"), the v1-v25 import adapter (A1.2), expressing a single-library
dump as a one-artifact project (A1.3), and everything after it in the Phase 1
list above. `PackageManifest.variant_refs`/`.artifact_refs` embed full
records rather than pointers to on-disk `refs/*.json` files, because there is
no writer yet to make that split meaningful — see the module's own docstring.

**A known, deliberately deferred gap** (flagged in review, Codex): `ArtifactRef.sections`
has no accompanying D3 `FactAvailability`/`AvailabilityLedger` per section, so an
absent section key cannot yet distinguish "not collected" from "unsupported"
from "failed" the way a real producer will eventually need to report. Not
closed here because no producer populates a section yet and D8's per-section
content schemas don't exist yet either — wiring D3's vocabulary in now would
mean guessing at a shape (per-artifact? per-section-kind?) with nothing real
to validate the guess against, exactly the premature-design risk this file's
own "Known gaps over risky reactive patches" convention warns about. Revisit
once A1.4/A1.5 (folding sections/`BundleFacts`) or the first real section
producer gives this a real caller to design against.

Tests live in `tests/unit/storage/test_project_package.py`, following the same
property-style-plus-example-cases convention as Phase 0 (A0.6/A1's
"Validation corpus" identity-preservation cases, applied at the level this
module actually operates at: a manifest's own `to_dict`/`from_dict` round
trip, digest stability, and the D6 path-layout functions).

### Documentation ownership — deliberately not registered yet

`docs/AGENTS.md` requires every **new public-facing feature or surface** to
register a topic in `docs/_meta/topics.yaml` in the same PR, and a reviewer
asked why storage v2 has none (Codex review). The answer is that neither
Phase 0 nor this A1.1 slice adds such a surface: no CLI command or flag, no
report field, no config namespace, no Action input, and nothing in the
product produces, consumes, or persists a byte through these primitives —
`SCHEMA_VERSION` is unchanged and every existing document is byte-for-byte
unchanged. `package.py` itself performs no filesystem or network I/O; its
`ObjectStore` is a protocol plus one in-memory reference implementation, not
a place any real package is written to or read from yet. They are also not
part of the documented Python API: `abicheck/__init__.py` does not re-export
them, and the `python-api` topic's `fact_sources` name the `service*` modules
that page actually describes.

The registry's `canonical_page` is required to be the one *published*
narrative page a human reads, and every registered topic points at one under
`learn/`, `use/`, `reference/` or `integration/` — never at `contribute/`.
Writing such a page now would mean documenting, to users, an API they cannot
reach. The ADR and this plan are the contributor-facing owners in the
meantime, which is what `contribute/` is for.

**The trigger is concrete rather than "later":** the PR that first *persists*
a `ProjectSnapshot` — Phase 1's package writer — is the one that makes this
user-facing, and it registers:

```yaml
  project-snapshot-storage:
    canonical_page: reference/project-snapshot-format.md
    fact_sources:
      - abicheck/storage/availability.py
      - abicheck/storage/fact_availability.py
      - abicheck/storage/availability_status.py
      - abicheck/storage/identity.py
      - abicheck/storage/entity_ids.py
      - abicheck/storage/canonical.py
      - abicheck/storage/versioning.py
```

alongside the page itself. Recorded here so a future reader finds a decision
rather than an omission.

**Deliberately not done in Phase 0**, so that no existing behavior changes:
nothing produces, consumes, or persists these types yet; `AbiSnapshot.index()`
still resolves first-wins; `SCHEMA_VERSION` is untouched; and no CLI
surface, report field, or exit code moves.
