# ADR-062: Project Snapshot Storage v2 — Content-Addressed Sections, Explicit Fact Availability, and Occurrence-Preserving Identity

**Date:** 2026-08-26
**Status:** Proposed — partially implemented. Phase 0 primitives implemented (`abicheck/storage/`:
fact availability, entity/occurrence identity with conflict preservation,
canonical encoding and semantic digest, and the separated version axes).
Phase 1's A1.1-A1.3 are implemented: `abicheck/storage/package.py` carries
the `ProjectSnapshot` manifest/ref/object-store *object model*
(`PackageManifest`, `VariantRef`, `ArtifactRef`, `ObjectRef`, the `ObjectStore`
protocol, and the D6 path-layout functions); `abicheck/project_snapshot_store.py`
is a real, directory-backed `ObjectStore` implementation
(`DirectoryObjectStore`) plus a manifest/ref writer and reader over ADR-059's
physical envelope — everything in D6's layout except the `.tar.zst` transport
form, which remains open; `abicheck/storage/import_v1.py`'s
`import_legacy_snapshot` is the v1-v25 import adapter, expressing a
single-library legacy document as a one-artifact, one-variant project that
round-trips through the store at the semantic-digest level. Landed jointly
with ADR-063 Phase 8, whose own D8 constraint (`abicheck/storage/dto.py`'s
`SectionDTO` — a distinct, versioned, explicitly-encoded class per section,
never `asdict`) this satisfies for the one domain type actually promoted onto
a typed representation so far: `semantic_ir`/`semantic_ir_conflicts`. Every
other legacy field still travels as one opaque, unmigrated document object —
D8's full per-category section split (folding baseline sets/`BundleFacts`
into sections, variant capture/CLI wiring, and everything else A1.4-A1.8
name) is not implemented. Still, no producer, reader, or comparison path this
build ships to users consumes any of this yet, so every existing snapshot,
baseline set, and `BundleFacts` document remains bit-for-bit unchanged. All
of Phase 2 is not implemented.
**Decision maker:** abicheck maintainers
**Supersedes (partially):** [ADR-015](015-snapshot-serialization.md)'s
single-document logical model. [ADR-059](059-compressed-snapshot-storage.md)'s
physical envelope is **kept unchanged** and explicitly not superseded.

## Context

The snapshot format has grown from a single-library JSON dump into the
persistence layer for a whole product line. Four distinct persistence shapes
now coexist:

1. per-library `.abi.json[.zst]` snapshots (`AbiSnapshot`, schema v25);
2. baseline sets (`manifest.json` plus per-library snapshots, sometimes with
   staged binaries);
3. `BundleFacts`, which embeds every per-library snapshot in one JSON
   document (`BUNDLE_FACTS_SCHEMA_VERSION = 1`);
4. `BuildSourcePack`, which may be embedded inside each snapshot or stored
   out of band.

Each is individually reasonable. Together they give more than one answer to
"what is the baseline for this project", "where does release membership
live", "which file owns shared build/source evidence", and "what is the
integrity root of a release".

The physical layer is in good shape. ADR-059's envelope detects compression
by magic bytes, rejects suffix conflicts, writes deterministically and
atomically, and enforces stored-size, decoded-size, and zstd-window limits.
Real measurements on oneDAL show it working: a ~149 MB `daal` snapshot and a
~115 MB `oneapi::dal` snapshot compress from roughly 264 MB to about 7.7 MB.
Most of that volume is genuine L5 graph evidence, not accidental bloat.

**So the problem is not the codec, and the fix is not to store less
evidence.** The problems are in the logical and container model:

- **Whole-document construction and loading.** `snapshot_to_dict` calls
  `dataclasses.asdict()`, which deep-copies the entire object graph before
  JSON encoding; the read path decompresses, decodes, and parses one
  complete document before reconstructing every dataclass. Several full
  representations can be live at once, so a 150 MB decoded snapshot costs
  far more than its 5 MB on disk. `BundleSignatureEvidence` was introduced
  precisely because release comparison could not afford to retain full
  snapshots; that is a symptom, not a general solution. One decoded
  `BundleFacts` document for a large release can also approach the reader's
  1 GiB decoded-size ceiling even though every library in it is
  individually valid.
