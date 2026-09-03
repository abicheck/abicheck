---
doc_type: contributor
level: expert
lifecycle: active
generated: false
---

# G40 — Content-addressed bundle archive format

**Status:** Substantially implemented (PR #869) —
`abicheck/storage/bundle_archive.py` (`BundleArchiveWriter`/
`BundleArchiveReader`), `abicheck/storage/bundle_archive_cd_guard.py`,
`abicheck/storage/bundle_archive_json_guard.py`, and
`abicheck/bundle_facts.py`'s `write_bundle_facts_archive()`/
`read_bundle_facts_archive()` ship the design below on the
`claude/g40-bundle-archive-impl` branch, verified against several rounds
of review (the "Codex review, fresh evidence, verified against the real
implementation in PR #869" annotations throughout this document, added as
the implementation and this plan were revised together). This document now
also serves as a retrospective design record for that implementation, not
only a forward-looking proposal — where a section still reads as
prescriptive ("must", "should"), that reflects a requirement the shipped
code satisfies, not a requirement still pending. Two still-open items were
identified during that work, both left as explicit, documented known
limitations rather than blocking the rest of the design: the
manifest-integrity gap in Phase 2 below (no reader-side binding on
`manifest.json` itself), and a lazy per-library reader's missing
schema checks (`BundleArchiveReader.read_manifest()`/`read_blob()` check
neither the container's own `schema_version` nor the encoded
`bundle_facts_schema_version` at all, unlike the whole-bundle
`read_bundle_facts_archive()` path, which checks both) — see those sections
for what closing each would need. Historical framing below
("the container format decision", "Phase 0", "Phase 1", etc.) is
unchanged; treat "the writer"/"the reader" language throughout as
describing the shipped `BundleArchiveWriter`/`BundleArchiveReader`, not a
still-hypothetical design.

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
This document was originally that separate scope as a design-only proposal.
Per the "Status" note above, the design has since been implemented on the
`claude/g40-bundle-archive-impl` branch (PR #869) — not yet merged to
`main`. No code changes ship with **this** plan-document PR; the
implementation itself is tracked and reviewed separately in PR #869.

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
without touching any other library's data, (b) stores one copy per *unique*
serialized-snapshot content within a single archive write — i.e. two map
entries that happen to reference the byte-identical `AbiSnapshot` collapse
onto one stored blob — and (c) is fully backward-compatible with every
`BundleFacts` file already produced by the shipped G38 Phase 2 format —
never a breaking format bump for an existing consumer.

**Criterion (b) is deliberately narrower than it may first read, and the
"Design" section's dedup-correction subsection below states exactly how
narrow.** Two genuinely distinct libraries — even a shared static
utility re-linked into both — do **not** produce byte-identical serialized
snapshots in practice: `AbiSnapshot.library`, `source_path`, mtimes/sizes,
and each DSO's own ELF/PE/Mach-O metadata block are always
per-library-distinct, so criterion (b) does not, by itself, close the
storage-duplication problem this plan's own "Problem" section opens with
(shared headers or a shared static library across several DSOs in one
release). What (b) delivers is real but narrower: within one archive write,
two map keys that reference the literal same already-serialized snapshot
object collapse onto one stored blob instead of two. Closing the
real-world, multi-library storage-duplication case — sharing content
*across* genuinely distinct libraries, or *across* separate captures/archive
files — is future work, out of scope for this plan; see "Design"'s
dedup-correction subsection and "Out of scope" below for what each would
require.

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
`zipfile` gains native Zstandard support; until this project's own minimum
supported Python version — `pyproject.toml`'s `requires-python`, per
`AGENTS.md`, which is the one fact owner for that number — allows relying
on that unconditionally, member payloads are zstd-compressed bytes stored
with zip's own `ZIP_STORED` method, mirroring how `snapshot_io.py` already
treats zstd as a payload transform independent of the outer container
rather than delegating framing to a library-specific codec) — so
per-member random access and per-member compression both hold, unlike a
single whole-archive zstd frame. **The dependency contract this rests on,
made explicit rather than left implicit:** `zstandard` (already a core,
non-optional dependency declared in `pyproject.toml` — `zstandard>=0.21`,
a minimum-version floor, not a pin to one exact release; see
`pyproject.toml` itself for the current constraint rather than restating
it here — the same package `snapshot_io.py` already depends on) supplies
`zstandard.ZstdCompressor`/`zstandard.ZstdDecompressor` for compressing and
decompressing each member's payload bytes; those bytes are then written
into the zip archive using zip's `ZIP_STORED` method — not zip's own
`ZIP_ZSTANDARD` compression method constant (`zipfile.ZIP_ZSTANDARD`,
Python 3.14+) — so there is no zip-extension version guard to write: the
compression happens entirely at the payload-bytes layer, before the zip
container ever sees them, and the container itself just stores whatever
bytes it's given.

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
`write_bundle_facts_archive`/`read_bundle_facts_archive` read from) stay
where they are — this plan adds a new storage-format module, not a
migration of those.

