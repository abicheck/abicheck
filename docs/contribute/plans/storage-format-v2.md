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

**Status: A1.1/A1.2/A1.3 implemented**, and **A1.4/A1.5's first slice
implemented** (ADR-063 Track B "8B") — the object model
(`PackageManifest`/`VariantRef`/`ArtifactRef`/`ObjectRef`/`ObjectStore`,
`PackageManifest.project_sections`), a real directory-backed store
(`abicheck/project_snapshot_store.py`'s `DirectoryObjectStore` plus its
manifest/ref writer/reader, now also publishing/reading `project_sections`
— everything but the `.tar.zst` transport form), the v1-v25 import adapter
(`storage/import_v1.py`), a single-library snapshot round-tripping through
the store as a one-artifact project, and the first real multi-artifact
writer/reader (`abicheck/bundle_facts_store.py`'s `write_bundle_facts_
package`/`read_bundle_facts_package`, covering `BundleFacts`'s per-library
snapshots and instantiation manifest) are all landed. `BuildSourcePack`/
source-graph dedup, `bundle_variants:`/A1.6-A1.8 remain open. See "Landed in
Phase 1" below.

- **A1.1** `ProjectSnapshotStore` reads and writes the D6 layout over a
  directory abstraction, with a deterministic `.tar.zst` transport form.
  **Implemented except the `.tar.zst` transport form**, which remains open.
- **A1.2** A v1-v25 import adapter maps every existing snapshot into the v2
  in-memory model, preserving source schema and producer generation. No
  existing baseline is rewritten in place. **Implemented, and now for every
  document field, not just `semantic_ir`/`semantic_ir_conflicts`**:
  `storage/legacy_sections.py`'s explicit per-section field split puts the
  rest onto D8's named sections too (each still carrying its own
  pre-existing JSON encoding internally, not a further per-field typed
  decode) — see "Landed in Phase 1" below.
- **A1.3** A single-library snapshot is representable as a one-artifact
  project, and round-trips through the store unchanged at the semantic-digest
  level. **Implemented** — see `tests/test_project_snapshot_store.py`'s full
  package round trip.
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
   **Object model landed**, including multi-artifact/multi-variant shape
   (`PackageManifest.artifact_refs`/`variant_refs` are already tuples, not
   singletons — see A1.1's own design note below for what's actually
   missing).
2. `storage/import_v1.py` — the v1-v25 adapter (A1.2), including the
   `*_facts_reliable` flags becoming `FactAvailability` records. **Landed.**
3. Express a single-library dump as a one-artifact project (A1.3). **Landed**
   — `project_snapshot_store.py`/`project_snapshot_legacy.py`; see
   `docs/contribute/adr/063-one-semantic-pipeline.md`'s Phase 8 note for the
   single-file-by-default CLI wiring this actually shipped as.
4. Express a `BundleFacts` (N libraries plus an instantiation manifest) as a
   multi-artifact project (A1.4/A1.5's per-library-facts and
   instantiation-manifest halves). **Landed** — `bundle_facts_store.py`'s
   `write_bundle_facts_package`/`read_bundle_facts_package`, plus
   `PackageManifest.project_sections` (`storage/package.py`) and its
   `write_project_manifest`/`read_project_manifest` publish/read support
   (`project_snapshot_store.py`).
5. **Open, designed below**: the `.tar.zst` transport form (the remainder of
   A1.1), `BuildSourcePack`/source-graph digest-deduplicated shared evidence
   (the remainder of A1.4/A1.5), `bundle_variants:` CLI wiring (A1.6),
   stored/live release-comparison reachability (A1.7), and non-ELF artifact
   membership (A1.8).

`AvailabilityLedger.declare` and `.override` rebuild, revalidate, and
re-sort the whole mapping per call, so building a ledger of *n* overrides
costs O(n² log n) (CodeRabbit review). That is deliberate for Phase 0,
where nothing calls them in a loop and the validating, canonically-ordered
reassignment is what makes the ledger's state impossible to corrupt in
place. A1.5's digest-deduplicated shared-object writer below is where it
starts to matter — the first producer that calls `override` per entity —
so the container decision belongs with that producer, which knows the
insertion pattern, rather than being guessed at now. Whatever replaces it
must keep both properties the current shape buys: every stored key
validated, and iteration order a function of content rather than of
insertion.

---

#### A1.1 remainder — the `.tar.zst` transport form

**Status: not implemented.** Everything else D6 specifies for the directory
layout is real (`project_snapshot_store.DirectoryObjectStore` plus its
manifest/ref writer and reader over ADR-059's physical envelope); only the
single-file transport wrapper is missing, which is why a package today is
"many small files… awkward to `scp`/commit/upload as a CI artifact" (this
plan's own Problem statement, finding row derived from #6/#7) whenever it
*is* used (the directory writer/reader remain real, typed-API primitives
and a `compare`/`scan --against` input shape even though no `dump` CLI flag
produces one — see ADR-063 Phase 8's landing note).

**Goal.** A `ProjectSnapshotStore` directory tree round-trips through one
deterministic archive file, so a multi-artifact package is exactly as easy
to move around as today's single `.abi.json.zst` — one path to upload as a
CI artifact, attach to a release, or hand to a teammate.

**Design.** A thin archive/unarchive pair, not a new format: `pack(store_dir,
out_path)` walks the D6 tree (`manifest.json`, `refs/**`, `objects/sha256/**`)
in one canonical order — sorted by relative path, the same "iteration order
a function of content, not insertion" discipline `storage/canonical.py`
already establishes for in-document collections — and streams each file into
a `tar` archive, `zstd`-compressed by ADR-059's existing `snapshot_io.py`
compressor (same codec, same decompression-bomb limits; this is a container
format change, not a new compression dependency). `unpack(archive_path,
dest_dir)` is the exact inverse: it must reject a member path that would
escape `dest_dir` (`../`, an absolute path, a symlink target outside the
tree) the same way `snapshot_io.py`'s own decompression guard rejects a
runaway decompressed size — an untrusted `.tar.zst` handed to `compare` is
adversarial input, not merely malformed. Determinism is checked the same way
A0.4 checks canonical JSON: two packs of the same logical content (built via
different traversal orders, e.g. Python `dict` insertion order varying
between constructing the manifest one way vs. another) must produce
byte-identical archives — fixed `mtime`/uid/gid/mode on every tar member,
sorted member order, no embedded timestamps in the outer zstd frame.

**Files.** `abicheck/project_snapshot_transport.py` (new, sibling to
`project_snapshot_store.py`/`project_snapshot_legacy.py`, for the same
import-layering reason those live outside `storage/` — this is a CLI/
filesystem-facing concern, not a leaf primitive): `pack_project_snapshot`/
`unpack_project_snapshot`. `snapshot_io.py` gains no new public surface;
this module calls its existing compressor/decompressor functions directly
over the tar byte stream instead of a single JSON document's bytes.

**Tests.** Round-trip identity (pack then unpack reproduces the exact
directory tree, file-for-file, byte-for-byte); determinism (two packs of
logically-identical content, built via different in-memory construction
order, produce identical archive bytes); the path-traversal/symlink-escape
adversarial cases, each asserted to raise rather than write outside
`dest_dir`; a real multi-artifact package (see A1.4/A1.5 below) at a
realistic size, per this repository's "toolchain/wire format changes need a
round-trip test at production scale" convention.

**Acceptance criteria.** A directory-backed package produced by
`project_snapshot_store.py` packs and unpacks losslessly; `compare`/
`scan --against`'s existing directory-package input path
(`workflows.input_resolution.resolve_input`) accepts a `.tar.zst` archive
interchangeably with an already-unpacked directory (unpacked to a temp
directory internally, the same way a compressed single-file `.abi.json.zst`
is transparently decompressed today).

---

#### A1.4/A1.5 — folding baseline sets/`BundleFacts` into sections, digest-deduplicated shared evidence

**Status: first slice implemented** (ADR-063 Track B "8B") — see "What
landed" below; the `BuildSourcePack`/source-graph half remains not
implemented. This is the item this plan's "Relationship to
G38" section flags as a coordination point: *whichever* of this plan's
Phase 1 or G38's own Phase 2 lands first decides which document the other
targets. Written here on the assumption G38 Phase 2 has not landed a second
persisted bundle shape first — if it has, this section's target is
`BundleFacts` document fields becoming a section, not a fresh design.

**What landed.** `abicheck/bundle_facts_store.py`'s
`write_bundle_facts_package`/`read_bundle_facts_package` implement the
per-library-ABI-facts half of the Design below exactly as specified (one
`ArtifactRef` per library under a shared `VariantRef`, via
`import_legacy_snapshot` against one shared `store`), plus the
`BundleFacts.manifest`/`filesystem_aliases`/`library_filenames` half of the
cross-library-facts split (the first real `PackageManifest.project_sections`
entry, and `ArtifactRef.native_identity`, respectively — both exactly as
this section's Design already specified). **Not** landed: a shared
`BuildSourcePack`/project source graph as a `project_sections` entry —
closing finding #6's "~57-59 MB graph repeated per artifact" needs that
half specifically, which this slice does not touch.

**Goal.** A release directory (today: N per-library `.abi.json[.zst]`
files, sometimes a `manifest.json`, sometimes a `BundleFacts` document
embedding all N snapshots again) is representable as one
`ProjectSnapshot` package: one `PackageManifest` naming N `ArtifactRef`s
under a shared `VariantRef`, with cross-library evidence
(`BuildSourcePack`, the project source graph, the instantiation manifest)
stored **once** and referenced by digest from every artifact that needs it
— closing finding #6 ("~57-59 MB graph repeated per artifact") directly,
since today's per-snapshot embedding is exactly what A1.5 replaces.

**Design.** Two distinct facts currently conflated in `BundleFacts`
(`abicheck/bundle_facts.py`) get two distinct homes:

- **Per-library ABI facts** (`BundleFacts.per_library_snapshots: dict[str,
  AbiSnapshot]`) become what A1.3 already established: one `ArtifactRef` per
  library, each with its own D8-sectioned `declarations`/`types`/`layout`/
  `debug`/... objects. No design gap here — A1.3's per-artifact section
  split already covers a single library; this item is applying it N times
  under one manifest instead of once.
- **Cross-library facts** (`BundleFacts.manifest: InstantiationManifest`,
  `filesystem_aliases`, `library_filenames`, plus — once a package carries
  build/source evidence at all — a shared `BuildSourcePack`/source graph)
  become **project-level objects**: content-addressed, stored once under
  `objects/sha256/...`, and referenced by every `ArtifactRef` that shares
  them via one more `ObjectRef`-shaped field on `PackageManifest` itself
  (not per-artifact `sections`, which is where A1.3's *per-artifact* D8
  sections already live) — `PackageManifest.project_sections:
  Mapping[str, ObjectRef]`, keyed the same way `ArtifactRef.sections` is
  (`"instantiation_manifest"`, `"build_source_pack"`, `"source_graph"`,
  `"filesystem_aliases"`). A library whose evidence happens to be
  byte-identical to another's (the common case for a shared
  `BuildSourcePack` across variants of the same project) collapses to one
  stored object automatically, since `ObjectStore` addressing is by digest,
  not by declared kind — no separate dedup pass is needed beyond writing
  through the store's existing `put`/digest-return contract.

  `filesystem_aliases`/`library_filenames` are per-artifact facts, not
  cross-library ones, despite living on `BundleFacts` today (the dict key
  is a library name) — those move onto `ArtifactRef.native_identity`
  instead (already the designated `str -> str` fact map for
  per-artifact content/build identity), not `project_sections`.

  The instantiation manifest (`InstantiationManifest`, from
  `bundle_manifest.py`) is genuinely project-level (it promises entries
  across the whole bundle, not one library), so it is the first real
  `project_sections` entry and the one this item's acceptance test targets.

**A schema note this item must get right the first time, not retrofit
later:** `ArtifactRef.sections`/the new `PackageManifest.project_sections`
are both `Mapping[str, ObjectRef]` today (A1.1/A1.3's existing shape) —
adding `project_sections` is additive to `PackageManifest` (a new optional
field with an empty-mapping default, so every already-round-tripped
one-artifact package stays valid) and needs no `PackageManifest.__post_init__`
change beyond validating its own `ObjectRef` values the same way
`ArtifactRef.sections` already does.

**Files.** `abicheck/storage/package.py` (`PackageManifest.project_sections`
field + validation, `to_dict`/`from_dict`); `abicheck/bundle_facts_store.py`
(new — the writer that takes a real `BundleFacts` plus N `AbiSnapshot`s and
produces a multi-artifact `PackageManifest` via the object store, and the
reader that reconstructs a `BundleFacts`-shaped view from one for every
existing `compare_bundle_from_facts` caller, so this is additive storage,
not a `BundleFacts` API break); `abicheck/storage/legacy_sections.py` gains
no new field allowlist entries (cross-library facts were never
`AbiSnapshot` fields, so A1.2's per-`AbiSnapshot` split is untouched by this
item).

**Tests.** A real multi-library `BundleFacts` (oneDAL-shaped, per this
plan's own Validation corpus) round-trips through
`bundle_facts_store.py` and back to an equivalent `BundleFacts` at the
semantic-digest level (mirrors A1.3's existing one-artifact round-trip
test, generalized to N artifacts). A property test: two libraries sharing
byte-identical `BuildSourcePack` content store exactly one
`project_sections["build_source_pack"]` object, not two — the executable
form of the "~57-59 MB graph repeated per artifact" finding actually
closing. `compare_bundle_from_facts`'s existing test suite is re-run
unmodified against the reconstructed-from-package `BundleFacts` view, per
this item's "additive storage" acceptance bar below.

**Acceptance criteria.** Every existing `compare_bundle_from_facts` test
passes unmodified against a `BundleFacts` reconstructed from a
`bundle_facts_store.py`-written package — this item changes *where the
bytes live*, not `compare_bundle()`'s observable behavior. Peak decoded
size for an N-library release with shared build/source evidence no longer
scales with N × (per-library size + shared-evidence size); it scales with
(sum of per-library sizes) + (shared-evidence size once).

---

#### A1.6 — `bundle_variants:` CLI wiring

**Status: not implemented** (the config schema and pairing algorithm are —
`bundle_variants_config.py`'s `parse_bundle_variants_config`/
`pair_variants` are real and tested; nothing yet resolves a `.abicheck.yml`
`bundle_variants:` block into an actual capture run).

**Goal.** A project's `bundle_variants:` block (variant name →
`target_triple`/`compiler_family`/`feature_toggles`/`required`) drives a
real multi-variant capture, and both what was *declared* in config and what
was *actually captured* end up on the package's own `VariantRef.declared`/
`.captured` maps (already exactly this two-map shape, per that class's own
docstring) — so a later comparison can tell a genuine variant-boundary
change from an ordinary version bump.

**Design.** `BundleVariantSpec`'s four fields map onto `VariantRef.declared`
verbatim (`{"target_triple": ..., "compiler_family": ..., **feature_toggles}`)
at config-parse time, before any capture runs — this is the `declared` half,
knowable from `.abicheck.yml` alone. `.captured` is filled in per real
capture run from whatever the toolchain/build actually reports (compiler
version, resolved standard, resolved feature-toggle values where a build
system can confirm them) — the same "two independent coordinate maps"
split `VariantRef`'s own docstring already specifies, so this item is
wiring a real producer for a schema that already exists, not designing a
new one. A `required: true` variant that fails to capture is a hard error
for the release-capture command (mirrors `AnalysisPlanner`'s "reject before
extraction" discipline: knowing a required variant is unreachable belongs
at plan time, not discovered as a silently-incomplete package after a long
capture run); `required: false` degrades to a package missing that
`VariantRef` entirely, not a placeholder with empty `captured`.

**Files.** `abicheck/cli_project.py` (a new subcommand, per this
repository's root-command admission bar in `AGENTS.md` — this is advanced,
multi-target CI-integration surface that fits the existing `project` group,
not a new root command) or an extension of whatever multi-library capture
entry point A1.7 below settles on, since the two are naturally one CLI
surface (capture N variants of M libraries into one package) rather than
two independent flags. `abicheck/bundle_variants_capture.py` (new) —
resolves a `.abicheck.yml` `bundle_variants:` block plus a real build
description into one capture run per variant, writing each into the shared
`bundle_facts_store.py` writer from A1.4/A1.5 above with the right
`VariantRef`.

**Tests.** A `bundle_variants:` block with two variants (one `required`,
one not) against a fixture build; the `required` variant's simulated
capture failure raises before any file is written (no partial package);
`declared` vs. `captured` disagree on at least one field in the fixture
(e.g. `.abicheck.yml` under-specifies a compiler version the real build
reports), asserting both maps are kept, not merged/overwritten.

**Acceptance criteria.** `pair_variants()` (already real) operates
correctly over `VariantRef`s produced by a real capture run, not only over
hand-constructed `BundleVariantSpec` fixtures — closing the "modelled but
not captured" half of finding #7.

---

#### A1.7 — stored/live and stored/stored release comparison from the standard CLI

**Status: not implemented.** `compare`/`scan --against` already accept a
directory package as an operand (ADR-063 Phase 8's landing note); what's
missing is a *release-level* command that compares two packages (or a
package against a live directory of binaries) library-by-library and
variant-by-variant, the multi-artifact counterpart to
`cli_compare_release.py`'s existing directory-of-`.so`-files fan-out.

**Goal.** `stored/live`, `live/stored`, and `stored/stored` release
comparisons are reachable the same way `live/live` already is — not a
second, parallel command family, per this plan's "Relationship to G38"
principle of one container format and this repository's admission bar
against a second vocabulary next to an established one.

**Design.** `cli_compare_release.py`'s existing per-library fan-out
(`_compare_release_libraries`, and — per this ADR-063 work's own D1
migration above — `_resolve_stranded_library`) already resolves each
library through the shared `CompareRequest`/`DumpRequest` pipelines; this
item's job is giving that fan-out a *package* as one of its two top-level
operands (today: two directories of loose files) — unpacking a
`.tar.zst`/directory package into the same `old_map`/`new_map: dict[str,
Path]` shape the fan-out already builds from a loose directory, keyed by
`ArtifactRef.artifact_id`, so every downstream step (matching, per-pair
comparison, bundle analysis, `--bundle-facts-out`) is unchanged code
operating on resolved paths — a package is a *source* for that map, not a
new code path through the fan-out. `--old-variant`/`--new-variant` select
which `VariantRef` to compare when a package carries more than one
(defaulting to the package's only variant when it carries exactly one, a
usage error when it carries several and neither flag is given — the same
"ambiguity is a hard usage error, not a silent first-match" discipline
`SymbolIdentityIndex`'s `unique_alias_match` already establishes for a
different kind of ambiguity elsewhere in this codebase).

**Files.** `cli_compare_release.py` (accept a package path — file or
directory — as either operand, detected via `project_snapshot_legacy.
is_project_snapshot_package_dir`/a `.tar.zst` magic-byte sniff already
established for the single-file path per ADR-059); `project_snapshot_
legacy.py` or the new `bundle_facts_store.py` (A1.4/A1.5) for the
package → `{artifact_id: resolved snapshot}` resolution a stored-side
operand needs (a live-side operand keeps resolving through the existing
binary-directory path unchanged).

**Tests.** All three of `stored/live`, `live/stored`, `stored/stored`
against a small real fixture package (2-3 libraries), each asserted to
produce the identical `DiffResult` set a `live/live` run over the
equivalent loose-file directories would — the "Stored-versus-live parity"
row this plan's own Validation corpus section already commits to,
finally exercised by a real test rather than only stated as a corpus
requirement.

**Acceptance criteria.** Closes finding #7's "no coherent multi-variant
baseline from a normal workflow" for the comparison half (A1.6 above
closes the capture half); no new root CLI command (`AGENTS.md`'s admission
bar) — this is `compare`'s existing release fan-out gaining a new operand
shape, not a new verb.

---

#### A1.8 — non-ELF artifact membership

**Status: not implemented**, and deliberately the narrowest item here.
`ArtifactRef.kind` already accepts any string (`"elf"`, `"pe"`, `"macho"`,
`"python"`, `"header_only"`, ...) per its own docstring — the object model
was built D6-complete from the start. What's missing is purely on the
*producer* side: nothing today constructs an `ArtifactRef` for a PE/Mach-O/
Python-visible/header-only member, because A1.3/A1.4's own capture paths
have so far only ever fed them an ELF `AbiSnapshot`.

**Goal.** A PE/Mach-O/Python-visible/header-only library is a first-class
package member — representable, storable, and readable — even though
bundle-level *resolution* (dependency-graph edges, ABI-affecting-type
propagation) stays an ELF-only capability, per D6's own explicit split
between "can this be a member" and "can this be resolved".

**Design.** No new schema: `ArtifactRef(kind="pe", ...)` /
`kind="header_only"` already round-trips through `to_dict`/`from_dict`
today (confirmed by reading `ArtifactRef.__post_init__` — `kind` is
validated as non-empty text, never restricted to a fixed enum). This item
is therefore almost entirely a **producer + capability-declaration** change,
not a storage change: `bundle_facts_store.py` (A1.4/A1.5) must accept a
non-ELF `AbiSnapshot`/equivalent fact object without assuming
`sections["binary"]` exists (a header-only member legitimately has no
`"binary"` section at all, per `ArtifactRef.sections`'s own docstring), and
whatever consumes `PackageManifest.artifact_refs` for bundle-level
resolution (`bundle._compute_resolution_graph` and siblings) must treat a
non-ELF `kind` as "a member with no resolution edges" rather than raising —
the same "declared as an ELF-only capability rather than silently excluded"
framing D6 already states, made mechanical: a resolution pass that silently
drops a non-ELF member is the bug this item exists to close, one that
silently *fails* on it (no diagnostic) or crashes is a regression this
item must not introduce either.

**Files.** `bundle_facts_store.py` (A1.4/A1.5) — accept any `ArtifactRef.kind`,
not only `"elf"`; `abicheck/bundle.py`/`abicheck/bundle_soname.py` (wherever
`_compute_resolution_graph` lives today) — an explicit non-ELF branch that
records "no resolution edges, capability not applicable" rather than
inferring absence from a missing ELF-shaped field.

**Tests.** A package with one ELF member and one header-only member;
bundle-level resolution runs over the ELF member unchanged and reports the
header-only member's absence from the resolution graph as an explicit,
named fact (not a silent gap a reader has to infer from the member simply
not appearing).

**Acceptance criteria.** Closes finding #7's "non-ELF artifacts... silently
excluded" half specifically — a PE/Mach-O/Python/header-only library
survives a round trip through the package format and appears in a package
listing/report, even where no resolution graph can place it.

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
| `abicheck/storage/entity_ids.py` | `EntityId`, `EntityKind`, `ObservationKind`, `OccurrenceId`, `elf_symbol_occurrence` — internal identifier leaf, re-exported in full by `identity.py`; plus `DOMAIN_ENTITY_ID_SCHEMA_VERSION`, `domain_entity_id_to_dto`, `domain_entity_id_from_dto` (ADR-063 Phase 2's storage v2 wire bridge to `model.identity.EntityId` — a separate domain type from this module's own `EntityId` above, so **not** re-exported by `identity.py`, whose surface is this module's pre-existing packed-key wire DTO and the occurrence-set collection built on it) |
| `abicheck/storage/canonical.py` | `canonical_form`, `canonical_json`, `raw_digest`, `semantic_digest`, `strip_capture_metadata`, `CAPTURE_METADATA_KEY` (A0.4; `raw_digest` A1.1) |
| `abicheck/storage/versioning.py` | `PACKAGE_FORMAT_VERSION`, `COMPARISON_CONTRACT_VERSION`, `UNSTATED_VERSION`, `StorageVersions`, `ProducerIdentity`, `ReaderCompatibility`, `check_reader_compatibility` (A0.5) |
| `abicheck/storage/guards.py` | `identity_text`, `binary_buffer`, `decision_key`, `key_collection`, `required_field`, `row_sequence`, `item_iterable`, `provenance_text`, `diagnostics_from`, `mapping`, `enum_member`, `instance_of`, `strict_int` (ADR-063 Phase 2) — internal, not re-exported by the package |

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

## Landed in Phase 1: A1.1's object model, directory store, import adapter, and one-artifact projects

The object model (A1.1's first half), the directory-backed store (A1.1's
second half), the v1-v25 import adapter (A1.2), and expressing a
single-library dump as a one-artifact project (A1.3) are implemented —
jointly with ADR-063 Phase 8, whose own D8 constraint (every DTO a
distinct, versioned, explicitly-encoded class, never `asdict`) this landing
satisfies for the one domain type it actually sections today
(`SemanticIR`).

| Module | Contract |
|---|---|
| `abicheck/storage/package.py` | `MANIFEST_RELPATH`, `SECTION_KINDS`, `ObjectRef`, `VariantRef`, `ArtifactRef`, `PackageManifest`, `ObjectStore`, `InMemoryObjectStore`, `object_relpath`, `variant_ref_relpath`, `artifact_ref_relpath` (A1.1) |
| `abicheck/storage/dto.py` | `BINARY_SECTION_KIND`, `BUILD_SECTION_KIND`, `DEBUG_SECTION_KIND`, `DECLARATIONS_SECTION_KIND`, `GRAPH_SECTION_KIND`, `LAYOUT_SECTION_KIND`, `PROVENANCE_SECTION_KIND`, `SECTION_SCHEMA_VERSIONS`, `SEMANTIC_IR_SECTION_KIND`, `TYPES_SECTION_KIND`, `SectionDTO`, `binary_from_dto`, `binary_to_dto`, `build_from_dto`, `build_to_dto`, `debug_from_dto`, `debug_to_dto`, `declarations_from_dto`, `declarations_to_dto`, `graph_from_dto`, `graph_to_dto`, `layout_from_dto`, `layout_to_dto`, `legacy_section_from_dto`, `legacy_section_to_dto`, `migrate_section_dto`, `provenance_from_dto`, `provenance_to_dto`, `semantic_ir_from_dto`, `semantic_ir_to_dto`, `types_from_dto`, `types_to_dto` (A1.1's per-section DTO envelope, jointly ADR-063 Phase 8's D8 constraint; `TYPES_SECTION_KIND`/`types_from_dto`/`types_to_dto` are ADR-063 Track 4 (8B)'s first typed-DTO promotion beyond semantic_ir, see types_section_codec.py; `GRAPH_SECTION_KIND`/`graph_from_dto`/`graph_to_dto` are its second, see graph_section_codec.py; the remaining six `*_SECTION_KIND`/`*_from_dto`/`*_to_dto` triples are its third slice, see sparse_section_codec.py -- every D8 legacy section kind now has a dedicated DTO) |
| `abicheck/storage/legacy_sections.py` | `LEGACY_SECTION_KINDS`, `SCHEMA_VERSION_KEY`, `join_legacy_document`, `missing_required_section_fields`, `split_legacy_document` (D8's full legacy-document section partition) |
| `abicheck/storage/import_v1.py` | `export_legacy_snapshot`, `import_legacy_snapshot` (A1.2) |
| `abicheck/storage/sectioned_document.py` | `SECTION_SCHEMA_VERSIONS_KEY`, `SECTIONS_KEY`, `from_sectioned_document`, `is_sectioned_document`, `to_sectioned_document` (Phase 8 redesign: the D8 section split packaged as one JSON document instead of a directory-backed package -- see the module's own docstring) |

`ObjectRef`/`VariantRef`/`ArtifactRef`/`PackageManifest` are the in-memory
document model of D6's `manifest.json` plus the ref documents it names.
`ObjectStore` is D7's digest-addressed `put`/`get`/`has` abstraction, kept a
`Protocol` rather than a filesystem client: this migrated layer may import
only `model` (`storage/AGENTS.md`'s "Permitted imports"), so it cannot itself
wrap ADR-059's `snapshot_io.py` envelope — a concrete, `.tar.zst`-transportable
store is a separate implementation, outside this package, built over both
this module and `snapshot_io`. `InMemoryObjectStore` is the one
process-local reference implementation `package.py` itself ships.

**`abicheck/project_snapshot_store.py`** (flat-root, deliberately outside
`storage/` for the identical import-layering reason `ObjectStore`'s own
docstring already gives) is A1.1's real, directory-backed store:
`DirectoryObjectStore` implements `ObjectStore` over ADR-059's
`snapshot_io.py` envelope — every object written zstd-compressed at
`objects/sha256/<aa>/<digest>.json.zst` (or `.bin.zst` for a raw binary
payload), content-addressed and deduplicated exactly as D7 describes.
`write_project_manifest`/`read_project_manifest` (plus the lazy
`read_manifest_summary`/`read_variant_ref`/`read_artifact_ref` primitives
the eager reader is built from) fan a `PackageManifest` out across the rest
of D6's directory tree: `manifest.json` carries only `versions` and the two
id lists — not the full embedded records `PackageManifest.to_dict()` itself
still returns for the in-memory convenience — with each variant/artifact's
full record at its own `refs/variants/<id>.json`/`refs/artifacts/<id>.json`.
**The deterministic `.tar.zst` transport form D6 also describes is not
implemented** — everything above operates on a real directory only.

**`abicheck/storage/import_v1.py`**'s `import_legacy_snapshot` takes one
already-serialized legacy document (`serialization.snapshot_to_dict()`'s
shape — `storage/` cannot import `serialization.py` itself, so a caller
outside the package builds the document) and an `ObjectStore`, and returns a
one-artifact, one-variant `PackageManifest` with the object content already
written into the store — A1.2 and A1.3 together. `StorageVersions.
source_schema_version` is carried through from the document's own
`schema_version` key unchanged, so a migration or audit can always answer
"what producer epoch actually emitted this" (D2). **What is actually
migrated onto a typed, D8-constrained representation today is
`semantic_ir`/`semantic_ir_conflicts` alone** (`storage/dto.py`'s
`semantic_ir_to_dto`/`semantic_ir_from_dto`, built on
`storage/semantic_ir_codec.py`'s `semantic_ir_to_document`/
`semantic_ir_from_document` — extracted from that module's existing
snapshot-dict-mutating `encode_semantic_ir`/`decode_semantic_ir` into a pure
object-in/document-out pair this DTO layer builds on rather than
duplicates). **Every other field the legacy document carries is now split
across D8's own named `binary`/`declarations`/`types`/`layout`/`debug`/
`build`/`graph`/`provenance` sections too** (`storage/legacy_sections.py`'s
`split_legacy_document`/`join_legacy_document`): one explicit, reviewed
allowlist per section (`_SECTION_FIELDS`) names exactly which top-level
document keys belong to it, checked in both directions (an unassigned key
on import, or a key outside its own section's allowlist on export, is a
hard `ValueError`, never a silent drop into a catch-all). What this split
does *not* do is decode a section's own internal shape into a typed domain
object the way `semantic_ir` is — `elf`/`dwarf`/`build_source`/... still
carry exactly the JSON `serialization.snapshot_to_dict()` already produced,
just inside their own now-independently-versioned, content-addressed
section rather than one shared blob. `import_v1.export_legacy_snapshot` is
the exact inverse of the import side: given an `ArtifactRef` and the
`ObjectStore` it was written into, it reads every section back, migrates
each to its section kind's current version, and reassembles the original
`snapshot_from_dict()`-shaped document, `schema_version` included.

**Not yet implemented, and still open**: folding baseline sets and
`BundleFacts` into sections (A1.4/A1.5), storing `BuildSourcePack`/project
source graphs/toolchain profiles once per project and referencing them by
digest, `bundle_variants:` CLI/config wiring (A1.6/A1.7), non-ELF artifact
membership specifics beyond `ArtifactRef.kind` (A1.8), and the `.tar.zst`
transport form. Decoding a legacy section's *internal* shape into a typed
domain object (rather than carrying the existing JSON as-is), and giving
`ArtifactRef.sections` a per-section `FactAvailability` (the "known,
deliberately deferred gap" below), are both real future work this landing
does not attempt.

**A known, deliberately deferred gap** (flagged in review, Codex): `ArtifactRef.sections`
has no accompanying D3 `FactAvailability`/`AvailabilityLedger` per section, so an
absent section key cannot yet distinguish "not collected" from "unsupported"
from "failed" the way a real producer will eventually need to report. Not
closed here because D8's per-section content schemas still don't exist for
most section kinds — wiring D3's vocabulary in now would mean guessing at a
shape (per-artifact? per-section-kind?) with nothing real to validate the
guess against, exactly the premature-design risk this file's own "Known
gaps over risky reactive patches" convention warns about. Revisit once
A1.4/A1.5 (folding sections/`BundleFacts`) gives this a real, multi-section
producer to design against.

**A second, deliberately deferred gap**: `import_legacy_snapshot` writes an
empty `ArtifactRef.native_identity` — a legacy document's `build_id` field
means an opaque CI identifier and is explicitly not reused for D6's native
binary identity (content SHA-256, ELF build ID, Mach-O UUID, PE/PDB
identity); populating it needs the artifact's own binary, which an adapter
operating on an already-serialized document does not have. Real, separately-
scoped future work for whichever caller has the binary in hand at import
time.

**A third, deliberately deferred gap**: `write_project_manifest` writes
`refs/*.json` before `manifest.json` (the previous gap's own fix), which
makes *first publication* of a set of ids safe but not *republishing
changed content under ids that are already live* — a second call against
a package another reader might concurrently be loading can overwrite a
ref file the currently-published manifest still names. Closing it needs
either a staged-directory-then-atomic-root-swap publish protocol or
content-addressed (never-overwritten) ref paths, and no caller in this
landing republishes an existing package (every current caller creates a
fresh one), so there is no real update caller yet to design the fix
against — see the function's own docstring. Revisit once A1.6/A1.7
(variant capture, stored/live comparison) gives this a real caller.

Tests live in `tests/unit/storage/test_project_package.py` (the object
model), `tests/unit/storage/test_dto.py` (the DTO envelope, including a
property test that payload key insertion order never changes the persisted
bytes — the general form of ADR-063 Phase 8's own D8 test requirement),
`tests/unit/storage/test_import_v1.py` (the import adapter), and
`tests/test_project_snapshot_store.py` (the directory-backed store and
manifest/ref writer/reader, including a full package round trip through a
real directory) — the same property-style-plus-example-cases convention as
Phase 0 (A0.6/A1's "Validation corpus" identity-preservation cases).

### Documentation ownership

`docs/AGENTS.md` requires every **new public-facing feature or surface** to
register a topic in `docs/_meta/topics.yaml` in the same PR, and a Phase-0
reviewer asked why storage v2 had none yet (Codex review). At the time the
answer was that neither Phase 0 nor A1.1's own first slice added such a
surface: no CLI command or flag, no report field, no config namespace, no
Action input, and nothing in the product produced, consumed, or persisted a
byte through these primitives yet — `package.py` performed no filesystem or
network I/O, and `ObjectStore` was a protocol plus one in-memory reference
implementation, not a place any real package was written to or read from.

**That Phase-0-era gap is closed.** As stated then, the trigger was
concrete rather than "later": the PR that first *persists* a
`ProjectSnapshot` — this file's own "Landed in Phase 1" section above,
`abicheck/project_snapshot_store.py`'s real, directory-backed
`DirectoryObjectStore`/`write_project_manifest`/`read_project_manifest` — is
the one that made this user-facing, and it registered the topic
`docs/_meta/topics.yaml` already commits to below. The registry's
`canonical_page` is required to be the one *published* narrative page a
human reads (never `contribute/`), which is why the trigger names a real
`reference/` page rather than pointing back at this plan document:

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
      - abicheck/storage/package.py
      - abicheck/storage/dto.py
      - abicheck/storage/legacy_sections.py
      - abicheck/storage/import_v1.py
      - abicheck/project_snapshot_store.py
```

**Still not part of the documented Python API**: `abicheck/__init__.py`
does not re-export any of these, and the separate `python-api` topic's
`fact_sources` still name only the `service*` modules that page actually
describes — registering the `project-snapshot-storage` topic above answers
"is this documented for a reader who finds `reference/project-snapshot-
format.md`", not "is this part of the stable Python API", which remains a
distinct, later question this landing does not answer either way.

**Deliberately not done in Phase 0**, so that no existing behavior changed
at that point: nothing produced, consumed, or persisted these types yet;
`AbiSnapshot.index()` still resolved first-wins; `SCHEMA_VERSION` was
untouched; and no CLI surface, report field, or exit code moved. Phase 1's
own landing above states plainly that this is still true of everything
`dump`/`compare`/`scan` read or write today — only a real filesystem
package outside that pipeline exists now.