- **Identity loss.** `AbiSnapshot.index()` is first-wins for functions,
  variables, and types: duplicates are warned about and then dropped from
  the lookup maps. `typedefs` is keyed by bare name (v25 added
  `typedefs_qualified` alongside it, but the lossy map remains).
  `ElfMetadata.symbol_map` maps a bare symbol name to exactly one
  `ElfSymbol`, although ELF legitimately carries several versions,
  bindings, and default/non-default definitions of one name. Base offsets
  are a `base name -> offset` mapping, which cannot express repeated or
  path-distinguished base subobjects. The multi-TU merge already documents
  further cases it cannot resolve (MSVC decorated names that encode return
  type, uninstantiated template methods with no mangled name, plain-C
  statics with no TU scoping).
- **One overloaded version integer.** `SCHEMA_VERSION` is at 25 and encodes
  at least four independent facts: JSON field layout, producer
  implementation epoch, per-fact reliability, and comparison-critical
  contract compatibility. Bumps v9, v19-v23, and v25 exist because a
  producer began emitting a *correct* value where it previously emitted a
  real-but-wrong default — which the loader reconstructs into snapshot-level
  `*_facts_reliable` flags. A normalization or resolver change can alter
  meaning without changing any field; adding an optional display field
  should not imply a new evidence recipe.
- **Missing conflated with false.** A plain `bool`, `[]`, `{}`, or `None`
  cannot distinguish "positively established" from "producer never ran",
  "producer does not support this", "extraction failed", and "not
  applicable". The `*_facts_reliable` flags are correct compatibility
  patches for the specific historical cases they name, but they do not
  scale to a general evidence model, and they say nothing when one side was
  captured at L2 and the other at L4.
- **Incompletely specified canonical form.** `snapshot_to_json()` does not
  globally sort keys, entity list order can follow producer traversal order,
  and the stable snapshot hash sorts dictionary keys but treats list order as
  significant. `BundleFacts` serialization cannot use recursive key sorting at
  all, because one template-instantiation mapping uses *insertion order* to
  carry template-argument order — structural order and incidental map order
  are not separated.
- **Duplicated shared evidence.** A `BuildSourcePack` embedded per library,
  inside a `BundleFacts` that embeds every library, repeats project-wide
  source graphs, compile databases, and toolchain data once per artifact.
  The oneDAL graph section alone is roughly 57-59 MB decoded per snapshot.
- **Multibuild is modelled but not captured.** `bundle_multibuild.py` gets
  the semantics right — variants are paired, never unioned, and a same-side
  fingerprint collision is an error — but the ordinary capture pipeline
  still writes the `default` variant fingerprint, `bundle_variants:` is not
  wired into `.abicheck.yml` discovery, and the stored/live release
  comparison is not reachable from the standard CLI. A coherent
  `cpu/gcc + cpu/clang + dpc/icx + aarch64/gcc` baseline cannot be produced
  by a normal workflow today.
- **Derived graphs are rebuilt with today's semantics.** `BundleFacts`
  correctly stores authoritative per-library ELF metadata and rebuilds the
  resolution graph rather than persisting a second copy. But provider
  selection, alias normalization, symbol-version handling, and reachability
  have each been corrected over time, so the same stored baseline can yield
  a different derived graph under a later abicheck — with nothing recorded
  about which semantics produced the original answer.

`BuildSourcePack` already demonstrates the shape the rest of the project
should adopt: a manifest, normalized facts, optional raw output, and a
content hash computed over normalized content with volatile runtime data
excluded. This ADR elevates that approach from one embedded subsection to
the storage model for the whole project.

## Decision

### D1 — Keep the physical envelope; replace the logical model

