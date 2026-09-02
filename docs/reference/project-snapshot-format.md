---
doc_type: reference
audience:
  - contributor
level: expert
canonical_for:
  - project-snapshot-storage
lifecycle: active
generated: false
---

# Project Snapshot Format (`ProjectSnapshot`, v2)

> **Redesigned as a single file (Phase 8).** `dump`'s real `-o`/`--output`/
> stdout output is now this format's D8 section split (independently
> versioned `binary`/`declarations`/`types`/`layout`/`debug`/`build`/
> `graph`/`provenance` sections, structurally validated on read) packaged as
> **one JSON document** (`storage.sectioned_document`), not a directory —
> every `dump` invocation gets this by default, no flag needed.
> `serialization.snapshot_from_dict` reads either shape transparently, so an
> older flat `.abi.json` a prior build wrote stays fully readable.
>
> The directory-backed package this page otherwise describes
> ([ADR-062](../contribute/adr/062-project-snapshot-storage-v2.md)'s
> `manifest.json`/`refs/`/`objects/sha256/...` layout) still exists as a
> typed-API primitive (`project_snapshot_legacy.write_legacy_snapshot_package`)
> and `compare`/`scan --against` still accept one as an input path — but no
> `dump` CLI flag writes one today. The directory shape's real value (content
> dedup, independent per-section objects) only pays off once a project shares
> content across multiple artifacts, which nothing produces yet; for the
> single-artifact case every `dump` performs today, the single-file shape
> gets the same structural benefits without the directory's storage-UX cost
> (many small files instead of one, awkward to `scp`/commit/upload as a CI
> artifact). See the ADR's own Status for the full picture.

`ProjectSnapshot` is ADR-062's replacement for four separate persistence
shapes (per-library `.abi.json` snapshots, baseline sets, `BundleFacts`, and
embedded `BuildSourcePack` data) — one content-addressed package per
project, addressed by digest rather than embedded whole. See
[ADR-062](../contribute/adr/062-project-snapshot-storage-v2.md) for the full
design rationale and
[the storage-format-v2 plan](../contribute/plans/storage-format-v2.md) for
phasing and acceptance criteria; this page documents the on-disk shape
itself.

## Directory layout

A package is a directory (D6):

```text
project.abicheck/
  manifest.json            # small; loads immediately
  refs/variants/<variant-id>.json
  refs/artifacts/<artifact-id>.json
  objects/sha256/<aa>/<digest>.json.zst
```

`manifest.json` carries only the package's version axes (see below) and the
list of variant/artifact ids — never a full record. Each variant's or
artifact's own record lives at its own `refs/variants/<id>.json`/
`refs/artifacts/<id>.json`, loaded lazily. Section content (declarations,
types, layout, ...) is never embedded in a ref — it is a separate,
content-addressed object under `objects/`, referenced by digest.

A deterministic `.tar.zst` transport form is part of the design but **not
yet implemented** — only the plain directory form exists today.

## Content addressing

Every object under `objects/` is addressed by the SHA-256 digest of its own
*canonical form*: keys sorted, floats normalized, unordered collections
given an explicit sort key, and a reserved `capture` metadata subtree
excluded from the hash domain (D5). Two objects with identical content —
however they were produced, in whatever order their producer traversed
its own data — always address the same file; storing the same content twice
is a no-op. A JSON-shaped object is stored at
`objects/sha256/<digest[:2]>/<digest>.json.zst`; a raw binary payload (one
`ObjectStore.put()` cannot represent as JSON) is stored the same way at
`.bin.zst` instead.

## Version axes

A package's `manifest.json` states seven core version axes plus two
legacy-import provenance axes (D2) — none of them the single overloaded
`schema_version` integer `.abi.json` uses:

| Axis | Meaning |
|---|---|
| `package_format_version` | container/manifest layout |
| `section_schema_versions` | per-section field layout, keyed by section kind |
| `normalization_recipe` | how spellings/paths/identities were normalized |
| `producer` | what emitted the facts (name, version, binary digest) |
| `extractor_generation` | extraction semantics epoch |
| `resolver_generation` | derived-graph/resolution semantics epoch |
| `comparison_contract_version` | what a reader must understand to compare safely |
| `source_schema_version` / `source_producer_generation` | import provenance for a package adapted from a legacy `.abi.json` document |

`package_format_version` and `comparison_contract_version` fail closed: an
unstated or unrecognized value refuses to load rather than being treated as
"this build's own version". The rest are informational.

## Sections

An artifact's content is split into independently-addressable *sections*
(D8) — `binary`, `declarations`, `types`, `layout`, `debug`, `build`,
`source_abi`, `graph`, `provenance`, `diagnostics`, `raw_refs` are the named
vocabulary, though `ArtifactRef.sections` accepts any section kind string.

