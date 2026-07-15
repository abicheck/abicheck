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
``source_facts/*.jsonl`` — that ``abicheck merge`` later ingests with no second
frontend (ADR-035 D5, G19.4).

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


def facts_filename(source: str) -> str:
    """Deterministic, collision-resistant ``source_facts`` filename for a TU.

    ``<stem>.<short-hash>.jsonl`` — the stem keeps it human-readable, the hash of
    the full source path keeps two same-named TUs in different directories from
    colliding (and lets parallel wrapper invocations each own a file).
    """
    import hashlib

    stem = Path(source).name or "tu"
    digest = hashlib.sha256(source.encode("utf-8")).hexdigest()[:12]
    return f"{stem}.{digest}.jsonl"


def init_inputs_pack(
    root: Path | str,
    *,
    library: str = "",
    version: str = "",
    created_by: str = "",
) -> InputsManifest:
    """Create the pack directory + manifest if absent; return the manifest.

    Idempotent: if a manifest already exists it is loaded and returned unchanged,
    so repeated per-TU wrapper invocations share one pack without clobbering it.

    Raises ``ValueError`` if an existing manifest.json does not declare
    ``kind: abicheck_inputs`` — e.g. a :class:`~.pack.BuildSourcePack`
    directory a build was mistakenly pointed at. Silently accepting it (the
    same forward-compat ``kind`` default :func:`InputsManifest.from_dict`
    applies elsewhere) would let every subsequent :func:`append_source_facts`
    call for this build write ``source_facts/*.jsonl`` into that unrelated
    directory (CodeRabbit review, P2) — this is the very first point of
    contact for a build's pack, so the check matters more here than anywhere
    downstream.
    """
    root = Path(root)
    (root / SOURCE_FACTS_DIR).mkdir(parents=True, exist_ok=True)
    mpath = root / INPUTS_MANIFEST_NAME
    if mpath.is_file():
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
            return InputsManifest.from_dict(data)
        # Defensive: a manifest left partial/malformed by a non-atomic writer
        # on an old pack (our writes are atomic) re-initializes rather than
        # raising and losing this TU's facts.
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
    if compress and not filename.endswith(".gz"):
        filename = f"{filename}.gz"
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
    if compress and not output_filename.endswith(".gz"):
        output_filename = f"{output_filename}.gz"
    facts_dir = root / SOURCE_FACTS_DIR
    facts_dir.mkdir(parents=True, exist_ok=True)
    output_path = facts_dir / output_filename

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
    if manifest.last_compacted:
        candidate = _safe_pack_path(root, manifest.last_compacted, sink)
        if candidate is not None:
            candidate = candidate.resolve()
            prior_files = [f for f in originals if f.resolve() == candidate]

    def _last_record_wins(
        files: list[Path],
    ) -> tuple[dict[str, SourceAbiTu], list[SourceAbiTu]]:
        """Read *files* in (sorted, deterministic) order, keeping the last-seen
        record per tu_id -- append order within/between files, never a
        timestamp comparison. A no-tu_id record is never deduped ("" cannot
        be treated as a real shared identity between otherwise-unrelated TUs,
        Codex review, P2)."""
        by_id: dict[str, SourceAbiTu] = {}
        no_id: list[SourceAbiTu] = []
        for tu in read_source_fact_files(files, diagnostics=sink):
            if tu.tu_id:
                by_id[tu.tu_id] = tu
            else:
                no_id.append(tu)
        return by_id, no_id

    before_diag_count = len(sink)
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

    if remove_originals:
        for f in fresh_files:
            with contextlib.suppress(OSError):
                f.unlink()
        # output_path itself (if it was among originals) was already
        # replaced in place above, not deleted here (fresh_files excludes it).

    # A manifest that names explicit individual files is repointed at the
    # single merged file (those originals are gone). But a manifest whose
    # "explicit" entry is just a directory reference to SOURCE_FACTS_DIR
    # itself — what the Clang facts plugin's ensureManifest() always writes,
    # and what a bare default manifest effectively means too — must NOT be
    # narrowed to the merged filename: ensureManifest() never rewrites an
    # existing manifest.json, so a narrowed single-file entry would
    # permanently hide every per-TU file a later incremental build writes
    # into that same directory from every subsequent ingest/compact/validate
    # (Codex review, P2). The merged file already lives inside
    # SOURCE_FACTS_DIR, so leaving the directory reference in place keeps
    # discovering it — and any future sibling files — via the existing scan.
    # Compared as normalized Path objects, not raw strings: _iter_source_
    # fact_files (via pathlib) treats "source_facts", "source_facts/", and
    # "./source_facts" as the exact same directory reference, so a byte-
    # exact string comparison would miss those equivalent spellings and
    # narrow them anyway (Codex review, P2).
    is_plain_directory_scan = len(manifest.source_facts) == 1 and Path(
        manifest.source_facts[0]
    ) == Path(SOURCE_FACTS_DIR)
    manifest_changed = False
    if manifest.source_facts and not is_plain_directory_scan:
        manifest.source_facts = [f"{SOURCE_FACTS_DIR}/{output_filename}"]
        manifest_changed = True

    # Record this run's output as the pack's new "last compaction", so a
    # later rerun recognizes it as stale (see prior_files above) regardless
    # of what output filename/compression setting that rerun uses.
    new_last_compacted = f"{SOURCE_FACTS_DIR}/{output_filename}"
    if manifest.last_compacted != new_last_compacted:
        manifest.last_compacted = new_last_compacted
        manifest_changed = True

    if manifest_changed:
        _write_manifest(root, manifest)

    return output_path