ADR-059's compression detection, atomic write path, deterministic
gzip/zstd output, and decompression-bomb limits are retained unchanged and
remain the only place compression is implemented. v2 changes what is
stored and how it is addressed, not how bytes are framed. **No new binary
codec is introduced**, and no evidence layer is dropped to save space:
JSON compresses this content extremely well, and the remaining cost is
construction, retention, and duplication rather than disk.

### D2 — Separate the version axes

One integer stops carrying four meanings. A v2 package records, as
independent fields:

| Axis | Meaning |
|---|---|
| `package_format_version` | container/manifest layout |
| `section_schema_version` | per-section field layout (per section kind) |
| `normalization_recipe` | how spellings/paths/identities were normalized |
| `producer` (`name`, `version`, `binary_digest`) | what emitted the facts |
| `extractor_generation` | extraction semantics epoch |
| `resolver_generation` | derived-graph/resolution semantics epoch |
| `comparison_contract_version` | what a reader must understand to compare safely |

Exactly two axes fail closed, for two different reasons: a newer
`package_format_version` means the reader may not be able to *locate* the
package's structures at all, and a newer `comparison_contract_version` means
comparing without understanding the change could produce a *wrong verdict*.
**Both** also fail closed when the package does not state the axis validly —
absent, malformed, non-integral or non-positive alike. An axis that exists to
refuse unknown semantics cannot treat "unknown" as agreement, and that
reasoning is symmetric: a package that never says what layout it has is no
more parseable than one whose layout is too new. Neither axis is validated
only by the deserializer; a reader must re-check both where it decides,
because the version object is constructible directly.

The remaining five are informational to a reader that does not recognize
them, which is what lets an optional display field ship without implying a
new evidence recipe.

(Three drafts of this paragraph were wrong in the same direction and each was
caught by review. The first said only `comparison_contract_version` fails
closed at all; the second granted that both do but kept the
absent-value rule for the contract axis alone, so a consumer implementing the
ADR could synthesize a current format version for a package whose layout is
unknown; and the implementation itself had to be corrected twice before it
matched — a negative version, then a fractional one, each slipping past the
guard written for the previous case. Recorded rather than tidied away,
because the repeated direction is the finding: it is always the
container-layout axis that gets quietly weakened to match the narrower rule,
and weakening it lets a reader silently misparse a package instead of
refusing it.)

Capability is stated explicitly rather than derived from schema
history, and an imported legacy snapshot preserves its
`source_schema_version` and `source_producer_generation` so migrations and
audits stay honest instead of accumulating one special case per newly
discovered historical producer defect.

### D3 — Fact availability is explicit, never inferred from a default value

Every comparison-relevant fact family carries a `FactAvailability` record:
`status` (`present` / `partial` / `not_collected` / `unsupported` /
`failed` / `not_applicable`), producer, producer version, recipe, scope,
confidence, and diagnostics. Availability is declared once per fact family,
overridden per entity only where an entity genuinely differs, and
summarized in a coverage ledger.

An empty vtable list then means exactly "the producer ran and established
there are no virtual entries", distinct from `unsupported`. A comparison
that requires a family whose availability is not `present`/`partial`
resolves through policy to `NOT_COMPARABLE` or an explicitly
reduced-confidence result. **It never infers safety from an empty
collection.** This generalizes the `*_facts_reliable` flags, which remain
as the import-time expression of the same fact for legacy snapshots.

### D4 — Preserve every occurrence; never resolve identity by dropping data

Two identities are modelled separately:

- **`EntityId`** — the logical declaration/symbol/type believed to be the
  same across occurrences and releases.
- **`OccurrenceId`** — one specific observation: an AST declaration, a TU
  occurrence, a binary symbol version, a DWARF DIE, a PDB record, a source
  location.

Every occurrence is stored. Resolution may group occurrences under one
entity, but must never destroy multiplicity. Where occurrences cannot be
reconciled confidently, **both are retained and an `IdentityConflict` is
recorded** — replacing today's warn-and-keep-the-first behavior.

Concretely, an ELF symbol occurrence is keyed by artifact, name, version,
default-ness, binding, type, visibility, and definition/import status
rather than by bare name; a base subobject becomes a list entry carrying
base type, inheritance path, offset, virtuality, access, producer, and
availability rather than a `name -> offset` mapping.

