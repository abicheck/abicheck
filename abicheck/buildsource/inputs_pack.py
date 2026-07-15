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

"""Flow-2 build-emitted facts artifact protocol (ADR-035 D5, G19.4).

The product build drops a self-describing ``abicheck_inputs/`` directory next to
its binary; abicheck then **ingests** the normalized facts without re-running a
compiler frontend (Flow 2). This is the closed-source / vendor path: the build
already paid the parse cost, so abicheck just folds the emitted facts into a
:class:`~abicheck.buildsource.pack.BuildSourcePack` and rides the existing
``merge`` flow (artifact-side dump + source-side facts → one baseline).

Layout::

    abicheck_inputs/
      manifest.json              # kind: abicheck_inputs, library/version, paths
      binary/…                   # the shipped artifact (dumped separately, L0-L2)
      headers/…                  # public headers (dumped separately, L2)
      build/compile_commands.json  # optional → L3 build evidence
      source_facts/*.jsonl       # PREFERRED — normalized SourceAbiTu, one per line → L4/L5
      raw_ast/*.json.zst         # optional, debug/forensic only — never ingested
      pp/*.macros.jsonl  deps/*.d  # optional preprocessor/dep provenance

**Canonical rule (ADR-035 D5):** normalized ``source_facts/*.jsonl`` are the
comparison format. ``raw_ast/`` is an MVP-ingest / forensic fallback only and is
*not* read here — a build that can only emit raw AST should normalize it into
``source_facts`` before dropping the pack.

Ingestion is **pure parsing**: it reads JSON, never runs a tool, so it is safe in
CI with no compiler (mirrors the ADR-028 D6 non-executing adapter discipline).
The binary/header L0-L2 dump stays the artifact side's job (normal ``dump``); this
module produces the source-side L3/L4/L5 pack that ``merge`` folds against it.
"""

from __future__ import annotations

import datetime as _dt
import gzip
import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .adapters.compile_db import CompileDbAdapter
from .build_evidence import BuildEvidence
from .inline import build_inline_coverage
from .model import BuildSourceManifest, ExtractorRecord
from .pack import BuildSourcePack
from .source_abi import SourceAbiTu
from .source_link import link_source_abi

#: Manifest ``kind`` discriminator — distinguishes a Flow-2 inputs pack from an
#: on-disk :class:`BuildSourcePack` (whose manifest carries
#: ``build_source_pack_version`` and no ``kind``).
INPUTS_KIND = "abicheck_inputs"

#: Protocol version, independent of every other schema version (ADR-028 D8).
ABICHECK_INPUTS_VERSION = 1

INPUTS_MANIFEST_NAME = "manifest.json"
#: Default location of the L3 compile DB inside the pack.
DEFAULT_COMPILE_DB_REL = "build/compile_commands.json"
#: Default sub-directory of normalized per-TU source facts.
SOURCE_FACTS_DIR = "source_facts"


def _opt_str(raw: Any, default: str = "") -> str:
    """Coerce an optional manifest string, treating JSON ``null`` as the default.

    A producer that serializes an unset optional field as ``null`` would
    otherwise become the literal ``"None"`` via ``str(None)`` — and for a path
    field like ``compile_db`` that silently points at a nonexistent file and
    drops the layer (Codex review). ``None`` → *default*; anything else →
    ``str(raw)``.
    """
    return default if raw is None else str(raw)


def _safe_pack_path(root: Path, entry: str, diagnostics: list[str]) -> Path | None:
    """Resolve a manifest-relative path, refusing absolute or escaping entries.

    Protocol paths are documented as **pack-relative** (ADR-035 D5). An absolute
    path, a ``..`` that climbs out of the pack, or a symlink that resolves
    outside it could read arbitrary runner files into the L4/L5 baseline, so it
    is refused with a diagnostic rather than followed (Codex review). The check
    is on the *resolved* path, so symlink escapes are caught too. Returns the
    joined (unresolved, for a stable user-facing path) path when safe, else
    ``None``.
    """
    if Path(entry).is_absolute():
        diagnostics.append(f"refused absolute path outside pack: {entry}")
        return None
    root_resolved = root.resolve()
    candidate = (root / entry).resolve()
    if candidate != root_resolved and not candidate.is_relative_to(root_resolved):
        diagnostics.append(f"refused path escaping pack root: {entry}")
        return None
    return root / entry


