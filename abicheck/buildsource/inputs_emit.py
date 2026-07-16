# Copyright 2026 Nikolay Petrov
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Flow-2 producer side: write/append a normalized ``abicheck_inputs/`` pack.

The inverse of :mod:`inputs_pack` (which *ingests*): these helpers let a build
(the ``abicheck-cc`` wrapper, a Clang plugin, or any tooling that can produce a
:class:`SourceAbiTu`) **emit** a conformant Flow-2 pack — manifest +
``source_facts/*.jsonl`` — that ``dump --build-info``/``--sources`` later
ingests with no second frontend (ADR-035 D5, G19.4).

Two usage shapes:

- **Incremental** (a per-TU compiler wrapper): :func:`init_inputs_pack` once,
  then :func:`append_source_facts` per compiled translation unit.
- **One-shot** (a batch producer or a test fixture): :func:`write_inputs_pack`
  writes the manifest, all facts, and an optional compile DB in one call.

Pure I/O — never runs a compiler. A pack written here round-trips through
:func:`inputs_pack.ingest_inputs_pack`.
"""

from __future__ import annotations

import contextlib
import datetime as _dt
import gzip
import hashlib
import json
import os
import shutil
import tempfile
from collections.abc import Iterable
from pathlib import Path

from .inputs_pack import (
    DEFAULT_COMPILE_DB_REL,
    INPUTS_KIND,
    INPUTS_MANIFEST_NAME,
    SOURCE_FACTS_DIR,
    InputsManifest,
    _iter_source_fact_files,
    _safe_pack_path,
    load_inputs_manifest,
    read_source_fact_files,
)
from .source_abi import SourceAbiTu

#: Default JSONL file the incremental writer appends to when no per-TU name is
#: given. A per-TU name (see :func:`facts_filename`) keeps parallel wrapper
#: invocations from racing on one file.
DEFAULT_FACTS_FILE = "facts.jsonl"


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


def _write_manifest(root: Path, manifest: InputsManifest) -> None:
    """Atomically write ``manifest.json`` (temp file + ``os.replace``).

    The wrapper is built for **parallel per-TU invocations** sharing one pack, so
    a plain truncate-then-write would let a concurrent ``init_inputs_pack`` reader
    observe a half-written manifest, raise on ``json.loads``, and lose that TU's
    facts (Codex review). ``os.replace`` is atomic, so a reader sees either the
    old file or the fully-written new one — never a partial.
    """
    data = json.dumps(manifest.to_dict(), indent=2, sort_keys=True) + "\n"
    fd, tmp = tempfile.mkstemp(dir=str(root), prefix=".manifest.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(data)
        os.replace(tmp, root / INPUTS_MANIFEST_NAME)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def facts_filename(source: str, *, library: str = "") -> str:
    """Deterministic, collision-resistant ``source_facts`` filename for a TU.

    ``<stem>.<short-hash>.jsonl`` — the stem keeps it human-readable, the hash of
    the full source path keeps two same-named TUs in different directories from
    colliding (and lets parallel wrapper invocations each own a file).

    *library* — the owning target's identity (``init_inputs_pack``'s
    ``library=``) — is folded into the hash alongside the source path when
    given, so the *same* source file compiled into two different libraries
    that share one ``abicheck_inputs/`` pack root gets two distinct files
    instead of the second compile silently overwriting the first's facts
    (latest-main Clang plugin review, PR3 target isolation). Omitting
    *library* keeps the pre-existing, library-blind filename for a caller
    that only ever emits one target into a given pack root.
    """
    stem = Path(source).name or "tu"
    digest_input = f"{library}\0{source}" if library else source
    digest = hashlib.sha256(digest_input.encode("utf-8")).hexdigest()[:12]
    return f"{stem}.{digest}.jsonl"


def init_inputs_pack(
    root: Path | str,
    *,
    library: str = "",
    version: str = "",
    created_by: str = "",
) -> InputsManifest:
    """Create the pack directory + manifest if absent; return the manifest.

    Idempotent for repeated calls naming the **same** target: if a manifest
    already exists and its ``library``/``version`` agree with (or leave
    unspecified) the ones passed here, it is loaded and returned unchanged, so
    repeated per-TU wrapper invocations across one build share one pack
    without clobbering it.

    Raises ``ValueError`` if an existing manifest.json does not declare
    ``kind: abicheck_inputs`` — e.g. a :class:`~.pack.BuildSourcePack`
    directory a build was mistakenly pointed at. Silently accepting it (the
    same forward-compat ``kind`` default :func:`InputsManifest.from_dict`
    applies elsewhere) would let every subsequent :func:`append_source_facts`
    call for this build write ``source_facts/*.jsonl`` into that unrelated
    directory (CodeRabbit review, P2) — this is the very first point of
    contact for a build's pack, so the check matters more here than anywhere
    downstream.

    Also raises ``ValueError`` when an existing manifest names a *different*
    non-empty ``library`` or ``version`` than this call (latest-main Clang
    plugin review, PR3): the manifest is otherwise first-writer-wins, so two
    different targets/versions built into one shared ``out=``/pack directory
    would silently inherit whichever one ran first — an operational
    correctness risk, not a legitimate shared-pack scenario (which always
    names the *same* target across its per-TU invocations). A caller that
    omits ``library``/``version`` (leaves it ``""``) is never treated as a
    conflict either way, preserving callers that do not always know it yet.

    The very first creation is claimed atomically (write-temp + ``os.link``):
    an ``is_file()``-then-write TOCTOU let two racing first-TU invocations for
    two *different* targets sharing one out= directory both observe no
    manifest yet, both skip the library/version agreement check above, and
    have the second writer silently win with neither call ever raising
    (Codex review; same fix shape as the Clang plugin's ``ensureManifest``).
    ``os.link`` is all-or-nothing — it fails with ``FileExistsError`` without
    ever creating a partial file at the destination if it already exists — so
    the loser always re-reads a fully-written manifest, never a torn one.
    """
    root = Path(root)
    mpath = root / INPUTS_MANIFEST_NAME
    # Only `root` itself -- not source_facts/ -- so a rejected wrong-kind
    # pack below is still left with no new files of ours (CodeRabbit review,
    # P2); tempfile.mkstemp(dir=root) below needs root to already exist.
    root.mkdir(parents=True, exist_ok=True)

    if not mpath.is_file():
        # Best-effort: skip the claim attempt when a manifest is already
        # visible (the common case after the pack's first TU) -- the
        # exclusivity guarantee is os.link() below, not this check.
        new_manifest = InputsManifest(
            library=library, version=version, created_by=created_by, created_at=_now()
        )
        manifest_json = (
            json.dumps(new_manifest.to_dict(), indent=2, sort_keys=True) + "\n"
        )
        fd, tmp = tempfile.mkstemp(dir=str(root), prefix=".manifest.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(manifest_json)
            os.link(tmp, mpath)
        except FileExistsError:
            pass  # lost the race -- fall through and validate the winner's manifest
        else:
            (root / SOURCE_FACTS_DIR).mkdir(parents=True, exist_ok=True)
            return new_manifest
        finally:
            with contextlib.suppress(OSError):
                os.unlink(tmp)

    try:
        data = json.loads(mpath.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        data = None
    if isinstance(data, dict):
        if data.get("kind") != INPUTS_KIND:
            # A syntactically valid manifest naming a different (or no)
            # kind names a real, different pack (e.g. a BuildSourcePack
            # directory a build was mistakenly pointed at) -- not a
            # "malformed" one, so it is not swallowed the way the
            # fallback below swallows a truncated/corrupted manifest.
            raise ValueError(
                f"{mpath} does not declare kind: {INPUTS_KIND} — not a "
                "Flow-2 abicheck_inputs pack."
            )
        existing_library = str(data.get("library") or "")
        if library and existing_library and library != existing_library:
            raise ValueError(
                f"{mpath} already names library {existing_library!r}; this "
                f"call named {library!r}. Two different targets must not "
                "share one abicheck_inputs pack directory -- use a separate "
                "out= directory per target/configuration/architecture."
            )
        existing_version = str(data.get("version") or "")
        if version and existing_version and version != existing_version:
            raise ValueError(
                f"{mpath} already names version {existing_version!r}; this "
                f"call named {version!r}. Two different versions must not "
                "share one abicheck_inputs pack directory -- use a fresh "
                "out= directory per build."
            )
        (root / SOURCE_FACTS_DIR).mkdir(parents=True, exist_ok=True)
        return InputsManifest.from_dict(data)
    # Defensive: a manifest left partial/malformed by a non-atomic writer
    # on an old pack (our writes are atomic) re-initializes rather than
    # raising and losing this TU's facts.
    (root / SOURCE_FACTS_DIR).mkdir(parents=True, exist_ok=True)
    manifest = InputsManifest(
        library=library, version=version, created_by=created_by, created_at=_now()
    )
    _write_manifest(root, manifest)
    return manifest


def append_source_facts(
    root: Path | str,
    tus: Iterable[SourceAbiTu],
    *,
    filename: str = DEFAULT_FACTS_FILE,
    compress: bool = False,
) -> Path:
    """Append per-TU dumps as JSON-Lines to ``source_facts/<filename>``.

    One compact, key-sorted JSON object per line (the canonical Flow-2 form).
    Returns the file written. The caller is responsible for having created the
    manifest (see :func:`init_inputs_pack`).

    *compress* (P1 #22) gzips the file (a ``.gz`` suffix is appended to
    *filename* if not already present) — pure execution policy, never changes
    the decoded facts a reader gets back (``inputs_pack.read_source_facts``
    decompresses transparently). Gzip append semantics differ from plain-text
    append (each ``gzip.open(..., "ab")`` call writes an independent member,
    which decompresses back to the concatenation of their contents — exactly
    what JSON-Lines needs), so this still supports the same incremental
    per-TU-invocation usage as the uncompressed path.
    """
    root = Path(root)
    facts_dir = root / SOURCE_FACTS_DIR
    facts_dir.mkdir(parents=True, exist_ok=True)
    # Infer compression from a caller-supplied ".gz" filename too: a mismatch
    # (compress=False with a ".gz"-named file) would silently write plaintext
    # under a name read_source_facts() later tries to gunzip (CodeRabbit
    # review, P2).
    compress = compress or filename.endswith(".gz")
    # The default directory scan _iter_source_fact_files() only recognizes
    # *.jsonl(.gz)/*.json(.gz) -- a caller-supplied basename without one of
    # those extensions (e.g. filename="tu", with or without compress=True)
    # wrote a file that scan could never find, so it silently vanished from
    # every later ingest/validate. Normalize to the canonical .jsonl
    # extension before the optional .gz suffix, same fix already applied to
    # compact_inputs_pack's output_filename (Codex review, P2).
    base = filename[: -len(".gz")] if filename.endswith(".gz") else filename
    if not (base.endswith(".jsonl") or base.endswith(".json")):
        base = f"{base}.jsonl"
    filename = f"{base}.gz" if compress else base
    path = facts_dir / filename
    lines = "".join(json.dumps(tu.to_dict(), sort_keys=True) + "\n" for tu in tus)
    if compress:
        with gzip.open(path, "ab") as fh:
            fh.write(lines.encode("utf-8"))
    else:
        with path.open("a", encoding="utf-8", newline="\n") as fh:
            fh.write(lines)
    return path


def write_inputs_pack(
    root: Path | str,
    *,
    library: str = "",
    version: str = "",
    tus: Iterable[SourceAbiTu] = (),
    created_by: str = "",
    compile_db: Path | str | None = None,
    exported_symbols: Iterable[str] = (),
    binary: str = "",
    headers: Iterable[str] = (),
    compress: bool = False,
) -> Path:
    """Write a complete Flow-2 pack in one call; return the pack root.

    Materializes ``manifest.json`` + ``source_facts/facts.jsonl`` and, when
    *compile_db* is given, copies it to ``build/compile_commands.json`` and
    records it in the manifest. Round-trips through ``ingest_inputs_pack``.
    *compress* gzips the facts file (P1 #22); see :func:`append_source_facts`.
    """
    root = Path(root)
    (root / SOURCE_FACTS_DIR).mkdir(parents=True, exist_ok=True)
    manifest = InputsManifest(
        library=library,
        version=version,
        created_by=created_by,
        created_at=_now(),
        exported_symbols=sorted(set(exported_symbols)),
        binary=binary,
        headers=list(headers),
    )
    append_source_facts(root, tus, filename=DEFAULT_FACTS_FILE, compress=compress)
    if compile_db is not None:
        dst = root / DEFAULT_COMPILE_DB_REL
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(compile_db, dst)
        manifest.compile_db = DEFAULT_COMPILE_DB_REL
    _write_manifest(root, manifest)
    return root


#: Default output filename for post-build compaction (P1 #21).
DEFAULT_COMPACTED_FACTS_FILE = "compacted.jsonl"


def _reject_escaping_filename(name: str) -> None:
    """Refuse an ``output_filename`` that could write outside ``source_facts/``.

    Documented as a bare filename, not a path — an absolute path or a ``..``
    component (e.g. ``../merged.jsonl``) would make the merged file land
    outside the directory a later ingest scans while ``remove_originals``
    still deletes every per-TU file from ``source_facts/``, leaving a pack
    that silently ingests zero TUs (Codex review, P2).
    """
    p = Path(name)
    if not name or name in (".", "..") or p.is_absolute() or p.name != name:
        raise ValueError(
            "output_filename must be a plain filename with no path "
            f"separators or '..', got {name!r}"
        )


def compact_inputs_pack(
    root: Path | str,
    *,
    output_filename: str = DEFAULT_COMPACTED_FACTS_FILE,
    compress: bool = False,
    remove_originals: bool = True,
    diagnostics: list[str] | None = None,
) -> Path | None:
    """Merge a pack's many per-TU ``source_facts/*.jsonl`` files into one (P1 #21).

    The race-free incremental writer (:func:`append_source_facts`, fed by
    :func:`facts_filename`) gives every parallel compile its own file so
    concurrent TUs never race on one fd — correct during a build, but it can
    leave thousands of tiny files in a large pack, expensive to transfer/store
    afterwards. This is a **post-build** step (run once the build has
    finished, never mid-build): it re-reads every discovered source-fact file
    (a malformed record degrades to a diagnostic, never an abort), re-
    serializes each TU with the same canonical
    ``json.dumps(tu.to_dict(), sort_keys=True)`` form the writers already
    use, and concatenates the result into one file.

    **Decoded facts an ingest sees are unchanged** (P1 #25 invariance) —
    compaction (and *compress*, gzipping the merged file) only changes file
    layout/size on disk, never the fact content ``ingest_inputs_pack`` folds.
    One exception, both deliberate: a *rerun* of compaction (the pack already
    has a prior compaction's output file, plus fresh per-TU files a later
    incremental build emitted for since-rebuilt TUs) prefers each fresh
    per-TU record over the prior output's now-stale record for the same
    ``tu_id`` — the prior output's records for TUs *not* rebuilt since are
    still carried forward, never dropped (Codex review, P2). "Prior" is
    identified via the manifest's ``last_compacted`` pointer (this function
    writes it after every successful run), not by matching *this* run's
    output filename or comparing file mtimes: a rerun with a different
    ``--output-filename``/``--compress`` setting than last time still
    recognizes last run's output as stale, and a rebuild's freshly-rewritten
    per-TU file landing in the same filesystem timestamp tick as the
    previous compaction's write can no longer flip the outcome (Codex
    review, P2 — both findings, the second reproduced empirically). And a
    read that produces new diagnostics (a malformed/unreadable original)
    does **not** write or publish anything and returns ``None`` — check
    *diagnostics* to find out why. Publishing a best-effort merged file
    anyway, while leaving the lossy/malformed originals in place too (the
    previous behavior), let a single malformed sibling silently duplicate
    every successfully-read TU on the next ingest: the default directory
    scan sees both the untouched originals and their copies now baked into
    the merged file (CodeRabbit review, P2 — reproduced empirically: a pack
    with one malformed ``source_facts`` file reported ``tu_count == 2`` for
    a single TU, and ``inputs validate`` flagged a duplicate ``tu_id``
    error). A lossy compaction therefore leaves the pack byte-for-byte
    unchanged — safe to retry once the offending file is fixed or removed.

    *remove_originals* deletes the per-TU files that were merged once the
    merged file is written (skipped entirely on a lossy read, see above), so
    a later ingest cannot double-count TUs by reading both the merged file
    and its stale sources. A manifest that names explicit ``source_facts``
    entries is repointed at the single merged file; the default (auto-scan
    of ``source_facts/``) needs no manifest change — the new file already
    lives where the scan looks.
    """
    _reject_escaping_filename(output_filename)
    root = Path(root)
    manifest = load_inputs_manifest(root)
    sink = diagnostics if diagnostics is not None else []

    # Infer compression from a caller-supplied ".gz" output_filename too, same
    # as append_source_facts (CodeRabbit review, P2). Resolved before reading
    # so the output path is known when partitioning discovered files below.
    compress = compress or output_filename.endswith(".gz")
    # The default directory scan _iter_source_fact_files() runs on every
    # later read only recognizes *.jsonl(.gz) and *.json(.gz) -- a caller-
    # supplied basename without one of those extensions (e.g.
    # --output-filename merged, with or without --compress) writes a
    # merged file that scan can never find. Compaction then "succeeds"
    # with zero diagnostics, deletes the originals it just merged, and the
    # pack silently ingests as zero TUs from that point on (Codex review,
    # P2, reproduced empirically for both the compressed and uncompressed
    # case). Normalize to the canonical .jsonl extension before the
    # optional .gz suffix, matching every other producer in this codebase,
    # unless the caller already used a recognized extension.
    base = (
        output_filename[: -len(".gz")]
        if output_filename.endswith(".gz")
        else output_filename
    )
    if not (base.endswith(".jsonl") or base.endswith(".json")):
        base = f"{base}.jsonl"
    output_filename = f"{base}.gz" if compress else base
    facts_dir = root / SOURCE_FACTS_DIR
    facts_dir.mkdir(parents=True, exist_ok=True)
    output_path = facts_dir / output_filename

    # Captured before ANY discovery/read step below, not just the per-file
    # record reads -- _iter_source_fact_files() itself can append a
    # diagnostic (an explicitly-named source_facts entry that resolves to no
    # readable files, or an escaping/unsafe path), and that must count as
    # "lossy" too: otherwise compaction proceeds as if the pack were fully
    # readable, publishes a merge missing that entry's TUs, repoints the
    # manifest to it, and deletes files based on an incomplete understanding
    # of the pack (Codex review, P2).
    before_diag_count = len(sink)
    originals = _iter_source_fact_files(root, manifest, sink)
    # Files other than the one *this* run writes to, for the deletion step
    # below only -- os.replace() already handles output_path itself, so it
    # must never also be unlinked as a "leftover original".
    fresh_files = [f for f in originals if f.resolve() != output_path.resolve()]

    # Recognize a stale prior compaction's output via an explicit manifest
    # pointer (manifest.last_compacted, written below after every successful
    # compaction) rather than matching *this* run's output path or comparing
    # file mtimes: a --compress toggle or a custom --output-filename between
    # two `compact` calls changes output_path, defeating a path match, and a
    # rebuild's freshly-rewritten per-TU file can land in the same
    # filesystem timestamp tick as the previous compaction's write, which an
    # mtime "which is newer" check cannot break correctly (Codex review, P2
    # -- both findings, the second reproduced empirically by a regression
    # test on some filesystems).
    prior_files: list[Path] = []
    recognized_prior_path: Path | None = None
    if manifest.last_compacted:
        candidate = _safe_pack_path(root, manifest.last_compacted, sink)
        if candidate is not None:
            candidate = candidate.resolve()
            recognized_prior_path = candidate
            prior_files = [f for f in originals if f.resolve() == candidate]

    # output_path already existing is legitimate ONLY when it IS the
    # recognized prior compaction's own file (a rerun reusing the same
    # --output-filename/--compress as last time) -- not merely "some file
    # happens to already sit at this path". A bare `output_path.exists()`
    # check used to treat an operator's --output-filename colliding with an
    # ordinary pre-existing file (an untouched per-TU original, a hand-placed
    # file, any stray leftover manifest.last_compacted does not point at) as
    # if it were a legitimate previous compaction -- so the os.replace()
    # below silently overwrote that unrelated file with this run's merge,
    # and a subsequent manifest-write failure then left the clobbered
    # replacement in place (rollback skips deletion for anything
    # "pre-existing") while the caller only saw an exception implying no
    # lasting effect (latest-main Clang plugin review, PR4). Reject the
    # collision outright instead of trying to back up and restore an
    # arbitrary file's prior content -- simpler and safer, per the review's
    # own recommendation.
    output_path_preexisted = (
        recognized_prior_path is not None
        and output_path.resolve() == recognized_prior_path
    )
    if output_path.exists() and not output_path_preexisted:
        raise ValueError(
            f"{output_path} already exists and is not this pack's recognized "
            "prior compaction output (manifest.last_compacted) -- refusing to "
            "overwrite an unrelated file. Choose a different --output-filename "
            "or remove the conflicting file first."
        )

    def _last_record_wins(
        files: list[Path],
    ) -> tuple[dict[str, SourceAbiTu], list[SourceAbiTu]]:
        """Read *files* in (sorted, deterministic) order, keeping the last-seen
        record per tu_id -- append order within/between files, never a
        timestamp comparison. A no-tu_id record is never deduped ("" cannot
        be treated as a real shared identity between otherwise-unrelated TUs,
        Codex review, P2).

        A non-empty tu_id seen more than once *within this bucket* (as
        opposed to a fresh record correctly superseding a stale prior-
        compaction record, handled separately by the caller) is a genuine
        pack-integrity problem -- inputs_validate.py's own duplicate_tu_ids
        check already flags this as an ERROR before compaction. Silently
        picking one via last-wins would make compaction delete the losing
        record's file (remove_originals) and erase the very duplicate the
        validator would otherwise catch, discarding one TU's facts with no
        trace (Codex review, P2). So this counts as lossy instead, same as
        any other malformed/ambiguous original.
        """
        by_id: dict[str, SourceAbiTu] = {}
        no_id: list[SourceAbiTu] = []
        for tu in read_source_fact_files(files, diagnostics=sink):
            if tu.tu_id:
                if tu.tu_id in by_id:
                    sink.append(
                        f"duplicate tu_id across source-fact files: {tu.tu_id!r} "
                        "(a race-free per-TU filename should make this "
                        "impossible)"
                    )
                by_id[tu.tu_id] = tu
            else:
                no_id.append(tu)
        return by_id, no_id

    prior_by_id, prior_no_id = _last_record_wins(prior_files)
    fresh_only = [f for f in originals if f not in prior_files]
    fresh_by_id, fresh_no_id = _last_record_wins(fresh_only)
    lossy_read = len(sink) > before_diag_count

    if lossy_read:
        # Publishing a best-effort merge here (while leaving the malformed
        # original in place too, for inspection) would duplicate every
        # successfully-read TU on the next scan: the merged file now also
        # carries copies of records the untouched good originals still
        # provide (CodeRabbit review, P2 -- reproduced empirically). Fail
        # closed instead: no write, no manifest update, pack unchanged.
        sink.append(
            "compaction read produced new diagnostics (a malformed/unreadable "
            "original); compaction was skipped entirely (no merged file "
            "written, pack left unchanged) so the raw evidence survives and "
            "a retry cannot double-count TUs"
        )
        return None

    tus = (
        [tu for tid, tu in prior_by_id.items() if tid not in fresh_by_id]
        + prior_no_id
        + list(fresh_by_id.values())
        + fresh_no_id
    )

    # Temp file + atomic rename so a concurrent reader never observes a
    # partially-written merge (same discipline as _write_manifest).
    fd, tmp = tempfile.mkstemp(dir=str(facts_dir), prefix=".compact.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as raw:
            writer = gzip.GzipFile(fileobj=raw, mode="wb") if compress else raw
            for tu in tus:
                writer.write(
                    (json.dumps(tu.to_dict(), sort_keys=True) + "\n").encode("utf-8")
                )
            if compress:
                writer.close()
        os.replace(tmp, output_path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise

    # A manifest that names explicit individual files is repointed at the
    # single merged file (those originals are gone). But any entry that is
    # itself a *directory* reference (SOURCE_FACTS_DIR itself — what the
    # Clang facts plugin's ensureManifest() always writes, and what a bare
    # default manifest effectively means too — or another sibling facts
    # directory in a hand-written mixed manifest) must NOT be dropped:
    # ensureManifest() never rewrites an existing manifest.json, so losing a
    # directory reference would permanently hide every per-TU file a later
    # incremental build writes into that directory from every subsequent
    # ingest/compact/validate (Codex review, P2 — both the single-entry case
    # and this generalization to a manifest that lists a directory alongside
    # other entries, since the original single-entry check collapsed the
    # *whole* list, directory entries included, the moment there was more
    # than one).
    #
    # The merged file always physically lands directly inside SOURCE_FACTS_DIR
    # (facts_dir above), regardless of what the manifest's entries say -- so a
    # directory entry only "covers" it when the entry *resolves to that exact
    # directory*, not merely an ancestor of it. _iter_source_fact_files()'s
    # directory-scan is non-recursive (one glob() call, no descent), so an
    # ancestor entry like source_facts=["."] can never actually discover a
    # file one level deeper in source_facts/ no matter how the two paths
    # relate on paper. A prior version of this check used
    # Path(merged_ref).is_relative_to(Path(d)), which answers "is merged_ref a
    # sub-path of d" -- true for essentially any ancestor directory, not
    # "would a non-recursive scan of d actually find merged_ref". That
    # silently deleted the very TUs compaction had just merged:
    # source_facts=["."] with a root-level fact file compacted with zero
    # diagnostics, deleted the original, and left the pack with zero readable
    # TUs afterward (review finding, reproduced empirically). Likewise a
    # directory-only manifest that does not itself cover SOURCE_FACTS_DIR
    # (e.g. source_facts=["extra_facts"], no "source_facts" entry at all)
    # still needs the merged file added explicitly, or it becomes permanently
    # undiscoverable the same way (Codex review, P2). Directory-ness is
    # checked against a scratch diagnostics list, not *sink*, so this
    # classification pass — which re-resolves entries already validated once
    # during discovery above — cannot itself flip the already-decided
    # lossy_read outcome or duplicate diagnostics in the caller-visible
    # result.
    facts_dir_resolved = facts_dir.resolve()
    _scratch: list[str] = []
    directory_entries: list[str] = []
    covered = False
    for entry in manifest.source_facts:
        target = _safe_pack_path(root, entry, _scratch)
        if target is None or not target.is_dir():
            continue
        directory_entries.append(entry)
        if target.resolve() == facts_dir_resolved:
            covered = True
    merged_ref = f"{SOURCE_FACTS_DIR}/{output_filename}"
    manifest_changed = False
    if manifest.source_facts:
        new_source_facts = (
            list(directory_entries) if covered else [*directory_entries, merged_ref]
        )
        if new_source_facts != manifest.source_facts:
            manifest.source_facts = new_source_facts
            manifest_changed = True

    # Record this run's output as the pack's new "last compaction", so a
    # later rerun recognizes it as stale (see prior_files above) regardless
    # of what output filename/compression setting that rerun uses.
    new_last_compacted = f"{SOURCE_FACTS_DIR}/{output_filename}"
    if manifest.last_compacted != new_last_compacted:
        manifest.last_compacted = new_last_compacted
        manifest_changed = True

    # Publish the manifest BEFORE destructively removing the originals it
    # supersedes: _write_manifest is atomic (temp file + rename) but can
    # still fail (disk full, permission change mid-run). Deleting the
    # originals first and publishing the manifest after would leave a
    # failed write pointing an explicit-file manifest at now-deleted files
    # -- a later read finds neither the old originals nor a manifest that
    # knows to look at the merged output, discarding evidence the merge
    # itself successfully captured. Publishing first means a manifest-write
    # failure leaves the pack's *manifest* exactly as it was pre-compaction
    # (still discoverable via its old entries/originals) (CodeRabbit
    # review, P2).
    #
    # But os.replace(tmp, output_path) above already published the merged
    # file itself, inside SOURCE_FACTS_DIR where the default directory scan
    # finds it unconditionally -- a manifest-write failure at this point
    # would otherwise leave that stray, already-discoverable merged file
    # sitting alongside the still-present (never-deleted, since
    # remove_originals hasn't run yet) originals. A later read would then
    # see BOTH copies of every TU compaction had just merged: not "less
    # evidence than before" but silently *duplicated* evidence, and the
    # stray file wedges every subsequent compact() attempt (Codex review,
    # P2, reproduced empirically: tu_count doubled after a simulated
    # manifest-write failure). Rolled back on failure, restoring the pack
    # to its exact pre-compaction state -- but only when output_path did
    # NOT already hold a prior successful compaction's result: a rerun
    # reusing the same output_filename overwrites that file in place, and
    # deleting it on this run's manifest-write failure would destroy the
    # still-valid, still-published (per the old manifest) prior result too,
    # not just this run's unpublished one.
    if manifest_changed:
        try:
            _write_manifest(root, manifest)
        except BaseException:
            if not output_path_preexisted:
                with contextlib.suppress(OSError):
                    output_path.unlink()
            raise

    if remove_originals:
        for f in fresh_files:
            with contextlib.suppress(OSError):
                f.unlink()
        # output_path itself (if it was among originals) was already
        # replaced in place above, not deleted here (fresh_files excludes it).

    return output_path