### D5 — One canonical logical encoding

- every unordered collection has an explicit, stable sort key;
- every semantically ordered collection is an array, never a map whose
  insertion order carries meaning (template arguments become
  `[{"parameter": …, "value": …}, …]`);
- a map is used only when its keys are unique and its order is meaningless;
- float normalization is specified: `-0.0` and `0.0` agree, an integral
  float and the same integer agree, and a non-finite value is refused rather
  than emitted as a bare `NaN`/`Infinity` literal no conforming JSON parser
  accepts;
- volatile capture metadata (timestamps, hostnames, wall-clock durations,
  absolute scratch paths) lives outside the semantic-hash domain;
- semantic digests are computed from the normalized logical object,
  independently of pretty-printing, key order, or compression.

Randomizing producer traversal, dictionary insertion, TU completion, and
parallel extraction order must not change any semantic digest or any
comparison result.

**Path normalization is deliberately not specified here, and is Phase 1
work.** An earlier draft of this list claimed it alongside float
normalization; nothing defined or implemented it, so a producer had a
requirement it could not satisfy consistently and two equivalent captures
could hash differently on path spelling or platform (Codex review). Stating a
rule nobody can implement is worse than stating none: it reads as settled.

It is not a line item because it is not a small one — separator direction,
case folding on case-insensitive filesystems, absolute versus source-root-
relative form, symlink resolution, and `~`-redaction (which ADR-032 D7
already applies to `CompileUnit.output`, for persistence rather than for
hashing) each change what two captures of one build agree on, and getting any
of them wrong silently merges or splits content. It belongs with the
`normalization_recipe` axis D2 already reserves, so that a package states
which rule produced its digests rather than every reader assuming the current
one. Until then, a path reaching the hash domain is hashed as written.

### D6 — One `ProjectSnapshot` package

Baseline sets, bundle facts, and per-library snapshots converge on a single
content-addressed package. A one-library scan is a package with one
artifact; bundle facts become one section rather than a parallel top-level
format.

```text
project.abicheck/
  manifest.json            # small; loads immediately
  refs/variants/<variant-id>.json
  refs/artifacts/<artifact-id>.json
  objects/sha256/<aa>/<digest>.json.zst
  indexes/index.sqlite     # optional, rebuildable, never canonical truth
```

The transport form is a deterministic `.tar.zst` of that directory. The
SQLite index accelerates queries and is regenerable from manifests and
objects, so baseline durability never depends on a database migration.

Artifact records carry native binary identity explicitly (content SHA-256,
ELF build ID / Mach-O UUID / PE-PDB identity) — the existing top-level
`build_id` means an opaque CI identifier and is not reused for this.
Membership is stated for every artifact kind, including PE, Mach-O,
Python-visible, and header-only targets, with bundle-level *resolution*
declared as an ELF-only capability rather than silently excluding
non-ELF entries.

### D7 — Shared evidence is stored once and referenced by digest

Project source graphs, variant build evidence, toolchain profiles, compile
database slices, common header surfaces, and raw extractor artifacts are
content-addressed objects. An artifact references the shared object plus its
own overlay instead of embedding a private copy.

### D8 — Sections are independently addressable and lazily loaded

Snapshot content is split into `binary`, `declarations`, `types`, `layout`,
`debug`, `build`, `source_abi`, `graph`, `provenance`, `diagnostics`, and
`raw_refs` sections, chunked to a bounded decoded size (target 1-8 MB,
tuned against real measurements). A project comparison loads two manifests,
all small L0 binary sections, then one matched library pair at a time,
releasing each pair's sections before the next. **Peak memory must scale
with the largest active section or library pair, not with the total decoded
size of the release.**

### D9 — Variant identity is created at capture time

The capture pipeline is told which variant it is producing. A package
stores both declared and captured-and-verified variant coordinates. Stable
variant identity (target, compiler family, feature toggles) is kept
separate from build state that may legitimately change between releases
(compiler version, standard, flags, artifact membership) — so adding or
removing a library is a change *inside* a matched variant, not a new
variant. Variants are never unioned; old-only and new-only variants stay
explicit outcomes.

