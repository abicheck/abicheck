---
doc_type: contributor
level: expert
lifecycle: active
generated: false
---

# G40 — Content-addressed bundle archive format

**Status:** Proposed; not started.

**Routing note (ADR-061):** this plan's implementation targets are qualified
against the root `AGENTS.md`'s "Task routing and dependency direction"
table (sourced from
[ADR-061](../adr/061-responsibility-package-architecture.md)). Storage
format/schema/migration ownership belongs to `storage/`; see "Design" and
"Files & surfaces" below. `storage/` did not yet exist as a physical package
when this plan was written — creating it is explicitly sanctioned by ADR-061
D2 ("a directory is created only when at least one implementation and its
tests move into it"), not a deviation from the migration.

Split out of [G38](g38-bundle-facts-model-and-multibuild-comparability.md)
Phase 2's own "deliberately NOT attempted" note (the external review's §9
`bundle-dump-vNext.tar.zst` sketch) — deferred there explicitly as "a real,
separate storage-architecture project on the scale of
[ADR-059](../adr/059-compressed-snapshot-storage.md) (snapshot compression)
or [G32](g32-comparability-contract-and-multi-tu-manifest.md) (multi-TU
manifest)", not a sub-step of making the bundle layer stored-data-capable.
This document is that separate scope: a design, not an implementation. No
code changes ship with this plan.

## Problem

`abicheck/bundle_facts.py`'s `BundleFacts` (G38 Phase 2) is the persisted,
stored-baseline counterpart to a live `compare_bundle()` run — but its
storage shape, as shipped, is the simplest one that could work, not the one
this plan's own docstring already flags as a real, separate need once usage
grows:

- `save_bundle_facts()`/`load_bundle_facts()` (`serialization.py`) write
  **one JSON document** — `bundle_facts_to_dict(facts)`, containing every
  matched library's full, inline `AbiSnapshot` dict — through the existing
  ADR-059 whole-file compression envelope (`snapshot_io.py`'s plain/gzip/
  zstd magic-byte detection). There is no per-library addressing: reading
  *one* library's snapshot out of a bundle facts file means decompressing
  and parsing the entire document, however many libraries the release
  actually has.
- No content deduplication across libraries. A release commonly ships
  several DSOs that share a large fraction of parsed declarations — a common
  umbrella header tree (the exact oneDAL-shaped scenario G38 Phase 13's own
  follow-up assessment used), or a static utility library re-linked into
  multiple `.so`s. Today each library's snapshot is fully independent JSON,
  so shared content is stored — and, on load, re-parsed into Python objects
  — once per library, not once per distinct value.
- No raw-binary retention option. The persisted facts capture only what
  `AbiSnapshot` already models; a consumer that later needs something the
  original capture didn't extract (a new `ChangeKind`'s required field, an
  L4 replay against source not available at capture time) has no fallback
  short of re-capturing from the original binaries.

G38 Phase 2 named this and deliberately declined to build it *as part of*
making the bundle layer stored-data-capable, on the reasoning that
`BundleFacts` should be scoped to "enough to reproduce `compare_bundle()`'s
existing analysis without live binaries," not "a general-purpose reanalysis
substrate for extractors that don't exist yet." That reasoning still holds
for the general-purpose/extractor-agnostic framing — this plan does **not**
revisit it — but the narrower, already-real cost (whole-document reads,
duplicated shared content across a real multi-library release) is worth its
own scoped design, informed by what `BundleFacts` actually looks like in
production per the G38 Phase 2 deferral's own stated trigger for revisiting
this ("If a future need for the latter materializes, it gets its own plan,
informed by whatever `BundleFacts` looked like in production by then").

## Goal & acceptance criteria

A stored bundle-facts artifact that (a) can have one library's snapshot read
without touching any other library's data, (b) stores one copy of a
byte-identical parsed snapshot shared by multiple libraries, and (c) is
fully backward-compatible with every `BundleFacts` file already produced by
the shipped G38 Phase 2 format — never a breaking format bump for an
existing consumer.

### Phase 0 — container format decision (S)

**Zip, not tar**, deliberately deviating from the original review sketch's
`bundle-dump-vNext.tar.zst` name — recorded here explicitly since it's a
real design decision, not a typo. A `tar` (even `.tar.zst`, zstd-framed) is
a sequential-access format: finding member N's content means scanning the
archive's central structure member-by-member (or, for a solid zstd frame
over the whole tar stream, decompressing from the start) — there is no
standard random-access index. `zip` carries a real end-of-file central
directory naming every member's offset and *independently compressed*
length, so `Python`'s stdlib `zipfile.ZipFile.open(name)` reads and
decompresses exactly one member without touching any other — which is
acceptance criterion (a) directly, using a well-understood standard library
module rather than hand-rolling seek logic over a zstd frame boundary. Each
member is stored zstd-compressed *individually* (Python 3.14's stdlib
`zipfile` gains native Zstandard support; until this project's floor
(3.10+, per `AGENTS.md`) allows relying on that unconditionally, member
payloads are zstd-compressed bytes stored with zip's own `ZIP_STORED`
method, mirroring how `snapshot_io.py` already treats zstd as a payload
transform independent of the outer container rather than delegating framing
to a library-specific codec) — so per-member random access and per-member
compression both hold, unlike a single whole-archive zstd frame.