@dataclass
class InputsManifest:
    """Declarative manifest for an ``abicheck_inputs/`` pack (Flow 2).

    Every field is optional/defaulted so a hand-written or older-protocol
    manifest never aborts a load (the forward-compat convention used across
    ``buildsource``). Relative paths are resolved against the pack root.
    """

    kind: str = INPUTS_KIND
    abicheck_inputs_version: int = ABICHECK_INPUTS_VERSION
    library: str = ""
    version: str = ""
    #: Free-text producer id, e.g. "abicheck-clang-plugin 0.3" or "abicheck-cc".
    created_by: str = ""
    created_at: str = ""
    #: Relative path to the shipped artifact (informational; dumped separately).
    binary: str = ""
    #: Relative paths to public headers (informational; dumped separately).
    headers: list[str] = field(default_factory=list)
    #: Relative path to the L3 compile DB; "" → auto-detect DEFAULT_COMPILE_DB_REL.
    compile_db: str = ""
    #: Relative paths/dirs of normalized source-fact files; "" → SOURCE_FACTS_DIR.
    source_facts: list[str] = field(default_factory=list)
    #: Exported (mangled) symbols the build already knows, if any. When empty the
    #: surface is relinked against the artifact side's exports during ``merge``.
    exported_symbols: list[str] = field(default_factory=list)
    #: Pack-level canonical fact-set identity (ADR-038 C.8), when the producer
    #: declares one at the manifest level (the Clang plugin does; a producer
    #: that only stamps per-TU records leaves this ``{}`` and
    #: ``inputs validate`` falls back to the TU records).
    fact_set: dict[str, Any] = field(default_factory=dict)
    #: Pack-relative path to the most recent ``inputs compact`` output, if any
    #: ("" before the first compaction). Lets a later rerun recognize that
    #: file as stale regardless of *this* run's --output-filename/--compress
    #: choice, without comparing file mtimes -- a rebuild's freshly-rewritten
    #: per-TU file and the previous compaction's write can land in the same
    #: filesystem timestamp tick, which an mtime-based "which is newer" check
    #: cannot break correctly (Codex review, P2).
    last_compacted: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "abicheck_inputs_version": self.abicheck_inputs_version,
            "library": self.library,
            "version": self.version,
            "created_by": self.created_by,
            "created_at": self.created_at,
            "binary": self.binary,
            "headers": list(self.headers),
            "compile_db": self.compile_db,
            "source_facts": list(self.source_facts),
            "exported_symbols": list(self.exported_symbols),
            "fact_set": dict(self.fact_set),
            "last_compacted": self.last_compacted,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> InputsManifest:
        def _str_list(key: str) -> list[str]:
            raw = d.get(key)
            return [str(x) for x in raw if x] if isinstance(raw, list) else []

        # A hand-written/third-party manifest's fact_set might not be a
        # mapping at all (e.g. a string) — dict(fs_raw) would raise before
        # `inputs validate` could produce a report, even though a malformed
        # optional field is supposed to degrade gracefully like everything
        # else here (Codex review, P2).
        fs_raw = d.get("fact_set")
        fact_set = dict(fs_raw) if isinstance(fs_raw, dict) else {}

        return cls(
            kind=_opt_str(d.get("kind"), INPUTS_KIND),
            abicheck_inputs_version=int(
                d.get("abicheck_inputs_version", ABICHECK_INPUTS_VERSION) or ABICHECK_INPUTS_VERSION
            ),
            library=_opt_str(d.get("library")),
            version=_opt_str(d.get("version")),
            created_by=_opt_str(d.get("created_by")),
            created_at=_opt_str(d.get("created_at")),
            binary=_opt_str(d.get("binary")),
            headers=_str_list("headers"),
            compile_db=_opt_str(d.get("compile_db")),
            source_facts=_str_list("source_facts"),
            exported_symbols=_str_list("exported_symbols"),
            fact_set=fact_set,
            last_compacted=_opt_str(d.get("last_compacted")),
        )


@dataclass
class IngestedInputs:
    """Result of folding a Flow-2 pack into an embeddable :class:`BuildSourcePack`."""

    manifest: InputsManifest
    pack: BuildSourcePack
    tu_count: int = 0
    diagnostics: list[str] = field(default_factory=list)


