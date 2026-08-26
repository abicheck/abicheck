# AGENTS.md — `abicheck/storage/`

## Purpose

This package owns persistence: snapshot and baseline serialization, the
schemas and migrations those formats carry, content addressing, and cache
management, per ADR-061 D1. It answers "how is a fact stored, identified,
versioned, and read back" — never "what that fact means" or "whether a
comparison is valid".

Two bodies of work live here and are deliberately independent:

- **G40's bundle archive** — a content-addressed zip container for
  already-computed `BundleFacts`.
- **[ADR-062](../../docs/contribute/adr/062-project-snapshot-storage-v2.md)'s
  Phase 0 primitives** — the availability, identity, canonical-encoding and
  versioning vocabulary a project-scale format is built on.

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
bundle-archive modules import only `abicheck.errors` (`SnapshotError`, the
project-wide error vocabulary) and no `model`/`compare` type at all — see
`bundle_archive.py`'s own docstring for why: the `BundleFacts`/`AbiSnapshot`-
aware glue that would need a `model` import stays in `bundle_facts.py`/
`serialization.py` (still flat-root, not yet part of this migration) rather
than being pulled in here prematurely, since `bundle_facts.py` itself cannot
yet join `model` cleanly (a pre-existing `TYPE_CHECKING`-only coupling to
`checker_types.DiffResult` would create a `model -> compare -> model` cycle —
confirmed by running `scripts/check_architecture.py`, not assumed). The
ADR-062 primitives import nothing from `abicheck` at all, which is what lets
them ship ahead of the `abicheck/model` package that the flat
`abicheck/model.py` has not yet migrated into.

A future module that genuinely needs a `model`-owned type should import it
directly once that type has actually joined `model` — not via
`serialization.py`.

## Canonical entry points

### Bundle archive (G40)

`bundle_archive.py`'s `BundleArchiveWriter`/`BundleArchiveReader` are a
pure, content-addressed zip-container primitive — write/read a manifest plus
content-hash-addressed blobs, nothing more.
`bundle_archive_cd_guard.py`'s `reject_absurd_central_directory` is its own
central-directory bomb guard, called from `BundleArchiveReader.__init__`
before `zipfile.ZipFile` ever parses the archive. Callers that want a real
`BundleFacts` written to or read from one of these archives go through
`serialization.py`'s `save_bundle_facts`/`load_bundle_facts`
(`format="archive"`), which delegates the actual glue to `bundle_facts.py`
(still flat-root), the module that already owns the `BundleFacts`-to-dict
conversion this format's blobs are built from.

### ADR-062 Phase 0 primitives

Four independent primitives. Each is consumed directly by its implementation
module — there is no service locator, and `__init__.py` is a narrow
re-export surface, not a namespace to import through internally.

| Module | Owns |
|---|---|
| `availability.py` | `FactStatus`/`FactAvailability`/`AvailabilityLedger` — why a fact is or is not present (ADR-062 D3) |
| `identity.py` | `EntityId`/`OccurrenceId`/`OccurrenceSet`/`IdentityConflict` — logical vs. observed identity, with multiplicity preserved (D4) |
| `canonical.py` | `canonical_form`/`canonical_json`/`semantic_digest` — the one canonical logical encoding (D5) |
| `versioning.py` | `StorageVersions`/`ProducerIdentity`/`check_reader_compatibility` — the separated version axes (D2) |

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

## Tests

`tests/test_bundle_archive.py` (the core archive primitive) and
`tests/test_bundle_archive_writer_hardening.py` (`BundleArchiveWriter`'s
temp-file/security/metadata hardening — split out purely to keep both under
the ADR-061 1200-line test cap); `tests/unit/storage/` for the ADR-062
primitives.

A reusable primitive here gets a property-style test class stating its
contract as invariants — see the root `AGENTS.md` "Primitive-level property
tests" section for why example-only tests are not sufficient for
merge/dedupe/grouping primitives.

## Prohibited responsibilities

This package must not parse a binary, run a comparison, evaluate policy, or
know what an `AbiSnapshot`'s fields mean — it stores and retrieves bytes a
caller already produced, addressed by content hash. A caller needing this
package to interpret its own payload is a sign the interpretation belongs in
the calling layer, not here.