### Phase 1 — content-addressed store, whole-snapshot granularity (M)

**Implementation location (ADR-061):** the root `AGENTS.md`'s routing table
names `storage/` for "Serialize snapshots/baselines, own their schemas/
migrations, or manage caches" — this plan's entire subject. `storage/` did
not exist as a physical package at the time this plan was written (only
`report/`, `frontends/`, `workflows/`, plus the pre-existing `buildsource/`,
`impact/`, `policies/`, `schemas/`, `compat/` had migrated); creating it here
is the explicitly-sanctioned first-mover case ADR-061 D2 describes. New code
under this plan therefore targets `abicheck/storage/bundle_archive.py`, not
a new flat root module — `bundle_archive.py` would otherwise be rejected by
`scripts/check_architecture.py`'s `frozen-root-family` rule the moment it's
added: `bundle_` is already a frozen root family
(`architecture/modules.yaml`) with an explicit, closed member list (`bundle.py`,
`bundle_facts.py`, `bundle_manifest.py`, ... — every file G38 already
shipped), and that check rejects a *new* sibling in a frozen family outright
("new root sibling is forbidden; create the responsibility package owner").
`abicheck/bundle_facts.py`/`bundle_manifest.py` (the existing G38 modules
this plan's `BundleArchive` reads from) stay where they are — this plan adds
a new storage-format module, not a migration of those.

New module, `abicheck/storage/bundle_archive.py`:

```python
@dataclass(frozen=True)
class BundleArchiveManifest:
    schema_version: int
    variant_fingerprint: str
    # canonical_library_name -> content hash of its serialized AbiSnapshot
    library_blobs: dict[str, str]
    manifest_blob: str | None  # InstantiationManifest, if present
    filesystem_aliases: dict[str, tuple[str, ...]]
    library_filenames: dict[str, str]
```

Layout inside the zip: `manifest.json` (the `BundleArchiveManifest` above,
uncompressed member — always readable without touching the blob store,
mirroring `bundle_facts.py`'s existing top-level fields) plus one member per
*unique* content hash under `blobs/<sha256-hex>.json.zst`. The blob store is
not `AbiSnapshot`-only: it holds any hashed, serialized payload this format
needs — the per-library `AbiSnapshot` dicts (referenced by
`library_blobs`), **and**, when `BundleFacts.manifest` is non-`None`, the
serialized `InstantiationManifest` itself (referenced by `manifest_blob`,
using `bundle_manifest.py`'s own existing `to_dict`-shaped serialization —
the same one a plain-JSON `BundleFacts` already uses for this field). An
earlier draft of this plan named `manifest_blob: str | None` in the
manifest dataclass but never specified where the referenced content
actually lives — corrected here: it's a blob like any other, addressed the
same way, not a second, undocumented storage mechanism.

**The reader must re-verify every blob's content hash before returning it
(Codex review) — part of the reader's own contract, not optional
hardening.** A manifest's `library_blobs`/`manifest_blob` values are
sha256 hex digests naming a member by content address, but a member's own
zip entry name is just a string a corrupted or hand-assembled archive
could set to anything — nothing about opening `blobs/<hash>.json.zst` and
decompressing it proves the decompressed bytes actually hash to `<hash>`.
Without that check, a tampered/corrupted archive whose payload still
happens to decompress to valid zstd/JSON would silently load as a
*different* `AbiSnapshot`/`InstantiationManifest` than the one the
manifest's own reference claims — defeating content-addressing entirely,
with no error. The reader **must** recompute the decompressed payload's
hash and reject a mismatch before returning it — part of Phase 2's
acceptance criteria, with a dedicated corruption test in Phase 2's own
test list below.

`manifest_blob` is `None` exactly when `BundleFacts.manifest is None` (no
instantiation manifest was captured), matching that field's own existing
optionality.
Every hash — library or manifest — is computed over its own canonical JSON
encoding (the existing `snapshot_to_json`-style deterministic serialization
G38 Phase 13's own `save_bundle_facts` docstring already documents caring
about — instantiation-order-sensitive fields must never be key-sorted, so
the hash input is the same non-`sort_keys` encoding the plain-JSON path
already writes, not a re-derived canonical form).

**Deduplication granularity is the whole per-library `AbiSnapshot`, not
individual declarations — and the within-one-bundle motivating case named
in an earlier revision of this section does not actually reproduce (Codex
review, verified against the code).** `AbiSnapshot.library: str` is a
required, always-distinct-per-library field (`model.py`), and
`snapshot_to_dict()` also serializes `source_path`, mtimes/sizes, and each
DSO's own ELF/PE/Mach-O metadata block — none of which two genuinely
different libraries share, even when re-linking the identical static
utility into both. So "a static archive re-linked into two DSOs" does
**not** produce two byte-identical serialized snapshots in practice; the
only way to observe this dedup path firing is the degenerate case of
capturing (or testing with) the literal same `AbiSnapshot` object under two
map keys. The real, still-genuine benefit this granularity delivers is
**cross-capture**, not cross-library, dedup: re-saving one library's own
unchanged `AbiSnapshot` in a later `BundleFacts` capture (the common CI
shape — most libraries in a release are unchanged run to run) reuses its
existing blob, since that *is* the same object's own content reproduced.
Corrected acceptance criterion: Phase 1's own tests exercise cross-capture
reuse (two `save_bundle_facts` calls a fixed number of days apart for one
unchanged library, sharing a blob) as the primary case, with the
within-one-bundle shared-content case named only as a real but rare
possibility (identical build output for two genuinely distinct libraries),
not the motivating scenario.

Individual-declaration-level dedup (sharing one blob per struct/function
across libraries, not per whole snapshot) is out of scope for the identical
reason as before: `AbiSnapshot` has no notion of a separately-addressable,
individually-hashable declaration today (it's one flat structure per
library, not a graph of independently-identified nodes) — building that
would be a model change reaching every consumer of `AbiSnapshot.functions`/
`.types`/etc., not a storage-layer change, and is explicitly **not**
attempted here (see "Out of scope").

### Phase 2 — lazy reader (S)

```python
class BundleArchive:
    @classmethod
    def open(cls, path: Path) -> "BundleArchive": ...
    def manifest(self) -> BundleArchiveManifest: ...              # reads only manifest.json
    def load_library(self, name: str) -> AbiSnapshot: ...         # reads only that library's blob
    def load_instantiation_manifest(self) -> InstantiationManifest | None: ...
        # reads only the manifest_blob member, or returns None without
        # touching the blob store when BundleArchiveManifest.manifest_blob is None
    def close(self) -> None: ...
```

`load_library` decompresses and parses exactly the one referenced blob member
(acceptance criterion (a)); a caller wanting every library still pays the
full cost, but a caller wanting one library out of a fifty-library release
(the CLI-blocked-but-real `compare_release_against_bundle_facts()` per-
library matching loop from G38 Phase 13, once it has a CLI surface —
[the still-open half of G38's own Known gap](g38-bundle-facts-model-and-multibuild-comparability.md))
no longer pays for the other forty-nine.

Decompression-bomb limits mirror `snapshot_io.py`'s existing discipline for
the plain-JSON path (ADR-059) — applied per-member here rather than to one
whole-document read, so a single oversized blob can't exhaust memory on a
`load_library` call for an unrelated, small library elsewhere in the same
archive.

### Phase 3 — CLI/API wiring (S)

**`load`/`save` get separate, unambiguous contracts — deliberately not one
shared `"auto"` default.** An earlier draft of this plan gave both
`save_bundle_facts()` and `load_bundle_facts()` the same
`format: str = "auto"` parameter and defined `"auto"` as "sniff from the
path's own bytes" — a real definition for *loading* an existing file, but
meaningless for *saving* a new one: there are no bytes at the destination
path to sniff yet, so a save call with no explicit `format=` would have no
defined behavior. Fixed by giving each function its own contract:

- `save_bundle_facts(facts, path, *, format: str = "json")` — `"json"` is
  the explicit, unchanged default (today's only behavior, so no existing
  caller's output format changes), `"archive"` opts into this plan's zip
  format. No `"auto"` on the save side — there is nothing to sniff.
- `load_bundle_facts(path, *, format: str = "auto")` — `"auto"` (the
  default) sniffs the *existing* file's own bytes, mirroring
  `snapshot_io.py`'s magic-byte detection, so a caller reading a path
  without knowing which format produced it just works; `"json"`/`"archive"`
  remain available to force one path explicitly (e.g. for a test asserting
  which branch ran).

**`save_bundle_facts`'s existing `compression: str = "auto"` keyword-only
parameter is unchanged and stays in the signature** (Codex review: an
earlier revision of this section only listed `format` and implicitly
dropped it, which would have broken every existing caller passing
`compression="gzip"`/`"zstd"` and removed the suffix-selected compressed-
JSON behavior `snapshot_io.py`'s writer already provides). It applies
*only* to the `format="json"` branch — the outer, whole-document ADR-059
compression envelope this plan's own "Why zip, not `.tar.zst`" section
above already distinguishes from an archive member's own per-blob zstd
compression, which is unconditional and not user-configurable (every blob
is stored zstd-compressed regardless of `compression=`, the same way the
archive's own zip container is always `ZIP_STORED`, never `ZIP_DEFLATED`).
`save_bundle_facts(facts, path, format="archive", compression="gzip")` is
therefore not a contradiction to reject: `compression` is silently
inapplicable to that branch (ignored, not an error) since the archive
format has no whole-document compression envelope of its own for it to
select — stated explicitly here so the implementation doesn't have to
invent a rejection rule this plan never asked for.

`BundleFacts` itself is unchanged — this plan is a storage-layer addition
underneath the existing dataclass, not a new in-memory shape;
`load_bundle_facts()` still returns a plain `BundleFacts` when a caller
wants the whole-bundle load path unchanged, with `BundleArchive` as the new,
separate lazy-access API for a caller that specifically wants per-library
loading.

### Phase 4 — migration (S)

No breaking change to any existing file: `BundleFacts.schema_version`
(currently `1`) is untouched by this plan — a plain-JSON `BundleFacts` file
is not "archive format at version 1", it's simply not an archive at all, and
`load_bundle_facts()`'s `"auto"` sniff routes it through the unchanged
plain-JSON path forever. A converter,
`abicheck/storage/bundle_archive.py`'s `convert_to_archive(src: Path, dst:
Path)`, is provided for a caller who wants to opportunistically re-save an
existing plain-JSON `BundleFacts` file in the new format; never required.

## Design

Deliberately **not** the review's original "raw-binary retention" option —
the original binaries a capture ran against already exist wherever they
were captured from; duplicating them inside the facts archive doubles
storage for data the archive format itself has no way to keep in sync with
the source binary's own lifecycle (a rebuild, a rename). If a future
concrete need for exactly-reproducible raw-binary retrieval materializes,
that's its own follow-up, informed by a real use case rather than speculative
inclusion now — the identical "no general-purpose reanalysis substrate for
extractors that don't exist yet" reasoning G38 Phase 2 already used to defer
this whole plan.

Deliberately reuses, rather than reinvents: `snapshot_io.py`'s magic-byte
container detection precedent (the loader's `"auto"` sniff mirrors it
exactly), ADR-059's decompression-bomb-limit discipline (Phase 2), and G38
Phase 2's own deterministic-serialization requirement for the manifest's
instantiation-order-sensitive fields (Phase 1's hash input).

## Files & surfaces

- `abicheck/storage/bundle_archive.py` — new: `BundleArchiveManifest`,
  `BundleArchive`, `convert_to_archive` (see Phase 1's routing note above
  for why this is `storage/`, not a new flat root module).
- `abicheck/serialization.py` — `save_bundle_facts`/`load_bundle_facts`
  gain the `format` parameter described in Phase 3; delegate to
  `storage/bundle_archive.py` for the archive branch. This module is
  itself pre-ADR-061 flat-root — whether it stays the public entry point or
  becomes a thin delegation facade over a `storage/`-owned equivalent by
  implementation time is a call for whoever lands this plan, informed by
  how far the `storage/` migration has progressed by then; either way, the
  archive *logic* lives in `storage/bundle_archive.py`, not duplicated here.
- `abicheck/snapshot_io.py` — no changes; `storage/bundle_archive.py`
  follows its precedent rather than extending it (the archive's own
  zip-member framing is a different mechanism from the single-stream
  plain/gzip/zstd detection `snapshot_io.py` owns, so this stays a sibling
  module, not a modification to a leaf module several other formats already
  depend on).
- `tests/test_bundle_archive.py` — new.

## Tests

- Round-trip: a multi-library `BundleFacts` saved as an archive and reloaded
  (both via `load_bundle_facts()`'s `"auto"` sniff and via `BundleArchive.open`
  directly) reproduces the identical per-library `AbiSnapshot`s.
- **Round-trip with a non-null `InstantiationManifest`** — the manifest-blob
  gap an earlier draft of this plan left unspecified (see Phase 1's own
  correction note): a `BundleFacts` whose `manifest` is populated, saved as
  an archive, reloaded via both `load_bundle_facts()` (which must populate
  `BundleFacts.manifest` from the archive, not silently drop it) and
  `BundleArchive.load_instantiation_manifest()` directly, reproduces the
  identical manifest; a `BundleFacts` with `manifest=None` round-trips to
  `manifest_blob=None` with no `blobs/` member allocated for it.
- **Partial-load, verified at production scale, not a toy fixture** — per
  `AGENTS.md`'s own "Third-party-boundary tests must exercise the real
  public API at realistic scale" convention (the zstd-`max_window_size`
  incident that convention exists to prevent): a real, multi-library archive
  (not a two-field stub) where `load_library("one_of_many")` is asserted,
  via a patched/instrumented `zipfile.ZipExtFile` or a member-read counter,
  to open and decompress **exactly one** member — proving lazy access is
  real, not merely API-shaped.
- Dedup: primarily a **cross-capture** test (matching the corrected
  motivating case above) — two `save_bundle_facts` calls for the same
  unchanged library's `AbiSnapshot` share a blob; a same-bundle,
  two-different-library-names test is included too but is a synthetic,
  same-object-under-two-keys construction, documented as such rather than
  presented as evidence the design reduces real production duplication.
  Either way the underlying archive contains exactly one
  `blobs/<hash>.json.zst` member for the shared content, not two.
- **Corruption: a tampered blob is rejected, not silently mis-loaded**
  (Codex review) — a valid archive with one blob member's content replaced
  in place (still valid zstd/JSON, but no longer matching its own member
  name's hash) must raise on load, not return the substituted content
  under the original, now-incorrect content address.
- Back-compat: every existing plain-JSON `BundleFacts` fixture in this
  repo's test suite still loads unchanged via the `"auto"`-sniffing path
  once this plan ships, with **no** re-save required.
- Migration round-trip: `convert_to_archive` over an existing plain-JSON
  fixture, then load via both paths, asserting identical `BundleFacts`.

## Example fixtures

A small (2-3 library) synthetic bundle archive fixture for the round-trip/
partial-load/dedup tests above; no new `examples/case*/` entry required —
this is a storage-format concern, not a new detected-change scenario, so it
doesn't fit that catalogue's own scope (per its own README: one case per
distinct detection scenario).

## Effort & risk

**L** — smaller than G39: one new leaf module, one additive parameter on an
already-small, already-isolated pair of functions
(`save_bundle_facts`/`load_bundle_facts`), no changes to detection logic, no
FP-rate/mutation-score gate involvement (this plan touches storage, not
detectors). Main risk is getting the zip random-access contract right under
`AGENTS.md`'s own "third-party-boundary" testing discipline — verified via
the partial-load test above, not asserted from documentation alone.

## Out of scope

- **Declaration-level content addressing** (a genuinely shared header's
  individual struct/function declarations deduplicated across libraries,
  not just whole identical snapshots) — needs `AbiSnapshot` itself to expose
  stable, independently-hashable per-declaration identity, a model change
  reaching every consumer of `.functions`/`.types`/etc., not a storage-layer
  addition. Left for its own follow-up if Phase 1's whole-snapshot dedup
  proves insufficient in real usage — the same "informed by production
  usage, not built speculatively" principle this plan itself was deferred
  under from G38.
- **Raw-binary retention** — see "Design" above.
- **A general-purpose, extractor-agnostic reanalysis substrate** — G38 Phase
  2's own deferral reasoning for this stays intact; this plan is scoped to
  the concrete, already-real storage-shape cost (whole-document reads,
  duplicated identical content), not a speculative future extractor
  interface.
- **Migrating G38 Phase 13's `compare_release_against_bundle_facts()` (or
  any other existing caller) onto the archive format by default** — this
  plan adds the capability as opt-in; deciding whether/when an existing
  caller should switch its own default is a separate, later decision once
  the format has real production mileage.
