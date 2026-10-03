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

A producer calls :func:`init_inputs_pack` once, then
:func:`append_source_facts` per compiled translation unit (the ``abicheck-cc``
wrapper). The one-shot ``write_inputs_pack`` had only test callers and now
lives in ``tests/_inputs_pack_writer.py``.

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
import tempfile
from collections.abc import Iterable
from pathlib import Path

from .inputs_pack import (
    INPUTS_KIND,
    INPUTS_MANIFEST_NAME,
    SOURCE_FACTS_DIR,
    InputsManifest,
)
from .source_abi import SourceAbiTu

#: Default JSONL file the incremental writer appends to when no per-TU name is
#: given. A per-TU name (see :func:`facts_filename`) keeps parallel wrapper
#: invocations from racing on one file.
DEFAULT_FACTS_FILE = "facts.jsonl"


def _now() -> str:
    return _dt.datetime.now(_dt.UTC).isoformat()


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
    # the former compact_inputs_pack's output_filename (Codex review, P2).
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


#: Default output filename for post-build compaction (P1 #21).