New module, `abicheck/storage/bundle_archive.py`. **As shipped (PR #869),
`abicheck/storage/bundle_archive.py` deliberately knows nothing about
`BundleFacts`/`AbiSnapshot` (see that module's own docstring, quoted under
"Files & surfaces" below) — there is no `BundleArchiveManifest` dataclass
in it; the manifest is a plain `dict[str, Any]`, written via
`BundleArchiveWriter.write_manifest(manifest: dict)` and read back via
`BundleArchiveReader.read_manifest() -> dict`. The `BundleFacts`-aware
shape below is `abicheck/bundle_facts.py`'s own manifest-dict *contract*
with that primitive — the keys `write_bundle_facts_archive()`/
`read_bundle_facts_archive()` write and expect, not a type either module
defines:

```python
# manifest.json's shape, as a dict -- not a dataclass:
{
    "artifact_type": str,  # BUNDLE_ARCHIVE_ARTIFACT_TYPE (CLI cleanup
    # phase two, PR I prerequisite) -- required, not defaulted; rejects a
    # missing/mismatched marker before any other key is trusted
    "schema_version": int,  # the *container's own* layout version
    "bundle_facts_schema_version": int,  # the encoded BundleFacts' own version
    "variant_fingerprint": str,
    # canonical_library_name -> content hash of its serialized AbiSnapshot
    "library_blobs": dict[str, str],
    "manifest_blob": str | None,  # InstantiationManifest, if present
    "filesystem_aliases": dict[str, list[str]],
    "library_filenames": dict[str, str],
}
```

**Two independent schema versions, not one (Codex review, fresh evidence):**
the archive manifest's own layout (this dataclass's shape) and the
`BundleFacts` it encodes (`BundleFacts.schema_version`) evolve on separate
schedules — a manifest-layout change (e.g. a new top-level field) says
nothing about whether the `BundleFacts` shape inside it changed, and vice
versa. A single `schema_version` field would force a reader to conflate the
two: bump it for a manifest change and an old reader can no longer tell
"the container changed" from "the facts changed," or skip bumping it for a
facts-only change and lose the rejection this whole mechanism exists to
provide. `schema_version` gates the container's own shape (checked against
this format's own version constant before reading any other manifest
field); `bundle_facts_schema_version` is carried through opaquely and
handed to the same version check `bundle_facts_from_dict` already performs
for the plain-JSON path, so both axes reject a too-new value independently
and the two consumers (the archive reader, `BundleFacts`'s own loader) each
answer only the question they own.

Layout inside the zip: `manifest.json` (the manifest dict above,
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

**Known limitation: `manifest.json` itself has no integrity binding a
reader checks (Codex review) — the per-blob hash check above protects each
*blob* against the manifest naming the wrong one, but nothing protects the
manifest's own content.** A tampered `manifest.json` — still valid JSON,
still naming real (or attacker-supplied) blob hashes — could redirect
`library_blobs` to point a library at a different, still-hash-valid blob,
or silently rewrite `variant_fingerprint`/aliases/filenames, and every
check this format defines would still pass: each blob's bytes genuinely
match the hash the (tampered) manifest asks for, so the mismatch is
invisible to `BundleArchiveReader.read_blob()`'s own verification. The one
mechanism this format provides toward closing this is write-time only:
`BundleArchiveWriter.stored_sha256`/`stored_size_bytes` (computed over the
*entire* published archive file — `manifest.json` included, not just the
blob store) are returned to the writer's caller
(`write_bundle_facts_archive()`'s own result), so a caller that persists
that digest in an external, independently-trusted channel (a release's own
publish manifest, a signed provenance record) can bind the whole archive
— manifest and all — to that record. But `BundleArchiveReader`/
`read_bundle_facts_archive()` accept **no** expected-digest parameter of
their own, so nothing in this format's *read* path enforces that binding
— a reader that opens an archive without independently re-checking
`stored_sha256` against some other trusted value has no protection against
this class of tampering. Closing this for real needs one of two follow-ups,
neither attempted by this plan: (a) an optional `expected_sha256` parameter
on `BundleArchiveReader.open()`/`read_bundle_facts_archive()`, so a caller
that already has a trusted digest (from a publish manifest, a signature) can
ask the reader to enforce it before returning anything; or (b) each
`library_blobs`/`manifest_blob` reference itself carrying a second,
manifest-external integrity anchor is not really possible without
restructuring what "the manifest" means, so (a) is the more natural fit.
Until either lands, a consumer of this format who needs tamper-evidence on
`manifest.json` itself must arrange whole-file digest verification outside
this module — this format's own reader offers none. A regression test
belongs alongside whichever of (a)/(b) actually lands (rewrite
`manifest.json` while keeping the surrounding zip and blob content
otherwise valid, and confirm the reader rejects it) — not added here, since
there is no enforcement mechanism yet for it to exercise.

`manifest_blob` is `None` exactly when `BundleFacts.manifest is None` (no
instantiation manifest was captured), matching that field's own existing
optionality.
Every hash — library or manifest — is computed over its own canonical JSON
encoding (the existing `snapshot_to_json`-style deterministic serialization
G38 Phase 13's own `save_bundle_facts` docstring already documents caring
about — instantiation-order-sensitive fields must never be key-sorted, so
the hash input is the same non-`sort_keys` encoding the plain-JSON path
already writes, not a re-derived canonical form).

**Publication must be atomic — this needs to be a stated part of the
`BundleArchiveWriter` contract, not left implicit (Codex review, fresh
evidence).** This document mentions "Phase 1's own atomicity design" later
(the dedup-correction section below), but never actually specifies one
here, where the writer's own API is defined — an implementation that
follows only the documented writer surface (`put_blob`/`write_manifest`/
`close`) has no requirement forcing it to avoid writing or truncating the
destination path directly. A writer that opens `path` itself (via
`zipfile.ZipFile(path, mode="w")` or equivalent) and something fails
mid-write — an oversized/unhashable blob, a disk-full `OSError`, a process
kill — leaves a truncated, unreadable zip at `path`, destroying whatever
valid archive was there before, exactly where a released baseline a
downstream reader depends on can least afford it. `BundleArchiveWriter`
must therefore write to a private, uniquely-named temporary file in the
destination's own parent directory (never a predictable name, and never
`/tmp` — a shared temp directory is writable by other users/processes and
a cross-filesystem rename cannot be atomic) and publish only via a single
`os.replace()` of that temp file onto the destination, performed once
every blob and the manifest have been written successfully — inside
`close()` on a clean context-manager exit, never before. Any failure
before that point (a raised exception from `put_blob`/`write_manifest`, an
exception propagating out of the `with` block) must leave the destination
path completely untouched — a `SnapshotError`, a full disk, or a killed
process must never partially overwrite a previously-valid baseline.

**Closing the temp file and calling `os.replace()` is not durable on its
own — the writer must fsync, matching `snapshot_io._atomic_write_bytes`'s
existing contract rather than inventing a weaker one for this format
(Codex review, fresh evidence).** `ZipFile.close()` only flushes Python's
own write buffer into the OS page cache; on a power loss or a delayed
storage-layer error, neither the temp file's own content nor the later
directory-entry rename it durable on their own. `_atomic_write_bytes`
already states the correct sequence for the plain-JSON path — flush the
Python buffer, `os.fsync()` the temp file's own fd, `os.replace()`, then
`os.fsync()` the *parent directory's* fd too (the directory-entry update
itself isn't durable until the directory inode is synced, skipped only
where the platform has no `O_DIRECTORY` to open it with) — and
`BundleArchiveWriter.close()` must follow the identical sequence rather
than a narrower one: fsync the temp file's fd after `ZipFile.close()`
(the payload write), fsync it again after any `fchown`/`fchmod` ownership
fixup (metadata changes made after the first fsync are not covered by
it), only then `os.replace()`, and finally fsync the destination's
parent-directory fd. A writer that skips any of these steps can report a
successful `stored_sha256`/`stored_size_bytes` while a reboot immediately
after leaves the destination missing, truncated, or holding stale bytes
the OS never actually wrote to disk — exactly the failure mode
`_atomic_write_bytes` already closed for the plain-JSON path, and this
format must not reopen it by inventing its own, less careful sequence.
This durability sequence is part of Phase 1's acceptance criteria and
needs its own dedicated test alongside the atomicity test below.

**The abandoned-temp-file cleanup guarantee must be scoped honestly — it
covers only a failure whose handling code actually gets to run, not every
way a write can stop (Codex review, fresh evidence).** This paragraph
previously stated flatly that "the abandoned temporary file must be
cleaned up rather than left behind," with no qualification — that promise
cannot be kept universally, and stating it unqualified invites a reader to
trust a guarantee no user-space code can deliver. If the writer process is
terminated with `SIGKILL`, the machine loses power, or the process is
OOM-killed, no `except`/`finally` block, no context-manager `__exit__`,
and no `atexit` hook runs — there is no code left executing to delete
anything, by construction, regardless of how carefully the writer is
implemented. The guarantee this contract can actually make, and the one
`BundleArchiveWriter` implements, is narrower and code-reachable: **on any
failure that unwinds through Python's own exception machinery** — a raised
exception from `put_blob`/`write_manifest`, an exception propagating out of
the `with` block, an explicit `close()`/`abort()` call — the writer's own
cleanup code runs and removes its temp file before control leaves the
writer. That boundary is exhaustive for every failure this plan's own test
list can inject (all of them are raised-and-caught Python exceptions), but
it is **not** a guarantee against a `SIGKILL`, a power loss, or an
OOM-killer termination — those leave a stale, uniquely-named temp file
behind in the destination's parent directory, and no writer-side code
change can close that gap, since the same mechanism that would need to run
the cleanup is the one that was killed. This does not weaken the atomicity
guarantee immediately above: a `SIGKILL`-abandoned temp file is inert dead
weight, never `os.replace()`d onto the destination (that call happens only
at the very end of a clean `close()`), so the destination path itself
stays exactly as untouched as the atomicity guarantee already promises —
only the *disk-space reclamation* of the orphaned temp file is out of
scope here, not the correctness of the published archive. Reclaiming an
orphaned temp file left behind by a killed process is therefore a
**separate, explicitly out-of-scope concern for this plan**: it would need
a GC-style sweep over stale `*.tmp`-named siblings in a destination's
parent directory (e.g. by age, run opportunistically or on a schedule) —
a different mechanism from anything a single `BundleArchiveWriter` call
can implement on its own behalf — and no such sweep is designed or
scheduled as part of G40. Recorded here as a known, accepted gap rather
than left as an implicit assumption a reader could mistake for a
delivered guarantee.

A destination that is itself a symlink is replaced at its resolved real
target, not by clobbering the link, mirroring `snapshot_io.
_atomic_write_bytes`'s existing symlink handling for the plain-JSON path —
this format should not invent a second convention for the identical
concern.

**A pre-existing destination with more than one hard link must be rejected
outright, the same way `_atomic_write_bytes` already rejects it for the
plain-JSON path (Codex review, fresh evidence).** `os.replace()` only ever
retargets the *one* directory entry the writer was pointed at — a
destination with `st_nlink > 1` has at least one other directory entry
still pointing at the same inode, and that sibling link keeps resolving to
the old, pre-replace content forever, silently desynchronized from the
name the write actually went through. `snapshot_io._atomic_write_bytes`
already treats this as a hard failure rather than a silent partial write
(stat the existing destination before writing the temp file; if it exists,
is a regular file, and `st_nlink > 1`, raise rather than proceed) — the
archive format's write path shares the identical publication mechanism
(temp file + `os.replace()`), so it inherits the identical risk and must
carry the identical guard, not a narrower one that only covers symlinks
and mode/ownership preservation. This is part of Phase 1's acceptance
criteria alongside the atomicity and durability guarantees above and needs
its own dedicated test: write an archive to a path that already has a
second hard link, and assert the writer refuses rather than silently
leaving the other link stale.

This atomic-publish behavior is part of Phase 1's acceptance
criteria and needs its own dedicated test in Phase 1's test list (see
below): write a real archive to a path, then attempt a second write that
is made to fail partway through (an injected error after some blobs are
written but before `write_manifest`/`close()`), and assert the original
file at that path is byte-for-byte unchanged — a failure-preserves-old-file
test, not merely a happy-path round-trip. This test — like the whole
cleanup guarantee it exercises — proves cleanup only for a catchable,
in-process failure (an injected exception); it says nothing about, and
cannot say anything about, a `SIGKILL`/power-loss/OOM-kill scenario, per
the scoping above. (The shipped implementation
already does this: `BundleArchiveWriter` writes to a randomized,
`O_CREAT|O_EXCL` sibling temp file and calls `os.replace()` only from
`close()`, using `fchown`/`fchmod` on the temp file's own open descriptor
— not a second, TOCTOU-able path-based reopen — to preserve a pre-existing
destination's ownership/mode before the swap; this paragraph exists so the
plan states that contract explicitly rather than leaving it as an
implementation detail a reader has to discover from the code.)