### D10 — Evidence profiles define completeness

"Store all relevant information" means: for the selected profile, every
required fact family is either present or carries a machine-readable reason
why it is not.

| Profile | Required |
|---|---|
| `binary` | artifact identity, exports/imports, versions, dependencies, loader facts |
| `abi` | `binary` plus debug/layout and signature evidence where available |
| `api` | `abi` plus public-header declarations and the extraction contract |
| `source_abi` | `api` plus L3/L4 build and source facts |
| `deep_graph` | `source_abi` plus L5 graph and reachability evidence |
| `reproducible` | the selected profile plus raw evidence references |

### D11 — Caches key on content, not on mtime

Cache identity is `binary content digest + input tree content digest +
extraction contract + producer/extractor generation + normalization recipe +
requested profile`. Size and mtime may be used to *avoid recomputing* a
digest, but never to define semantic identity, so touching an unchanged
header does not discard an expensive extraction. Eviction uses a byte quota
with LRU rather than an entry count, and caching is per section so an
unchanged binary, a shared graph, or an unchanged variant can be reused.

### D12 — Compression level is internal policy, not a new user knob

Local/extraction cache uses zstd level 3, ordinary CI baselines 10-15,
archival release publication 19; already-compressed or raw objects are not
recompressed. After structural deduplication the marginal value of maximum
compression falls further, so this stays a policy table rather than another
tuning surface.

### D13 — Migration is adapter-based and non-destructive

Every v1-v25 snapshot stays readable through an import adapter into the v2
in-memory model. No existing baseline is rewritten in place, and no user is
required to regenerate a baseline to keep comparing. Writers adopt v2
behind an explicit opt-in until the validation corpus in the plan passes.

## Consequences

**Positive.** Peak memory decouples from release size. Duplicate and
ambiguous entities stop disappearing silently. "Not collected" stops
reading as "not present". Determinism becomes checkable rather than
assumed. One package answers project, release, variant, and integrity
questions. Shared graphs are stored once. Historical baselines can be
compared under, or explicitly refused by, the semantics that produced them.

**Negative / cost.** A second storage model exists during migration, and
the import adapter is real, permanent surface. Content-addressed layout is
harder to inspect by hand than one JSON file (mitigated by the transport
`.tar.zst` and the rebuildable index). Occurrence preservation makes some
sections larger before deduplication makes the package smaller. Explicit
availability will surface genuine `NOT_COMPARABLE` results that today pass
silently — that is the point, but it is a visible behavior change and is
therefore gated behind D13's opt-in.

## What this ADR does not decide

- The wire encoding *inside* an object (JSON is retained; a columnar or
  binary encoding is not proposed and is not expected to be the bottleneck).
- Concrete chunk-size and zstd-level constants, which are set from the
  plan's measurement corpus rather than chosen here.
- Whether the SQLite index ever becomes mandatory (it does not today).
- Bundle-level *resolution* semantics for PE and Mach-O — D6 requires
  membership and capability declaration, not a new resolver.
- The `ChangeKind` or report-schema consequences of newly explicit
  `NOT_COMPARABLE` outcomes; those are ADR-050's and ADR-042's surfaces.

## References

- [ADR-015](015-snapshot-serialization.md) — original snapshot format (historical)
- [ADR-023](023-bundle-aware-multi-binary-analysis.md) — bundle layer
- [ADR-050](050-comparability-contract-and-multi-tu-manifest.md) — comparability contract
- [ADR-059](059-compressed-snapshot-storage.md) — physical storage envelope (kept)
- [ADR-061](061-responsibility-package-architecture.md) — the `storage` layer this package occupies
- [G38](../plans/g38-bundle-facts-model-and-multibuild-comparability.md) — bundle facts and multibuild pairing
- [Storage format v2 plan](../plans/storage-format-v2.md) — phasing, acceptance criteria, validation corpus
