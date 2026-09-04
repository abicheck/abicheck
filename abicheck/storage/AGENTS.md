# AGENTS.md — `abicheck/storage/`

## Purpose

This package owns persistence: snapshot and baseline serialization, the
schemas and migrations those formats carry, content addressing, and cache
management, per ADR-061 D1. It answers "how is a fact stored, identified,
versioned, and read back" — never "what that fact means" or "whether a
comparison is valid".

Three bodies of work live here and are deliberately independent:

- **G40's bundle archive** — a content-addressed zip container for
  already-computed `BundleFacts`.
- **[ADR-062](../../docs/contribute/adr/062-project-snapshot-storage-v2.md)'s
  Phase 0 primitives** — the availability, identity, canonical-encoding and
  versioning vocabulary a project-scale format is built on.
- **ADR-063 Phase 0's `Fact[T]` snapshot codec** — `fact_codec.py`/
  `enum_codec.py`, `serialization.py`'s codec helpers; excluded from the
  ADR-062 sweeps (`adr062_scope.py`'s `NON_ADR062_MODULES`), not re-exported.

[ADR-059](../../docs/contribute/adr/059-compressed-snapshot-storage.md)'s
physical envelope (compression detection, atomic writes, decompression-bomb
limits) stays in `abicheck/snapshot_io.py` and is *not* reimplemented here.

## Permitted imports

Per ADR-061 D1, `storage/` may depend only on `model`, plus the public root
surfaces (`abicheck.api_types`, `abicheck.errors`). It may not import
extraction, comparison, policy, workflow, report, or frontend modules — a
storage module that needs to know a verdict, a `ChangeKind`, or a CLI flag
is in the wrong layer. `scripts/check_architecture.py` enforces this.

In practice today the package imports even less than that allows. The
bundle-archive modules import only `abicheck.errors` and no `model`/`compare`
type at all — the `BundleFacts`-aware glue that would need one stays in
`bundle_facts.py`/`serialization.py` (still flat-root), which cannot yet join
`model` cleanly; see `bundle_archive.py`'s own docstring for the cycle that
blocks it. The ADR-062 primitives import nothing from `abicheck` at all,
which lets them ship ahead of the `model` package the flat `model.py` has not
yet migrated into. A future module needing a `model`-owned type imports it
directly once that type has joined `model` — not via `serialization.py`.

## Canonical entry points

### Bundle archive (G40)

`bundle_archive.py`'s `BundleArchiveWriter`/`BundleArchiveReader` are a pure,
content-addressed zip-container primitive — write/read a manifest plus
content-hash-addressed blobs, nothing more. `bundle_archive_cd_guard.py`'s
`reject_absurd_central_directory` is its central-directory bomb guard, called
from `BundleArchiveReader.__init__` before `zipfile.ZipFile` parses anything.
Callers wanting a real `BundleFacts` in one of these archives go through
`serialization.py`'s `save_bundle_facts`/`load_bundle_facts`
(`format="archive"`), which delegates the glue to `bundle_facts.py`.

### ADR-062 Phase 0 primitives, plus Phase 1's object model, DTO layer, and import adapter

Seven primitives (four Phase 0, three Phase 1), plus the internal `guards.py`; each is consumed directly by its own module — no service locator; `__init__.py` is a re-export surface only.

| Module | Owns |
|---|---|
| `availability.py` | `FactStatus`/`FactAvailability`/`AvailabilityLedger` — why a fact is or is not present (D3) |
| `identity.py` | `EntityId`/`OccurrenceId`/`OccurrenceSet`/`IdentityConflict` — logical vs. observed identity, multiplicity preserved (D4) |
| `canonical.py` | `canonical_form`/`canonical_json`/`semantic_digest` — the one canonical logical encoding (D5) |
| `versioning.py` | `StorageVersions`/`ProducerIdentity`/`check_reader_compatibility` — the separated version axes (D2) |
| `package.py` (+ `ref_ids.py`, internal) | `PackageManifest`/`VariantRef`/`ArtifactRef`/`ObjectRef`/`ObjectStore`/`InMemoryObjectStore` — D6/D7's package object model and path layout (plan A1.1); `ref_ids.py` is its split-out cross-platform ref-id safety leaf. The directory-backed `ObjectStore` lives outside this package — `abicheck/project_snapshot_store.py`'s `DirectoryObjectStore` — since this package may import only `model` |
| `dto.py` | `SectionDTO`/`migrate_section_dto`/`semantic_ir_to_dto`/`semantic_ir_from_dto` — A1.1's per-section DTO envelope, jointly ADR-063 Phase 8's D8 constraint (a distinct, versioned, explicitly-encoded class per section, never `asdict`; `scripts/check_ai_readiness.py`'s `project-snapshot-dto-no-asdict` check enforces it mechanically) |
| `import_v1.py` | `import_legacy_snapshot`/`export_legacy_snapshot` — the v1-v25 import adapter (A1.2/A1.3): one already-serialized legacy document, reshaped into a one-artifact `PackageManifest`, and its exact inverse |
| `import_bundle_facts.py`, `import_baseline_set.py` | A1.4 (ADR-063 Track C 8B): fold a G38 `BundleFacts` document (resp. a baseline set's `manifest.json` + snapshots) onto `VariantRef.sections`, calling `import_v1` once per library |
| `guards.py` | the value guards all seven apply at their doors — internal, not re-exported (invariant 6) |

## Invariants this package must not break

1. **Never resolve identity by discarding an occurrence.** Ambiguity is
   recorded and both occurrences are kept. The first-wins behavior in
   `AbiSnapshot.index()` is exactly what D4 exists to replace; do not
   reproduce it here.
2. **Never decide, in this package, whether two observations contradict
   each other.** That needs to know what an attribute *means*, which is
   domain knowledge this layer does not hold. `OccurrenceSet` reports the
   structural fact (`same_site_observations`); the caller supplies the
   predicate (`conflicts`). Three review rounds were spent adding one more
   dimension to a site tuple before this was separated — treat a fourth
   proposed dimension as evidence the question is in the wrong layer again.
3. **Never let a default value stand in for missing evidence.** An empty
   collection means "the producer ran and established nothing is there".
   Anything else needs a `FactAvailability` status.
4. **Never make a semantic digest depend on incidental order.** This is a
   claim about the stored *state*, not only about accessors: a canonical
   view over non-canonical state leaves `__eq__` and `repr` exposed. Unordered
   collections get an explicit sort key; anything whose order carries
   meaning is an array, never a map relying on insertion order.
5. **Never add a fifth meaning to one version integer.** A new kind of
   compatibility question gets its own axis in `StorageVersions`.
6. **Never coerce a value a decision reads.** `str()` on a field that
   identifies something is silently lossy in the one way that matters: `1`
   and `"1"` become the same value, so two things a package distinguished
   stop being distinguishable, and where the field is a mapping key,
   iteration order decides which record survives. Reject instead. This
   applies to identity fields, mapping keys, ledger family names, override
   key halves, and provenance (`producer`/`recipe` decide whether two
   `PRESENT` records are interchangeable). Informational version axes are
   the deliberate exception — no decision reads them, and this repo's
   convention is that a hand-edited package must not abort a load.

   **The guards live in `guards.py`** — `identity_text`, `decision_key`,
   `provenance_text`, `diagnostics_from`, `mapping`. They were one copy per
   module until review found *seven* separate sites missing a check its
   siblings already had, one at a time. Add a new guard there, not at a
   call site.

   `canonical_form` keeps its own mapping-key rejection: it is the *format*
   refusing to encode a key it cannot round-trip, decided by the value's
   type at encode time, not a named field a caller passes — and its message
   is about the document being unencodable rather than about a field.

   The rule reaches further than the value in a field. A **container** a
   decision looks up in must be checked before its keys: a list, a tuple and
   a string all iterate into values that pass every key guard, so
   `AvailabilityLedger(families=["layout"])` constructed and only failed at
   the first real lookup. A **record slot** must be checked where it is
   assigned, not read, or a non-record surfaces from inside whichever
   decision consults it. And a `from_dict` must refuse a non-mapping
   *cleanly* — a caller separating "malformed package" from "broken reader"
   catches `TypeError`/`ValueError`, so an `AttributeError` escaping `.get`
   reads as the second when it is the first. Where a `from_dict` degrades
   instead of refusing (the informational axes), it must degrade to what an
   empty document parses to, never to the dataclass defaults: those are the
   current *writer's* versions, so a malformed versions block would have
   read as "written by this exact build" and passed `check_reader_compatibility`.

## Tests

`tests/test_bundle_archive.py` and
`tests/test_bundle_archive_writer_hardening.py` (`BundleArchiveWriter`'s
temp-file/security/metadata hardening — split out to keep both under the
ADR-061 1200-line test cap); `tests/unit/storage/` for the ADR-062
primitives. A reusable primitive here gets a property-style test class
stating its contract as invariants — see the root `AGENTS.md`
"Primitive-level property tests" for why example-only tests are not
sufficient for merge/dedupe/grouping primitives.

## Prohibited responsibilities

This package must not parse a binary, run a comparison, evaluate policy, or
know what an `AbiSnapshot`'s fields mean — it stores and retrieves bytes a
caller already produced, addressed by content hash. A caller needing this
package to interpret its own payload is a sign the interpretation belongs in
the calling layer, not here.
