"""Test-only one-shot writer for a Flow-2 ``abicheck_inputs/`` pack.

Moved here from ``abicheck/buildsource/inputs_emit.py``
(dead-code-and-single-owner, library-API pass): no production producer writes
a pack in one call -- the ``abicheck-cc`` wrapper and the Clang plugin write
incrementally through ``init_inputs_pack``/``append_source_facts``. It builds
on those same production primitives, so a pack it writes still exercises the
real format and round-trips through ``ingest_inputs_pack``.
"""

from __future__ import annotations

import shutil
from collections.abc import Iterable
from pathlib import Path

from abicheck.buildsource.inputs_emit import (
    DEFAULT_FACTS_FILE,
    _now,
    _write_manifest,
    append_source_facts,
)
from abicheck.buildsource.inputs_pack import (
    DEFAULT_COMPILE_DB_REL,
    SOURCE_FACTS_DIR,
    InputsManifest,
)
from abicheck.buildsource.source_abi import SourceAbiTu


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