`SemanticIR` (ADR-063 Phase 6's cross-backend declaration/type
representation) is the one domain type actually promoted onto a typed,
versioned section built from a real domain object, under section kind
`"semantic_ir"`, via `abicheck/storage/dto.py`'s
`semantic_ir_to_dto`/`semantic_ir_from_dto`. **Every other field a legacy
`.abi.json` document carries is split across the rest of D8's named
sections too** (`abicheck/storage/legacy_sections.py`'s
`split_legacy_document`/`join_legacy_document`): `binary` (ELF/PE/Mach-O
container facts, `build_id`, source path/size), `declarations` (functions,
variables, enums, typedefs, constants, Python/SYCL extension surfaces),
`types` (record/class/union types), `layout` (DWARF-vs-header coherence,
conditional fields, extraction contract), `debug` (DWARF/AST toolchain
facts and their reliability flags), `build` (embedded `BuildSourcePack`
data), `graph` (the surface reachability graph), and `provenance` (library/
version identity, git/build metadata, `dump`'s own `dump_provenance`
block). Each section is independently versioned
(`storage.dto.SECTION_SCHEMA_VERSIONS`) and carries an explicit, reviewed
field allowlist — a document field with no assigned section is a hard
error at import time, not a silent drop. What this split does *not* do yet
is decode a section's own **internal** shape into a typed domain object the
way `semantic_ir` is: `elf`/`dwarf`/`build_source`/... inside their own
section still carry exactly the JSON `serialization.snapshot_to_dict()`
already produced for them. That deeper per-field typing is real,
separately-scoped future work.

Every `SectionDTO` is a small, explicit envelope:

```json
{
  "section_kind": "semantic_ir",
  "section_schema_version": 1,
  "payload": { "...": "the section's own explicit, hand-encoded content" }
}
```

`section_schema_version` is the DTO's own version — independent of every
other axis above — and a per-section-kind migration chain
(`abicheck/storage/dto.py`'s `migrate_section_dto`) advances an older
payload to the current version one registered step at a time. No section's
`payload` is ever built via `dataclasses.asdict()`: each is written by an
explicit, hand-authored encoder (`storage/semantic_ir_codec.py`'s
`semantic_ir_to_document`, for the one section that exists today), enforced
mechanically by `scripts/check_ai_readiness.py`'s
`project-snapshot-dto-no-asdict` check.

## Importing a legacy snapshot

`abicheck/storage/import_v1.py`'s `import_legacy_snapshot` takes one
already-serialized `.abi.json`-shaped document (any schema version this
build can still read) and produces a one-artifact, one-variant
`PackageManifest` — a single-library dump represented as a minimal project.
No existing baseline is rewritten: the adapter only ever reads a document
and builds new, additional structures from it. `export_legacy_snapshot` is
the exact inverse: given an `ArtifactRef` and the `ObjectStore` it was
written into, it reads every section back, migrates each to its current
version, and reassembles the original document.

## CLI wiring

`storage.sectioned_document`'s `to_sectioned_document`/
`from_sectioned_document` package the same D8 split as one JSON document,
reused by `serialization.snapshot_to_json`/`snapshot_from_dict` (the real
`-o`/`--output`/stdout write and read path) so every `dump`/`compare`/`scan`
invocation gets it by default. `abicheck/project_snapshot_legacy.py`'s
`write_legacy_snapshot_package`/`read_legacy_snapshot_document` remain the
real, directory-backed round trip built on the primitives above
(`DirectoryObjectStore` + `import_legacy_snapshot`/`export_legacy_snapshot`
+ `write_project_manifest`), reached through `abicheck.workflows.storage`'s
facade re-export (`frontends -> workflows -> storage`, ADR-061's layering):

- **No `dump` CLI flag writes a directory package today** — every
  `dump` invocation writes the single-file sectioned shape instead (see
  above). The directory writer stays available as a typed-API primitive
  for a caller that wants it directly.
- **`compare`/`scan --against`** still accept a `ProjectSnapshot` package
  directory as an input path — detected by a real, validated
  `manifest.json` read (`is_project_snapshot_package_dir`, which
  distinguishes it from a `BuildSourcePack`'s own identically-named
  `manifest.json`, and from a plain directory-of-libraries `compare`
  operand, which stays routed to the release/bundle fan-out). Resolved by
  `workflows.input_resolution.resolve_input`'s directory branch into the
  identical in-memory `AbiSnapshot` a `.abi.json`/sectioned file resolves
  to, so every downstream detector, report, and exit code behaves the same
  regardless of which of the three shapes the input actually is.

## Related

- [ADR-062](../contribute/adr/062-project-snapshot-storage-v2.md) — the design decision this format implements
- [Storage format v2 plan](../contribute/plans/storage-format-v2.md) — phasing, acceptance criteria, what remains open
- [Snapshot Format (`.abi.json`)](snapshot-format.md) — the flat legacy shape `snapshot_from_dict` still reads unchanged
