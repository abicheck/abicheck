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
    INPUTS_MANIFEST_NAME,
    SOURCE_FACTS_DIR,
    InputsManifest,
    _iter_source_fact_files,
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
    """
    root = Path(root)
    (root / SOURCE_FACTS_DIR).mkdir(parents=True, exist_ok=True)
    mpath = root / INPUTS_MANIFEST_NAME
    if mpath.is_file():
        # Defensive: a manifest left partial by a non-atomic writer on an old pack
        # (our writes are atomic) re-initializes rather than raising and losing
        # this TU's facts.
        with contextlib.suppress(ValueError, OSError):
            return InputsManifest.from_dict(json.loads(mpath.read_text(encoding="utf-8")))
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
) -> Path:
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
    still carried forward, never dropped (Codex review, P2). And a read that
    produces new diagnostics (a malformed/unreadable original) still writes
    the merged output best-effort, but leaves *every* original in place
    rather than deleting evidence of a lossy compaction (CodeRabbit review,
    P2) — check *diagnostics* after the call.

    *remove_originals* deletes the per-TU files that were merged once the
    merged file is written (skipped when the read above was lossy, see
    above), so a later ingest cannot double-count TUs by reading both the
    merged file and its stale sources. A manifest that names explicit
    ``source_facts`` entries is repointed at the single merged file; the
    default (auto-scan of ``source_facts/``) needs no manifest change — the
    new file already lives where the scan looks.
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
    prior_output_files = [
        f for f in originals if f.resolve() == output_path.resolve()
    ]
    fresh_files = [f for f in originals if f.resolve() != output_path.resolve()]

    before_diag_count = len(sink)
    prior_tus = read_source_fact_files(prior_output_files, diagnostics=sink)
    fresh_tus = read_source_fact_files(fresh_files, diagnostics=sink)
    lossy_read = len(sink) > before_diag_count

    fresh_ids = {tu.tu_id for tu in fresh_tus}
    tus = [tu for tu in prior_tus if tu.tu_id not in fresh_ids] + fresh_tus

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

    if lossy_read:
        sink.append(
            "compaction read produced new diagnostics (a malformed/unreadable "
            "original); originals were kept, not deleted, so the raw evidence "
            "survives for inspection"
        )
    elif remove_originals:
        for f in fresh_files:
            with contextlib.suppress(OSError):
                f.unlink()
        # prior_output_files (if any) was already replaced in place above.

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
    if manifest.source_facts and manifest.source_facts != [SOURCE_FACTS_DIR]:
        manifest.source_facts = [f"{SOURCE_FACTS_DIR}/{output_filename}"]
        _write_manifest(root, manifest)

    return output_path