**Payload-level determinism alone does not make the *archive file's own*
`stored_sha256` reproducible — the zip container's own metadata must be
pinned too (Codex review, fresh evidence).** `zipfile.ZipFile.writestr(name,
data)` given a bare string `name` stamps its own `ZipInfo` with
`time.localtime()` at write time, and a `ZipInfo`'s own file-mode/
`create_system` (0 on Windows, 3 on Unix/macOS) default to the *host*
platform unless set explicitly — so writing byte-identical facts on two
different days, or on two different CI platforms, would otherwise produce
two different archives (and two different `stored_sha256` values) despite
every payload being identical. `BundleArchiveWriter` must therefore pin, on
every member it writes: a fixed timestamp (the zip format's own 1980-01-01
epoch floor, since DOS-style zip timestamps can't represent anything
earlier), a fixed, portable permission bit, and a fixed `create_system`
(Unix, matching this project's actual CI/release platforms) — plus, since
`library_blobs`/`filesystem_aliases`/`library_filenames` are unordered-by-
name maps rather than order-sensitive content, writing their manifest keys
in sorted order rather than whatever order the caller's own `BundleFacts`
mapping happens to iterate in.

**Pinning per-member metadata and manifest-key order is not sufficient on
its own — the *order the blob members themselves are written in* is also
part of the zip container's own bytes (central directory entries are
written in write order), and must be pinned the identical way (Codex
review, fresh evidence).** Two logically-equal `BundleFacts` values built
by populating `per_library_snapshots` in a different insertion order would
otherwise still produce different archive bytes even with every above fix
applied, since a naive `for name, snap in facts.per_library_snapshots.items():
writer.put_blob(...)` writes blobs in whatever order that dict happens to
iterate in. The write path must therefore compute every blob's content hash
first and emit `put_blob` calls in sorted-hash order (not sorted-by-name
order — two different library names can share one content hash, so a
name-sorted emission order is not itself uniquely determined by content),
so archive byte-identity depends only on the *set* of unique payloads, never
on construction order. A dedicated round-trip test (two saves of
logically-equal but differently-*constructed* `BundleFacts` values,
asserting byte-identical output and matching `stored_sha256`) belongs in
Phase 2's own test list, not deferred to an ad hoc discovery later.

**Deduplication granularity is the whole per-library `AbiSnapshot`, not
individual declarations — and this plan's own dedup motivation has now been
corrected twice, each round narrower than the last (Codex review, two
rounds; recorded in full so a third round doesn't re-litigate the same
ground).**

*Round 1's finding, still valid:* `AbiSnapshot.library: str` is a required,
always-distinct-per-library field (`model.py`), and `snapshot_to_dict()`
also serializes `source_path`, mtimes/sizes, and each DSO's own ELF/PE/
Mach-O metadata block — none of which two genuinely different libraries
share, even when re-linking the identical static utility into both. So "a
static archive re-linked into two DSOs" does **not** produce two
byte-identical serialized snapshots in practice; the only way to observe
this dedup path firing *within one bundle* is the degenerate case of
capturing (or testing with) the literal same `AbiSnapshot` object under two
map keys.