def is_inputs_pack(path: Path | str) -> bool:
    """Whether *path* is a Flow-2 ``abicheck_inputs/`` directory.

    A directory whose ``manifest.json`` declares ``kind: abicheck_inputs``. The
    explicit discriminator keeps this distinct from a :class:`BuildSourcePack`
    directory (``manifest.json`` with ``build_source_pack_version``), so
    ``merge`` can route a directory input to the right loader.
    """
    p = Path(path)
    manifest = p / INPUTS_MANIFEST_NAME
    if not (p.is_dir() and manifest.is_file()):
        return False
    try:
        with manifest.open(encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return False
    return isinstance(data, dict) and data.get("kind") == INPUTS_KIND


def load_inputs_manifest(root: Path | str) -> InputsManifest:
    """Load and parse the pack manifest.

    Raises ``FileNotFoundError`` if absent, ``ValueError`` if *root* carries a
    manifest.json but it does not declare ``kind: abicheck_inputs`` (e.g. a
    :class:`~.pack.BuildSourcePack` directory, which uses a different manifest
    schema entirely) — without this check, ``InputsManifest.from_dict()``'s
    forward-compat ``kind`` default would silently accept it as a Flow-2 pack,
    letting a write path like :func:`~.inputs_emit.compact_inputs_pack`
    corrupt an unrelated directory's manifest (Codex review, P2).
    """
    root = Path(root)
    manifest_path = root / INPUTS_MANIFEST_NAME
    if not manifest_path.is_file():
        raise FileNotFoundError(
            f"No abicheck_inputs manifest at {manifest_path}. Expected a Flow-2 "
            f"pack with a manifest.json declaring kind: {INPUTS_KIND}."
        )
    with manifest_path.open(encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise ValueError(f"{manifest_path} must contain a JSON object.")
    if data.get("kind") != INPUTS_KIND:
        raise ValueError(
            f"{manifest_path} does not declare kind: {INPUTS_KIND} — not a "
            "Flow-2 abicheck_inputs pack."
        )
    return InputsManifest.from_dict(data)


def _iter_source_fact_files(
    root: Path, manifest: InputsManifest, diagnostics: list[str] | None = None
) -> list[Path]:
    """Resolve the normalized source-fact files to read.

    Honours an explicit ``source_facts`` list in the manifest (each entry a file
    or a directory of ``*.jsonl``); otherwise scans the default
    ``source_facts/`` sub-directory. Entries are constrained to the pack root
    (absolute/escaping paths are refused, see :func:`_safe_pack_path`). Files are
    returned sorted for deterministic ingest order.
    """
    sink = diagnostics if diagnostics is not None else []
    explicit = bool(manifest.source_facts)
    entries = manifest.source_facts or [SOURCE_FACTS_DIR]
    files: list[Path] = []
    for entry in entries:
        target = _safe_pack_path(root, entry, sink)
        if target is None:
            continue
        before = len(files)
        if target.is_dir():
            # ``.jsonl`` is the canonical form; a ``.json`` array file is also
            # accepted so a producer that cannot stream lines still ingests.
            # A gzip-compressed sibling of either (P1 #22: post-build
            # compaction can gzip the merged file to shrink pack transfer
            # size) is read transparently — same decoded facts either way.
            files.extend(target.glob("*.jsonl"))
            files.extend(target.glob("*.jsonl.gz"))
            files.extend(target.glob("*.json"))
            files.extend(target.glob("*.json.gz"))
        elif target.is_file():
            files.append(target)
        # An *explicitly named* entry that resolves to nothing (typo, empty or
        # missing dir) must not vanish quietly and leave an L3-only baseline
        # claiming L4 facts (Codex review). The default auto-scan may be empty.
        if explicit and len(files) == before:
            sink.append(f"source_facts entry resolved to no readable fact files: {entry}")
    # Re-validate each discovered file on its *resolved* path: a file inside an
    # in-pack directory can itself be a symlink pointing outside the pack, which
    # the per-entry guard above does not catch (Codex review). Drop any escapee
    # with a diagnostic so a third-party pack cannot pull runner-local facts in.
    root_resolved = root.resolve()
    safe: list[Path] = []
    for f in files:
        if not f.resolve().is_relative_to(root_resolved):
            sink.append(f"refused source-fact file escaping pack root: {f.name}")
            continue
        # A glob can match a non-regular entry (a directory named `old.jsonl`, a
        # FIFO from an unpacked archive); read_text() would error or block on it.
        # Skip with a diagnostic instead of aborting the ingest (Codex review).
        if not f.is_file():
            sink.append(f"skipped non-regular source-fact entry: {f.name}")
            continue
        safe.append(f)
    return sorted(set(safe))


def _parse_tu_records(text: str, source: str, diagnostics: list[str]) -> list[SourceAbiTu]:
    """Parse one ``source_facts`` file into :class:`SourceAbiTu` records.

    Accepts JSON-Lines (one TU object per line, the preferred form), a single
    JSON array of TU objects, or a single TU object. Malformed lines are skipped
    with a diagnostic rather than aborting the whole ingest (forward-compat).
    """
    def _convert(obj: Any, where: str) -> SourceAbiTu | None:
        """Convert one record, skipping (not aborting) a schema-invalid TU.

        ``SourceAbiTu.from_dict`` / ``SourceEntity.from_dict`` require some keys
        (e.g. an entity ``id``), so a valid-JSON-but-schema-invalid record raises
        ``KeyError``. Treat that like a malformed line — one bad TU must not
        reject an otherwise usable pack (Codex review)."""
        try:
            return SourceAbiTu.from_dict(obj)
        except (KeyError, ValueError, TypeError) as exc:
            diagnostics.append(f"{where}: skipped schema-invalid TU record ({exc})")
            return None

    stripped = text.strip()
    if not stripped:
        return []
    # Whole-file JSON array or single object (non-JSONL producers): try parsing
    # the entire file first; a true JSON-Lines file with >1 record fails this and
    # falls through to the line-by-line path below.
    try:
        whole = json.loads(stripped)
    except ValueError:
        whole = None
    if isinstance(whole, list):
        out: list[SourceAbiTu] = []
        for i, x in enumerate(whole):
            if not isinstance(x, dict):
                diagnostics.append(f"{source}[{i}]: skipped non-object record")
                continue
            tu = _convert(x, f"{source}[{i}]")
            if tu is not None:
                out.append(tu)
        return out
    if isinstance(whole, dict):
        tu = _convert(whole, source)
        return [tu] if tu is not None else []
    tus: list[SourceAbiTu] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except ValueError as exc:
            diagnostics.append(f"{source}:{lineno}: skipped malformed JSON line ({exc})")
            continue
        if isinstance(obj, dict):
            tu = _convert(obj, f"{source}:{lineno}")
            if tu is not None:
                tus.append(tu)
        else:
            diagnostics.append(f"{source}:{lineno}: skipped non-object record")
    return tus


def _read_text_maybe_gz(path: Path) -> str:
    """Read *path* as UTF-8 text, transparently decompressing a ``.gz`` file.

    Compression is execution policy, not fact content (P1 #22/#25): a gzip'd
    ``source_facts`` file decodes to the exact same JSON text a plain one
    would, so this is the only place a caller needs to know the file might be
    compressed.
    """
    if path.suffix == ".gz":
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            return fh.read()
    return path.read_text(encoding="utf-8")


def read_source_fact_files(
    paths: Iterable[Path], *, diagnostics: list[str] | None = None
) -> list[SourceAbiTu]:
    """Read+parse an explicit list of source-fact files.

    The per-file half of :func:`read_source_facts`, split out so a caller
    that needs to control file resolution/order itself — e.g.
    :func:`~.inputs_emit.compact_inputs_pack` reading a stale prior-
    compaction output separately from fresh per-TU files so it can prefer
    the fresher record for a duplicate ``tu_id`` — reuses the same parsing
    without re-deriving the file list (Codex review, P2).
    """
    sink = diagnostics if diagnostics is not None else []
    tus: list[SourceAbiTu] = []
    for path in paths:
        try:
            text = _read_text_maybe_gz(path)
        except OSError as exc:
            sink.append(f"{path.name}: could not read/decompress ({exc})")
            continue
        tus.extend(_parse_tu_records(text, path.name, sink))
    return tus


def read_source_facts(
    root: Path | str,
    manifest: InputsManifest | None = None,
    *,
    diagnostics: list[str] | None = None,
) -> list[SourceAbiTu]:
    """Read every normalized per-TU dump from a pack's ``source_facts/`` files.

    When a *diagnostics* sink is supplied, per-record parse warnings (malformed
    or non-object lines that were skipped) are appended to it, so a caller can
    surface them instead of silently dropping bad TUs (Codex review).
    """
    root = Path(root)
    manifest = manifest or load_inputs_manifest(root)
    sink = diagnostics if diagnostics is not None else []
    return read_source_fact_files(
        _iter_source_fact_files(root, manifest, sink), diagnostics=sink
    )


def _load_build_evidence(root: Path, manifest: InputsManifest, diagnostics: list[str]) -> BuildEvidence | None:
    """Parse the pack's compile DB into L3 build evidence, if present."""
    rel = manifest.compile_db or DEFAULT_COMPILE_DB_REL
    compile_db = _safe_pack_path(root, rel, diagnostics)  # refuse absolute/escaping
    if compile_db is None or not compile_db.is_file():
        # An *explicitly named* compile DB that is absent must be reported — a typo
        # or stale pack would otherwise silently drop all L3 (Codex review). The
        # default auto-detect path is allowed to disappear quietly (None refused →
        # already diagnosed by _safe_pack_path).
        if manifest.compile_db and compile_db is not None:
            diagnostics.append(f"compile_db {rel}: file not found")
        return None
    try:
        return CompileDbAdapter(compile_db).collect()
    except Exception as exc:  # malformed compile DB → skip L3, keep ingesting
        diagnostics.append(f"compile DB {rel}: {exc}")
        return None


def ingest_inputs_pack(
    root: Path | str,
    *,
    exported_symbols: Iterable[str] = (),
) -> IngestedInputs:
    """Fold a Flow-2 ``abicheck_inputs/`` pack into an embeddable pack.

    Reads the normalized ``source_facts/*.jsonl`` (→ L4 surface via
    :func:`link_source_abi`), the optional ``build/compile_commands.json``
    (→ L3 :class:`BuildEvidence`), folds the L5 source graph, and stamps the
    coverage manifest. ``exported_symbols`` (union of the manifest's own list and
    the caller's) seed the L4 decl→symbol linking; when empty the surface is
    relinked against the artifact side's exports during ``merge`` (the existing
    A1 path). Pure parsing — never invokes a compiler.
    """
    root = Path(root)
    manifest = load_inputs_manifest(root)
    diagnostics: list[str] = []

    # Thread the diagnostics sink so skipped/malformed source-fact records are
    # surfaced in the extractor ledger + IngestedInputs, not silently dropped.
    tus = read_source_facts(root, manifest, diagnostics=diagnostics)
    exports = sorted(set(manifest.exported_symbols) | set(exported_symbols))

    surface = None
    if tus:
        # Preserve the TU target id when the pack describes a single target, so
        # the linked surface's `target_id` is set and the L5 graph emits the
        # BINARY_EXPORTS_SYMBOL target edges `localize_symbol()` needs (Codex
        # review). Ambiguous (multi-target) packs leave it empty.
        tu_targets = {tu.target_id for tu in tus if tu.target_id}
        target_id = next(iter(tu_targets)) if len(tu_targets) == 1 else ""
        surface = link_source_abi(
            tus,
            exported_symbols=exports,
            library=manifest.library,
            target_id=target_id,
        )

    build_evidence = _load_build_evidence(root, manifest, diagnostics)
    has_build = build_evidence is not None and bool(
        build_evidence.compile_units or build_evidence.targets
    )

    graph = None
    if surface is not None or has_build:
        from .source_graph import build_source_graph

        graph = build_source_graph(build_evidence or BuildEvidence(), source_abi=surface)
        graph.finalize()

    extractor = ExtractorRecord(
        name="abicheck_inputs",
        version=str(manifest.abicheck_inputs_version),
        # Any skipped record (a diagnostic) means the ingest was lossy → partial.
        status="ok" if (tus or has_build) and not diagnostics else "partial",
        detail=(
            f"Flow-2 ingest: {len(tus)} source-fact TUs"
            + (f", L3 from {manifest.compile_db or DEFAULT_COMPILE_DB_REL}" if has_build else "")
            + (f", {len(diagnostics)} skipped/diagnostic" if diagnostics else "")
            + (f" (produced by {manifest.created_by})" if manifest.created_by else "")
        ),
        diagnostics=list(diagnostics),
    )
    coverage = build_inline_coverage(
        build_evidence or BuildEvidence(), has_build, surface, graph, [extractor]
    )

    pack = BuildSourcePack(
        root=Path(""),
        manifest=BuildSourceManifest(
            abicheck_version="",
            created_at=_dt.datetime.now(_dt.timezone.utc).isoformat(),
            extractors=[extractor],
            coverage=coverage,
        ),
        build_evidence=build_evidence,
        source_abi=surface,
        source_graph=graph,
    )
    return IngestedInputs(
        manifest=manifest, pack=pack, tu_count=len(tus), diagnostics=diagnostics
    )