*Round 2's finding: the "fix" for round 1 — reframing the benefit as
cross-capture dedup — does not hold up either, because each archive is its
own independent, self-contained zip with no storage shared across files.*
`BundleArchiveWriter(path)` opens one zip at `path` and writes every blob
it needs *into that zip alone*; there is no blob directory, external
object store, or archive-update protocol spanning multiple `save_bundle_
facts` calls. Concretely: saving two captures to *different* paths (the
realistic CI shape — a new archive per run) writes the identical blob into
each archive independently, with zero bytes actually shared between them;
saving twice to the *same* path doesn't accumulate reuse either — the
second `BundleArchiveWriter` truncates the same target via a fresh temp
file (Phase 1's own atomicity design), so it never even sees the first
capture's blobs to reuse. A test proving two saved manifests reference
equal *hashes* proves the hash function is stable, not that any storage was
actually shared or reduced — exactly the round 2 finding's own point.

**Honest, corrected scope: as designed, Phase 1's whole-snapshot
content-addressed dedup delivers real space savings only in the narrow,
single-write, same-object-content case round 1 already named as rare — not
as a general cross-library *or* cross-capture storage-reduction claim.**
The format's other benefits (Phase 2's lazy per-library reader; a stable,
content-addressed reference for a manifest/blob pair) stand on their own
and are unaffected by this correction. **Genuine cross-capture space
reduction is real future work, out of scope for this plan** (see "Out of
scope" below) — it needs either an append-only write mode that reuses an
existing archive's own already-written blobs on a subsequent save to the
same path, or a shared blob store spanning multiple archive files (a real
CAS layer, not a per-archive one) — both materially larger designs of
their own, not a one-line extension of Phase 1's current per-archive
`BundleArchiveWriter`. The Tests section below is corrected to test
exactly what Phase 1 actually provides (intra-archive dedup for identical
content within one write), not a cross-capture claim the design can't back.

Individual-declaration-level dedup (sharing one blob per struct/function
across libraries, not per whole snapshot) is out of scope for the identical
reason as before: `AbiSnapshot` has no notion of a separately-addressable,
individually-hashable declaration today (it's one flat structure per
library, not a graph of independently-identified nodes) — building that
would be a model change reaching every consumer of `AbiSnapshot.functions`/
`.types`/etc., not a storage-layer change, and is explicitly **not**
attempted here (see "Out of scope").

### Phase 2 — lazy reader (S)

**As shipped (PR #869), lazy per-library access is composed by the caller
from `storage/bundle_archive.py`'s own primitives — there is no dedicated
`BundleArchive`/`load_library()` wrapper class.** `BundleArchiveReader`
(the primitive module's own class; see "Files & surfaces" below) exposes
`open(path)`, `read_manifest() -> dict`, and `read_blob(content_hash, ...)
-> bytes`; a caller wanting one library's `AbiSnapshot` reads the manifest
dict, looks up that library's content hash in `manifest["library_blobs"]`,
and calls `read_blob()` on that hash, deserializing the returned bytes
itself (`snapshot_from_dict`). The `InstantiationManifest` blob, when
present, is read the identical way via `manifest["manifest_blob"]`. The
sketch below states this as the conceptual per-library read operation this
phase's acceptance criteria are about — not a literal typed method that
ships:

```python
# Conceptual: what "load one library, lazily" means over the real,
# shipped primitives (storage/bundle_archive.py's BundleArchiveReader) --
# not a class this format defines.
with BundleArchiveReader.open(path) as reader:
    manifest = reader.read_manifest()            # reads only manifest.json
    content_hash = manifest["library_blobs"][name]
    payload = reader.read_blob(content_hash)      # reads only that library's blob
    snapshot = snapshot_from_dict(json.loads(payload))
```

**This sketch is incomplete as written, and deliberately corrected here
rather than left implicit (Codex review, fresh evidence; sharpened by a
later round of the same review — the first draft of this correction named
only one of the two axes, see below; a still later round added the marker
check below): a lazy per-library read must check `manifest["artifact_type"]`
against `BUNDLE_ARCHIVE_ARTIFACT_TYPE` (CLI cleanup phase two, PR I
prerequisite -- rejecting a missing or mismatched marker outright, the same
way `read_bundle_facts_archive()` does), and **both**
`manifest["schema_version"]` (the container's own layout version) **and**
`manifest["bundle_facts_schema_version"]` (the encoded `BundleFacts`' own
version) against their respective supported ranges *before* trusting
anything else the manifest says, exactly where the sketch currently jumps
straight from `read_manifest()` to indexing `library_blobs`.** Checking only
`bundle_facts_schema_version` — as an earlier revision of this passage did —
leaves the other axis unguarded: `schema_version` gates the container's own
shape, including `library_blobs`'s own keying and layout (see the "Two
independent schema versions, not one" note above), so a future archive whose
container layout changed but whose `BundleFacts` shape is still supported
could pass a `bundle_facts_schema_version`-only check and still have
`manifest["library_blobs"][name]` interpreted under a layout assumption that
no longer holds. Both fields independently gate bundle-*wide* semantics —
`filesystem_aliases`, `library_filenames`, and `library_blobs`'s own keying,
not just each per-library `AbiSnapshot`'s own shape — so a too-new value on
either axis can make `manifest["library_blobs"][name]` itself unsafe to
interpret even when the one referenced snapshot blob still happens to
deserialize without error. As shipped (PR #869), this check exists only
inside `bundle_facts.read_bundle_facts_archive()` (the whole-bundle load,
which checks both `schema_version` and `bundle_facts_schema_version`) — its
own docstring explicitly directs a caller wanting one library to
`BundleArchiveReader` directly instead, and that primitive's
`read_manifest()`/`read_blob()` perform **no schema check of either kind**
(confirmed by reading both functions directly on
`claude/g40-bundle-archive-impl`'s latest commit, `b9e0dae9`: neither
references `schema_version` nor `bundle_facts_schema_version` anywhere), so
a caller following this sketch literally skips the rejection Phase 1
promises for both fields. This is a real gap in the shipped lazy-read path,
not merely a documentation omission: closing it means every lazy
per-library reader — this sketch included, and any future dedicated
wrapper — must reject a too-new `manifest["schema_version"]` *and* a
too-new `manifest["bundle_facts_schema_version"]` (the same two comparisons
`read_bundle_facts_archive()` already performs) immediately after
`read_manifest()` returns, before doing anything with
`library_blobs`/`filesystem_aliases`/`library_filenames`. Updating
`BundleArchiveReader`/`read_bundle_facts_archive()` themselves is
implementation work on the already-shipped PR #869 branch, out of scope for
this plan document — recorded here as a Phase 2 acceptance requirement so a
future lazy-read entry point (CLI or typed API) is built to check both axes
from the start rather than reproducing the same gap a second time.
`artifact_type` is a later, additional requirement on top of this
paragraph's original two-axis gap (CLI cleanup phase two, PR I prerequisite,
postdating PR #869): `read_bundle_facts_archive()` now validates it via
`validate_bundle_archive_artifact_type()` before either schema-version
check, so a lazy per-library reader built against this sketch must add that
same validation too, not just the two schema-version comparisons.

This per-library read decompresses and parses exactly the one referenced blob member
(acceptance criterion (a)); a caller wanting every library still pays the
full cost, but a caller wanting one library out of a fifty-library release
(the CLI-blocked-but-real `compare_release_against_bundle_facts()` per-
library matching loop from G38 Phase 13, once it has a CLI surface —
[the still-open half of G38's own Known gap](g38-bundle-facts-model-and-multibuild-comparability.md))
no longer pays for the other forty-nine.

Decompression-bomb limits mirror `snapshot_io.py`'s existing discipline for
the plain-JSON path (ADR-059) — applied per-member here rather than to one
whole-document read, so a single oversized blob can't exhaust memory on a
per-library `read_blob()` call for an unrelated, small library elsewhere in the same
archive.

**A per-member cap alone is not sufficient for a *whole-bundle* load, and
this is not merely a per-library `read_blob()` design note — it's already load-bearing
in the shipped implementation (Codex review, fresh evidence).** Bounding
each blob individually stops one oversized member from being a bomb on its
own, but an archive can name many blobs each just under the per-member
ceiling — a whole-bundle load (`load_bundle_facts()`/
`read_bundle_facts_archive()`, not the lazy per-library `read_blob()` read
this phase otherwise describes) would decompress and parse an unbounded
*aggregate* before returning, since nothing stops the per-member checks from
each individually passing. `read_bundle_facts_archive()` therefore also
enforces a cumulative decoded-byte budget across the whole load
(`DEFAULT_MAX_BUNDLE_DECODED_BYTES`, mirrored from `snapshot_io.py`'s own
whole-document cap) — each blob read is capped at *the remaining* aggregate
allowance, not the full per-blob ceiling, so a long run of just-under-the-
limit blobs is caught by the shrinking budget rather than by any single
blob's own size — plus a separate cap on the *number* of `library_blobs`
entries a manifest may name (`DEFAULT_MAX_LIBRARY_COUNT`), since many
library names can cheaply share one small, size-capped blob and each still
materializes its own full `AbiSnapshot` object graph on load — a
Python-object-count amplification the byte-level caps alone don't bound.
The per-library `read_blob()` read correctly keeps only the per-member
cap — it has no aggregate to bound, by construction, since it never touches
more than one blob.

**A per-member/aggregate decoded-payload cap alone is not sufficient
either — it runs too late to protect `BundleArchiveReader.open()` itself against
an untrusted archive, and this plan's earlier text never named the guard
the shipped implementation had to add to close it (Codex review, fresh
evidence, verified against the real implementation in PR #869).**
`zipfile.ZipFile` eagerly parses the *entire* central directory the
moment it is constructed, before any per-member or aggregate-decoded-byte
check above ever runs — so a crafted archive naming millions of tiny,
unreferenced entries (or one with an enormous, uncompressed
`manifest.json`, itself never mentioned above either) can exhaust memory
purely from that construction, regardless of how tightly the per-library `read_blob()` read/
`read_bundle_facts_archive` bound the blobs they actually read. Two
outer guards close this, both ahead of `zipfile.ZipFile` ever running:
a central-directory preflight (`reject_absurd_central_directory`) that
parses the EOCD/ZIP64 record directly — without invoking `ZipFile`'s own
parse — and rejects an archive whose declared *or* actually-walked entry
count exceeds a fixed cap (`abicheck/storage/bundle_archive.py`'s own
`MAX_ARCHIVE_MEMBERS`) or whose central-directory byte size exceeds a
fixed cap (`abicheck/storage/bundle_archive_cd_guard.py`'s own
`_MAX_CENTRAL_DIRECTORY_BYTES`) — both constants own their current
values, not copied here, for the same reason this document already
refers to `BUNDLE_FACTS_SCHEMA_VERSION` rather than hand-copying its
number (Codex review; `AGENTS.md`'s own "don't hand-copy a table,
count, or version number that already has a fact owner elsewhere"
rule) — and a bounded read of the
uncompressed `manifest.json` member itself
(`DEFAULT_MAX_MANIFEST_BYTES`), checked incrementally rather than after
fully materializing it. Both are enforced symmetrically on the write
side too — `BundleArchiveWriter` refuses to publish an archive its own
paired reader could not reopen.

**A decoded-byte cap alone does not bound the *decompressor's own*
allocation for a per-blob zstd frame, and this plan's earlier text never
named the guard that closes it (Codex review, fresh evidence, verified
against the real implementation in PR #869).** `snapshot_io._decompress_zstd()`
already treats an unbounded declared zstd window as an independent bomb
vector, separate from the decoded-output-size caps above: a crafted frame
can declare a very large window *before* the decoder produces any bounded
output at all, so a per-member/aggregate decoded-byte budget that only
checks the *result* size does not stop the decompressor from allocating
that window on the way there. Every zstd-compressed blob member this
format reads must therefore construct its `zstandard.ZstdDecompressor`
with the identical runtime-clamped `max_window_size` bound
`snapshot_io._decompress_zstd()` already applies to the whole-document
path, rather than relying on the decoded-byte caps above alone — the
shipped implementation (`abicheck/storage/bundle_archive.py`, PR #869)
already does this correctly (`ZstdDecompressor(max_window_size=1 <<
_ZSTD_MAX_WINDOW_LOG)`); this plan's own resource-limit inventory above
was simply missing the requirement, not describing a gap in the code.

**These guards bound only central-directory metadata and the decoded
*output* of a blob's decompression — each `blobs/<hash>.json.zst`
member is itself a `ZIP_STORED` zip entry, and reading *that* stored
member's own bytes needs its own, independent ceiling before
decompression ever starts (Codex review, fresh evidence, verified
against the real implementation in PR #869).** A malicious archive can
carry a tiny manifest and central directory yet name one multi-gigabyte
`ZIP_STORED` blob member; nothing above stops that member's raw,
still-compressed bytes from being read into memory in one shot before
the bounded zstd decoder in the previous paragraph ever runs. The
shipped implementation already closes this the same way
`snapshot_io.py` bounds its own stored/compressed reads: every member
read — both `manifest.json` and each blob — goes through one shared
helper, `BundleArchiveReader._read_stored_member()`, which streams the
member in bounded chunks and aborts as soon as the running total
exceeds the caller's own byte ceiling, rather than reading the whole
member into a buffer first. For a blob specifically, that ceiling is
`max_decoded_bytes` plus a small fixed zstd-frame-overhead slack, not
the raw member size, so an incompressible payload's slightly larger
compressed form is still accepted without loosening the bound.

**The streaming cap above bounds only what `_read_stored_member()` itself
reads chunk-by-chunk — it does nothing for a member whose *own* declared
compression method isn't `ZIP_STORED` in the first place, since
`zipfile.ZipFile.open()` performs that *outer* decompression (LZMA/BZIP2/
DEFLATE) internally, before this format's chunked-read loop ever sees a
single byte (Codex review, fresh evidence, verified against the real
implementation in PR #869).** This format's own writer only ever emits
`ZIP_STORED` members (`_deterministic_zipinfo()` pins
`info.compress_type = zipfile.ZIP_STORED` unconditionally), so a
legitimately-produced archive never exercises this — but a hand-crafted
or corrupted one can set any `ZipInfo.compress_type` the zip format
allows for a member named `manifest.json` or `blobs/<hash>.json.zst`. A
member flagged `ZIP_LZMA`, in particular, lets `ZipExtFile.read()` build
an LZMA decoder with an attacker-chosen dictionary size *before*
`_read_stored_member()`'s running-total check observes any output at
all — the byte-ceiling check the paragraph above describes runs on
`f.read(1024 * 1024)`'s returned chunks, which is already too late for
an allocation `zipfile`'s own decompressor made internally to produce
that first chunk. `_read_stored_member()` closes this by checking
`info.compress_type != zipfile.ZIP_STORED` — read from the central
directory entry the earlier preflight already validated, so this check
itself requires no additional parsing — and raising before `self._zf.
open(name)` is ever called for that member, for both `manifest.json` and
every blob alike, one shared check ahead of the one shared streaming
read. This
plan's own resource-limit inventory above was, again, simply missing
the requirement — the code already reads the compressed member and the
decoded decompression output as two independently bounded steps.

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

**`save_bundle_facts` keeps its existing `SnapshotWriteResult` return type
for `format="archive"` too, with each field computed for real rather than
left at a JSON-envelope default (Codex review, fresh evidence):** an
earlier revision of this section defined the new `format=` argument's
behavior but never stated what the archive branch returns, which could
otherwise degrade to `None` or to values that describe a JSON envelope, not
a zip. `compression=SnapshotCompression.NONE` — **not** `ZSTD`, despite every blob
being zstd-compressed (Codex review, fresh evidence, correcting an earlier
draft of this section): `compression` describes the *outer envelope*
`detect_snapshot_compression()`/`read_snapshot_storage_info()` would
independently discover by sniffing the written file's own magic bytes, and
that envelope is a ZIP (`PK\x03\x04`), which neither sniffer recognizes as a
zstd frame — they report `NONE`. The per-blob zstd compression is real but
internal to individual zip members, not a fact about the file as a whole;
claiming `ZSTD` here would disagree with an independent sniff of the same
file, or mislead a caller that tries to feed the raw file bytes to a zstd
decoder directly; `decoded_size_bytes` is the sum of every serialized payload actually
written to the blob store — one library's snapshot plus the instantiation
manifest, if present, each counted once even when `put_blob`'s own content
dedup collapses two libraries onto one blob (this field answers "how much
was there to encode," not "how many bytes did the store end up holding");
`stored_size_bytes`/`stored_sha256` are the real archive **file's** own
size and sha256 digest, streamed rather than read fully into memory, since
this bookkeeping step has no reason to hold a second full copy of a
potentially large multi-library archive just to size/hash it.

**`decoded_size_bytes` deliberately excludes the archive's own container
`manifest.json` (verified against the shipped `write_bundle_facts_archive()`,
`abicheck/bundle_facts.py`, PR #869 — Codex review, fresh evidence).**
The function accumulates `decoded_size_bytes` only from the per-library
`AbiSnapshot` payloads and, when present, the `InstantiationManifest`
payload — the two things that land in the blob store. The container
manifest (the manifest dict's own JSON: `schema_version`,
`library_blobs`, `filesystem_aliases`, `library_filenames`) is serialized
and size-checked separately, into a local `manifest_member_bytes`, and
that value is never folded into the returned `decoded_size_bytes` — so
even the simplest single-library archive returns a `decoded_size_bytes`
smaller than the true total logical bytes encoded into the file, and the
gap grows with however many filesystem aliases and library filenames the
container manifest carries. This differs from the plain-JSON path's own
`decoded_size_bytes` (`snapshot_io.py`), which is `len(data)` for the
*entire* document written — there is no analogous "container vs. payload"
split for a single JSON file, so that field's existing meaning there is
"all the logical bytes this write encoded," full stop. Carrying the same
field name into the archive format with a narrower definition is a real,
if minor, semantic divergence worth stating plainly rather than assuming
readers will infer it: `SnapshotWriteResult.ratio` (`stored_size_bytes /
decoded_size_bytes`) is therefore not a like-for-like compression ratio
for the archive format the way it is for the plain-JSON format —
`stored_size_bytes` is the real, whole zip file's size (container
manifest included), while `decoded_size_bytes` excludes that same
manifest's logical bytes. This is accepted as the archive format's
scope for this plan: the field answers "how much per-library fact
content was there to encode," which is the number a caller comparing
archive efficiency across bundles actually wants, and re-defining it to
include the container manifest would mean re-deriving it from
`manifest_member_bytes` — itself an implementation-internal encoding
detail (`_json.JSONEncoder(indent=2).iterencode(...)`) not otherwise
exposed on this return type. A future revision that wants a
byte-for-byte-accurate `ratio()` for the archive format should introduce
a distinct field (e.g. `container_manifest_bytes`) rather than folding a
differently-scoped number into `decoded_size_bytes` under the name this
document's own plain-JSON contract already gives a stricter meaning.
**`save_bundle_facts(facts, path, format="archive", compression="gzip")` must
be rejected, not silently ignored (Codex review, fresh evidence, correcting
an earlier draft of this section that specified the opposite).** An earlier
revision of this paragraph said `compression` is "silently inapplicable ...
(ignored, not an error)" for `format="archive"` — that would let a caller's
explicit, stated storage requirement (`compression="gzip"`/`"zstd"`) produce
an uncompressed-envelope archive with no error, which contradicts this
module's own existing `resolve_write_compression()` contract elsewhere: an
explicit compression selection is either honored or rejected loudly when
it can't be, never silently discarded. The correct rule distinguishes the
implicit default from a genuine explicit request: `compression="auto"` (the
parameter's own default — i.e. the caller never stated a preference) is
accepted for `format="archive"` and simply has no effect, since the archive
format has no whole-document compression envelope of its own to select; any
other explicit value that actually asks for a *different* outer-envelope
compression (`"gzip"`, `"zstd"`) is rejected with a clear `ValueError`
before any archive is written, the same way an incompatible explicit
selection is rejected elsewhere in this module.

**Correction (Codex review, fresh evidence): `compression="none"` is not
"any other explicit value" and must not be rejected alongside
`"gzip"`/`"zstd"` — the previous revision of this paragraph got this one
case wrong by lumping it in with the two genuinely incompatible
selections.** `resolve_write_compression()`'s own existing contract (the
plain-JSON path this format's rule is explicitly modeled on) treats
`"none"` as a real, legal explicit selection — "no outer compression" — not
merely `"auto"`'s unstated default spelled out. For `format="archive"`,
`"none"` describes *exactly* what the format already does: the archive's
own container (the zip envelope) is never additionally compressed —
`SnapshotWriteResult.compression` is already documented above as
`NONE` for this reason — so a caller stating `compression="none"` is
asking for precisely the envelope behavior the format unconditionally
provides, the same way `"auto"` is. `"gzip"`/`"zstd"` are different: they
ask for an outer compression layer the archive format structurally cannot
apply (the container is a zip, and wrapping a zip in a second compression
layer is not what either of those flags mean anywhere else in this
module). The correct rule is therefore **accept `compression` in
`{"auto", "none"}` for `format="archive"`** (both are no-ops, for the
identical reason), and reject only `"gzip"`/`"zstd"` with a `ValueError`.
Rejecting `"none"` alongside the two truly incompatible values actively
works against a caller who has *correctly* reasoned about the format's own
behavior and stated it explicitly — exactly the kind of generic,
format-agnostic caller (e.g. one iterating `save_bundle_facts` across
several `format=`s with one fixed `compression="none"` for reproducible,
uncompressed-envelope output) this module's compression contract exists to
support uniformly.

**Closed in the shipped implementation (`abicheck/serialization.py`,
commit `ec85ed3e8` on the `claude/g40-bundle-archive-impl` branch) — this
section previously described the guard as still rejecting
`compression="none"`; that is now stale.** The guard reads
`if format == "archive" and SnapshotCompression(compression) not in
(SnapshotCompression.AUTO, SnapshotCompression.NONE): raise
ValueError(...)`, so `compression="none"` is accepted as a no-op alongside
`"auto"`, and only `"gzip"`/`"zstd"` are rejected — exactly the contract
this section calls for. Verified directly against the real function at
that commit; kept here as a record of the correct contract rather than a
still-open gap.

`BundleFacts` itself is unchanged — this plan is a storage-layer addition
underneath the existing dataclass, not a new in-memory shape;
`load_bundle_facts()` still returns a plain `BundleFacts` when a caller
wants the whole-bundle load path unchanged, with the
`BundleArchiveReader`-based lazy per-library read above (not a dedicated
`BundleArchive` class — see Phase 2's own correction) as the new API for a
caller that specifically wants per-library loading.

**Docs-ownership registration (`docs/AGENTS.md`'s topic-ownership
contract):** the `format="archive"` option on `save_bundle_facts`/
`load_bundle_facts` and the new lazy per-library read via
`storage/bundle_archive.py`'s `BundleArchiveReader` are new public-facing
surface, so this phase must register them in `docs/_meta/topics.yaml` in
the same PR. `bundle-analysis`
(`canonical_page: use/multi-binary.md`) is the existing topic that already
documents `load_bundle_facts()` (see that page's own "Comparing two release
bundles from saved facts" example) — extend its `fact_sources` with the new
`abicheck/storage/bundle_archive.py` module (see Phase 1's ADR-061 routing
note above for why that's the implementation location) rather than
registering a separate topic, and add the archive format/lazy-per-library-
read usage to `use/multi-binary.md` itself as part of this phase's PR.

### Phase 4 — migration (S)

No breaking change to any existing file: `BundleFacts.schema_version` — the
value `abicheck/bundle_facts.py`'s own `BUNDLE_FACTS_SCHEMA_VERSION` constant
owns, not a number copied here — is untouched by this plan. Referring to the
owned constant rather than hand-copying its current value keeps this
paragraph correct even after a future PR bumps it independently of this plan
landing (`AGENTS.md`'s own "don't hand-copy a table, count, or version
number that already has a fact owner elsewhere" rule; this document already
follows the identical discipline for the report and scan schema versions it
cites elsewhere). A plain-JSON `BundleFacts` file
is not "archive format at that version", it's simply not an archive at all, and
`load_bundle_facts()`'s `"auto"` sniff routes it through the unchanged
plain-JSON path forever. **As shipped (PR #869), no dedicated converter
function exists** — a caller who wants to opportunistically re-save an
existing plain-JSON `BundleFacts` file in the new format does so with the
existing primitives: `load_bundle_facts(path)` (its `"auto"` sniff reads
the plain-JSON file) followed by `write_bundle_facts_archive(facts,
new_path, ...)`; never required.

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

**Corrected against the shipped implementation (PR #869) — an earlier
revision of this list named a `BundleArchiveManifest`/`BundleArchive`/
`convert_to_archive` shape that was never built; the real symbols below are
what actually shipped.**

- `abicheck/storage/bundle_archive.py` — the content-addressed zip-container
  primitive this whole plan is about (see Phase 1's routing note above for
  why this is `storage/`, not a new flat root module): `BundleArchiveWriter`
  (`put_blob`/`write_manifest`/`close`, the `stored_sha256`/
  `stored_size_bytes` attributes), `BundleArchiveReader`
  (`open`/`from_open_file`/`read_manifest`/`read_blob`), plus the
  module-level helpers `content_hash`, `sniff_bundle_archive_format`, and
  `open_regular_file_for_format_sniff`.
- `abicheck/storage/bundle_archive_cd_guard.py` — the central-directory
  bomb guard (`reject_absurd_central_directory`,
  `looks_like_zip_from_tail`), checked once by `BundleArchiveReader`'s own
  `__init__` before `zipfile.ZipFile` ever scans a candidate archive.
- `abicheck/storage/bundle_archive_json_guard.py` — the `manifest.json`
  size-preflight helpers (`oversized_raw_string`, `bounded_encode_utf8`)
  `BundleArchiveWriter.write_manifest()` uses to reject an oversized
  manifest before it fully materializes.
- `abicheck/storage/bundle_facts_validation.py` — shared alias/filename-map
  validation (`validated_alias_map`, `validated_filename_map`) used by both
  the plain-JSON and archive `BundleFacts` read paths.
- `abicheck/bundle_facts.py` — the `BundleFacts`-level entry points that
  call the `storage/bundle_archive.py` primitives above:
  `write_bundle_facts_archive`/`read_bundle_facts_archive` (the direct,
  archive-only pair), and `maybe_write_bundle_facts_archive`/
  `maybe_read_bundle_facts_archive` (the `format`-dispatching wrappers a
  caller not committed to one format uses).
- `abicheck/snapshot_io.py` — no changes; `storage/bundle_archive.py`
  follows its precedent rather than extending it (the archive's own
  zip-member framing is a different mechanism from the single-stream
  plain/gzip/zstd detection `snapshot_io.py` owns, so this stays a sibling
  module, not a modification to a leaf module several other formats already
  depend on).
- `tests/test_bundle_archive.py`, `tests/test_bundle_archive_cd_guard.py`,
  `tests/test_bundle_archive_writer_hardening.py` — the `storage/`-layer
  primitive tests. `tests/test_bundle_facts_archive.py`/
  `tests/test_bundle_facts_archive_hardening.py` — the `BundleFacts`-level
  archive read/write tests.

## Tests

- Round-trip: a multi-library `BundleFacts` saved as an archive and reloaded
  (both via `load_bundle_facts()`'s `"auto"` sniff and via `BundleArchiveReader.open`
  directly, per-library, per Phase 2's own correction) reproduces the identical per-library `AbiSnapshot`s.
- **Round-trip with a non-null `InstantiationManifest`** — the manifest-blob
  gap an earlier draft of this plan left unspecified (see Phase 1's own
  correction note): a `BundleFacts` whose `manifest` is populated, saved as
  an archive, reloaded via both `load_bundle_facts()` (which must populate
  `BundleFacts.manifest` from the archive, not silently drop it) and
  a `BundleArchiveReader.read_blob()` call against `manifest["manifest_blob"]`
  directly, reproduces the
  identical manifest; a `BundleFacts` with `manifest=None` round-trips to
  `manifest_blob=None` with no `blobs/` member allocated for it.
- **Partial-load proves lazy access, separately from a real zstd round-trip
  at production scale (Codex review — fresh evidence: an earlier revision
  of this bullet conflated the two, and a member-read counter alone can
  pass against tiny stub payloads spread across many members without ever
  exercising a realistically-sized zstd frame)**. Two distinct assertions,
  both required, neither substituting for the other:
  1. *Lazy access is real, not merely API-shaped* — a multi-library archive
     where the per-library `read_blob()` read for `"one_of_many"` is asserted, via a patched/
     instrumented `zipfile.ZipExtFile` or a member-read counter, to open and
     decompress **exactly one** member. This one can use small payloads;
     it's checking *which* member is touched, not the codec.
  2. *The archive's own zstd codec survives a realistic payload* — per
     `AGENTS.md`'s own "Third-party-boundary tests must exercise the real
     public API at realistic scale" convention (the zstd-`max_window_size`
     incident that convention exists to prevent, and the exact pattern
     `test_snapshot_compression.py::test_zstd_round_trip_at_production_scale_and_level`
     already establishes for `snapshot_io.py`'s own zstd path): a real,
     production-sized `AbiSnapshot` (scaled past the threshold where a
     window-size/decoded-size regression would actually reproduce, not a
     two-field stub) written through the real `BundleArchiveWriter` and
     read back through the real `BundleArchiveReader`'s per-library `read_blob()` read —
     the actual public chokepoints, not a hand-constructed shortcut into
     `zstandard`'s own lower-level API.
- **Determinism across construction order** — the round-trip test promised
  by Phase 2's own "order the blob members themselves are written in" note
  above: two logically-equal `BundleFacts` values whose `per_library_snapshots`
  are populated in different insertion orders (e.g. built by iterating the
  same library set forwards vs. reversed, or via two dicts constructed with
  their keys inserted in a different sequence) are each saved as an archive
  and asserted to produce byte-identical archive bytes and matching
  `stored_sha256` — proving archive identity depends only on the *set* of
  unique payloads, never on the order the caller happened to build the map
  in. Distinct from the plain round-trip test above, which only checks that
  one save/reload cycle preserves content, not that two differently-ordered
  but logically-equal inputs converge on the same bytes.
- Dedup: an **intra-archive** test only (matching the corrected, honest
  scope above, round 2) — two library names in *one* `BundleFacts` map to
  byte-identical `AbiSnapshot` content (the same object under two keys,
  documented in the test itself as a synthetic construction, not evidence
  of real production duplication) produce exactly one
  `blobs/<hash>.json.zst` member for the shared content in the resulting
  archive, not two. **No cross-capture test** — two independent
  `save_bundle_facts` calls (even for a genuinely unchanged library) are
  not expected to share any bytes, since each writes its own
  self-contained archive; asserting otherwise would test a claim this
  design doesn't make.
- **Corruption: a tampered blob is rejected, not silently mis-loaded**
  (Codex review) — a valid archive with one blob member's content replaced
  in place (still valid zstd/JSON, but no longer matching its own member
  name's hash) must raise on load, not return the substituted content
  under the original, now-incorrect content address.
- **Deferred — not yet implemented, no test exists today (Codex review,
  2026-08-26, verified against the real `BundleArchiveReader.read_manifest()`/
  `read_blob()` on `claude/g40-bundle-archive-impl`@`abfda5b`): lazy read
  rejects a too-new `bundle_facts_schema_version` before touching
  `library_blobs`.** This bullet describes the test that would pin the fix
  for the still-open known limitation the Status note and the "Effort &
  risk" section both name (`BundleArchiveReader.read_manifest()`/
  `read_blob()` perform no `bundle_facts_schema_version` check at all,
  because that key is a `bundle_facts.py`-level concept the `storage/`-layer
  reader deliberately doesn't know about — confirmed by reading both
  functions directly: neither references `bundle_facts_schema_version`, or
  `schema_version` at all, anywhere). It is *not* satisfied by the shipped
  code and must not be read as describing a passing test: an archive whose
  `manifest["bundle_facts_schema_version"]` is one greater than
  `BUNDLE_FACTS_SCHEMA_VERSION` is rejected today only via the whole-bundle
  `read_bundle_facts_archive()` path (`bundle_facts.py`, which does check
  both `schema_version` and `bundle_facts_schema_version` before any blob
  read); the direct `BundleArchiveReader`-based lazy per-library read
  (`read_manifest()` then `read_blob()` against a `library_blobs` entry,
  bypassing `bundle_facts.py` entirely) currently returns the blob
  regardless. Closing this — either by teaching the lazy path the same
  check (which needs `BundleArchiveReader` to accept the caller's
  `BUNDLE_FACTS_SCHEMA_VERSION` ceiling, since `storage/` cannot import it
  as a constant per ADR-061's dependency direction) or by documenting the
  lazy path as deliberately unchecked and pushing the obligation onto every
  caller — is a real, separate follow-up; when it lands, this bullet
  should be promoted out of "deferred" and a real test added alongside the
  fix, not written speculatively ahead of it.
- **Deferred — a distinct, second case for the *other* axis, not covered by
  the bullet above (Codex review, 2026-08-26, fresh evidence on the same
  commit): lazy read also rejects a too-new *container* `schema_version`
  before touching `library_blobs`.** The bullet above mutates only
  `manifest["bundle_facts_schema_version"]` above its ceiling — it says
  nothing about `manifest["schema_version"]` (the container's own layout
  version) above *its* ceiling, and the two are independently checked by
  `read_bundle_facts_archive()` (see the Phase 2 correction above), so an
  implementation could satisfy the `bundle_facts_schema_version` case above
  while still leaving `schema_version` completely unguarded on the lazy
  path — passing this test list without actually closing the gap Phase 2
  now requires both checks for. This bullet is the container-axis sibling:
  an archive whose `manifest["schema_version"]` is one greater than the
  container's own supported ceiling must be rejected by the lazy
  per-library read path *before* `library_blobs` is ever indexed — the
  same "reject before doing anything with `library_blobs`" ordering the
  Phase 2 correction states for both axes, asserted here specifically so a
  fix that only special-cases `bundle_facts_schema_version` (the axis a
  reader might reach for first, since it is the one already familiar from
  the whole-bundle path) cannot pass this test list while leaving
  `schema_version` open. Like its sibling above, this is *not* satisfied
  by the shipped code and must not be read as describing a passing test;
  when the lazy-path fix lands, both this bullet and its sibling should be
  promoted out of "deferred" together, with real tests for both axes added
  alongside the fix.
- **Atomic publication: a failed write leaves a prior valid archive
  untouched** (Codex review — the requirement introduced above under
  "Publication must be atomic", not previously exercised by any test in
  this list) — write a real archive to a path, capture its bytes, then
  attempt a second write to the same path that is made to fail partway
  through (an injected exception between a successful `put_blob` and
  `write_manifest`/`close()`); assert both that the destination's bytes
  are byte-for-byte identical to the first write's, and that no leftover
  temp file survives in the destination's parent directory. This test
  proves cleanup only for the code-reachable failure class the "must be
  scoped honestly" note above describes (a raised, in-process exception) —
  it cannot exercise, and makes no claim about, a `SIGKILL`/power-loss/
  OOM-kill termination, where no cleanup code runs at all; don't read a
  pass here as evidence against that residual gap.
- **Durability sequence: the fsync/replace ordering itself is asserted, not
  only its net effect** (Codex review, PR #866 round 20 — the atomic-
  publication test above proves the *result* survives an in-process
  failure, but nothing in this list checks the *ordering* the "Closing the
  temp file and calling `os.replace()` is not durable on its own" note
  above promises; a writer that dropped the post-metadata-fixup fsync or
  the parent-directory fsync could still pass every other test here while
  reopening the exact durability gap that note exists to close). Patch
  `os.fsync` and `os.replace` (e.g. via `unittest.mock.patch`, recording
  each call's target fd/path in a shared list) around one real
  `BundleArchiveWriter` write with at least one blob plus an ownership/mode
  fixup, and assert the recorded call sequence is: fsync(temp file fd) —
  after the payload write, before any fixup; fsync(temp file fd) again —
  after the `fchown`/`fchmod` fixup; `os.replace()`; fsync(parent-directory
  fd) — last. A sequence missing a step, or reordering `os.replace()` ahead
  of either temp-file fsync, must fail this test even though the archive's
  final on-disk bytes are unaffected either way.
- **Hard-link rejection: a pre-existing destination with `st_nlink > 1` is
  refused, not silently desynchronized** (Codex review, PR #866 round 20 —
  the "must be rejected outright" requirement above names its own
  acceptance criterion and dedicated test, but this list never added the
  test) — create a destination file, hard-link a second name to the same
  inode (`os.link()`), then attempt to write an archive to the original
  path; assert the write raises before any temp file is published (before
  `os.replace()` runs) and that both the destination and its sibling hard
  link are byte-for-byte unchanged afterward — proving the writer refuses
  rather than retargeting only the one directory entry it was pointed at
  and leaving the sibling link silently stale.
- **Every declared resource-limit guard needs its own adversarial test, not
  just the intra-archive dedup/round-trip happy path above (Codex review,
  fresh evidence — the decompression-bomb and central-directory guards
  introduced earlier in this plan are exactly the class of security
  boundary a plan can describe in prose while its own test list quietly
  never exercises, letting a later regression silently reopen the memory-
  exhaustion risk those guards exist to close).** Each of the following is
  its own required test, not a single combined smoke test — a shared test
  could pass while any one individual guard silently regresses:
  1. *Oversized manifest* — a `manifest.json` member whose declared/actual
     size exceeds the manifest read cap is rejected before its content is
     parsed as JSON, with a clear error rather than an out-of-memory
     failure or a hang.
  2. *Cumulative decoded-byte exhaustion across a whole-bundle load* — an
     archive with many blob members, each individually under the
     per-member decompression cap but whose combined decoded size exceeds
     the aggregate budget a whole-bundle load enforces, is rejected once
     the aggregate budget is exceeded (not merely once any single member
     exceeds it) — the scenario the per-member-only cap is explicitly
     insufficient for, per this plan's own "aggregate before returning"
     note above.
  3. *Library-count exhaustion* — a manifest naming an implausibly large
     number of `library_blobs` entries (whether or not any of them
     resolve to a real member) is rejected before the reader attempts to
     iterate or open that many members.
  4. *Forged/oversized ZIP central directory* — a hand-crafted archive
     whose End-Of-Central-Directory record claims an absurd entry count,
     and a second case whose EOCD understates the entry count while the
     actual central-directory bytes (`cd_size`) contain far more real
     records than declared — both must be rejected by the preflight
     central-directory guard *before* `zipfile.ZipFile` is ever
     constructed on the untrusted bytes, per this plan's own "must run
     before `ZipFile`" requirement above; a real, ordinary-sized archive
     must still open cleanly (a positive control alongside the two
     adversarial ones, so the guard's threshold isn't accidentally
     tightened to reject legitimate archives).
  5. *Forged/oversized ZIP64 central directory* — the ZIP64 extension's own
     End-Of-Central-Directory-Locator/Record pair (used once an archive's
     entry count or offsets exceed the legacy 32-bit EOCD's range) is
     checked by the identical preflight, not only the legacy EOCD shape —
     an archive whose ZIP64 record claims an absurd entry count must be
     rejected the same way, since a reader that validates only the
     legacy EOCD leaves the ZIP64 path as an unguarded bypass.
  6. *Writer-side symmetry* — `BundleArchiveWriter` itself must not be
     capable of producing an archive that fails these same limits at
     write time (e.g. it must refuse, or itself bound, a write that would
     manifest an absurd member count) rather than relying solely on the
     reader-side guards to catch what the writer already knows would be
     illegitimate output — a real gap if the writer can silently emit
     something only the reader's adversarial-input guards happen to catch.
  7. *Oversized declared zstd window* — a blob member whose zstd frame
     header declares a window size larger than the reader's clamped
     `max_window_size` bound is rejected by the decompressor itself
     (`zstandard.ZstdDecompressionError`, or this format's own wrapping of
     it) before any decoded bytes are produced, distinct from the
     decoded-*output*-size cases above — this is the one guard in this
     list that bounds the decompressor's own allocation rather than the
     size of what it eventually returns, per the requirement introduced
     above; a real, ordinarily-windowed blob must still decompress
     cleanly (a positive control, matching this list's existing pattern).
  8. *Oversized stored (still-compressed) blob member* — a `ZIP_STORED`
     blob member whose own declared/actual size (before decompression)
     exceeds the reader's bound is rejected while streaming that member's
     raw bytes, never fully buffered into memory first — the guard this
     plan's own "these guards bound only central-directory metadata and
     the decoded *output*" note above describes, distinct from both the
     central-directory preflight (guards 4-5) and the decoded-window
     bound (guard 7): a tiny manifest and central directory naming one
     multi-gigabyte stored member must still be rejected before any
     zstd decoding is attempted.
  9. *Non-`ZIP_STORED` member (`ZIP_DEFLATED`, and by the same blanket
     `!= ZIP_STORED` check, `ZIP_LZMA`/`ZIP_BZIP2` too)* — a
     `manifest.json` or blob member whose `ZipInfo.compress_type` is
     anything other than `ZIP_STORED` must be rejected by
     `_read_stored_member()`'s own compress-type check, adversarially,
     through the public reader (`BundleArchiveReader.read_manifest()`/
     `read_blob()`, not the private helper directly) — before
     `zipfile.ZipFile.open()` is ever asked to open that member, since
     that call is what would otherwise perform the outer decompression
     internally, ahead of and unbounded by this format's own
     chunked-read cap (guard 8's "streaming, never fully buffered"
     property only applies once the member is already `ZIP_STORED`).
     The shipped implementation already carries this coverage —
     `tests/test_bundle_archive.py::TestBundleArchiveReaderRejectsNonStoredMembers`
     (`test_read_manifest_rejects_a_deflated_manifest_member`,
     `test_read_blob_rejects_a_deflated_blob_member`) — constructing the
     adversarial fixture with a real `zipfile.ZipFile(..., compression=
     zipfile.ZIP_DEFLATED)` write rather than hand-editing headers, since
     this format's own `BundleArchiveWriter` never produces a non-`ZIP_
     STORED` member to test against otherwise; the check itself is a
     blanket inequality against `zipfile.ZIP_STORED`, not a per-method
     allowlist, so the `ZIP_DEFLATED` coverage already exercises the
     identical code path an `LZMA`/`BZIP2` member would hit.
- Back-compat: every existing plain-JSON `BundleFacts` fixture in this
  repo's test suite still loads unchanged via the `"auto"`-sniffing path
  once this plan ships, with **no** re-save required.
- Migration round-trip: `load_bundle_facts()` over an existing plain-JSON
  fixture followed by `write_bundle_facts_archive()` to re-save it (see
  Phase 4's own correction — no dedicated converter function ships), then
  load via both paths, asserting identical `BundleFacts`.

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
the partial-load test above, not asserted from documentation alone. As the
Status line above states, this sizing was borne out rather than merely
estimated: PR #869 shipped this scope (plus the several correctness rounds
this document's own "Codex review, fresh evidence" annotations record) with
no detector-logic changes and no FP-rate/mutation-score involvement, exactly
as sized here — two known limitations grew the scope beyond this original
estimate, and both remain open: the manifest-integrity gap in Phase 2 below
(no reader-side binding on `manifest.json` itself), and the lazy per-library
reader's missing schema checks — neither the container's own
`schema_version` nor the encoded `bundle_facts_schema_version` is validated
on that path (see the Status note above and Phase 2 below for what closing
each would need).

## Out of scope

- **Cross-capture deduplication** (sharing blob bytes across two separate
  `save_bundle_facts` calls / archive files, e.g. an unchanged library
  reused across consecutive CI runs) — not delivered by Phase 1's
  per-archive `BundleArchiveWriter` as designed (see the corrected
  "Deduplication granularity" note above; each archive is independently
  self-contained, so this is a real gap, not an oversight left unstated).
  Needs either an append-only write mode that reuses an existing archive's
  own blobs on a later save to the same path, or a genuine shared CAS layer
  spanning multiple archive files — both a materially larger design than
  this plan's per-archive format, deferred the same "informed by
  production usage" way declaration-level dedup below already is.
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
