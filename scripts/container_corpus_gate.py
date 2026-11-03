#!/usr/bin/env python3
"""``container-corpus`` gate: every storage-v2 container keeps a frozen
sample written under the encoder versions this checkout declares (plan
``storage-format-v2.md`` A1.9, ADR-062 D14-D18).

A sibling of ``generation_corpus_gate.py``, imported by
``check_ai_readiness.py``. Pure stdlib: it reads the sources with ``re`` and
the corpus with ``json``/``hashlib`` and never imports ``abicheck``, so it
runs before ``pip install``.

``tests/fixtures/containers/manifest.json`` must name, for each of
``SAMPLES``, at least one frozen entry whose file (or package directory)
exists with the recorded sha256. The newest entry of each sample must record
the scalar encoder versions the sources declare now (``VERSION_SOURCES``,
restricted to the keys that sample records). So bumping a container or
snapshot schema version fails until
``python scripts/freeze_container_corpus.py --sample <name>`` adds an entry
under the new version (the older entries stay, as what the bumped reader
must still decode). ``tests/test_container_corpus.py`` checks the rest:
``section_schema_versions`` (a computed mapping this gate cannot read without
importing), that re-encoding each frozen container reproduces its bytes
unless a version moved with a registered migration, and that each one
compares clean against a fresh production.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Protocol

ROOT = Path(__file__).resolve().parent.parent
CORPUS = ROOT / "tests" / "fixtures" / "containers"
CHECK = "container-corpus"

#: The samples ``tests/_container_corpus.py`` produces (the test asserts the
#: two lists agree).
SAMPLES = (
    "clang_snapshot",
    "g38_document",
    "g40_archive",
    "macho_snapshot",
    "package",
    "pe_snapshot",
)

#: Where each scalar encoder version is declared: ``key -> (file, pattern)``.
VERSION_SOURCES: dict[str, tuple[str, str]] = {
    "snapshot_schema_version": (
        "abicheck/storage/snapshot_schema_versions.py",
        r"^SCHEMA_VERSION\s*:\s*int\s*=\s*(\d+)",
    ),
    "bundle_facts_schema_version": (
        "abicheck/model/bundle_facts.py",
        r"^BUNDLE_FACTS_SCHEMA_VERSION\s*=\s*(\d+)",
    ),
    "bundle_archive_schema_version": (
        "abicheck/storage/bundle_facts_archive.py",
        r"^BUNDLE_ARCHIVE_SCHEMA_VERSION\s*=\s*(\d+)",
    ),
    "package_format_version": (
        "abicheck/storage/versioning.py",
        r"^PACKAGE_FORMAT_VERSION\s*=\s*(\d+)",
    ),
    "ref_schema_version": (
        "abicheck/storage/package_publish.py",
        r"^REF_SCHEMA_VERSION\s*=\s*(\d+)",
    ),
    "bundle_variant_spec_schema_version": (
        "abicheck/workflows/bundle_variants_capture.py",
        r"^BUNDLE_VARIANT_SPEC_SCHEMA_VERSION\s*=\s*(\d+)",
    ),
}


class _FindingsLike(Protocol):
    def err(self, check: str, msg: str) -> None: ...


def declared_versions(root: Path = ROOT) -> dict[str, int | None]:
    """Each scalar encoder version as the sources declare it (``None`` when
    the declaration is gone)."""
    out: dict[str, int | None] = {}
    for key, (relpath, pattern) in VERSION_SOURCES.items():
        path = root / relpath
        text = path.read_text(encoding="utf-8") if path.is_file() else ""
        match = re.search(pattern, text, re.MULTILINE)
        out[key] = int(match.group(1)) if match else None
    return out


def digest(path: Path) -> str:
    """sha256 of a file, or of a directory's files keyed by relative path
    (``tests/_container_corpus.file_digest``)."""
    h = hashlib.sha256()
    if path.is_dir():
        for member in sorted(p for p in path.rglob("*") if p.is_file()):
            rel = member.relative_to(path).as_posix()
            h.update(rel.encode() + b"\0" + member.read_bytes() + b"\0")
    else:
        h.update(path.read_bytes())
    return h.hexdigest()


def _number(key: str) -> int:
    tail = key.rsplit("/", 1)[-1]
    return int(tail) if tail.isdigit() else -1


def evaluate(
    manifest: dict | None, corpus: Path, declared: dict[str, int | None]
) -> list[str]:
    """Every violation, as a message; empty when the corpus is current."""
    if manifest is None:
        return [f"{corpus.name}/manifest.json is missing"]
    for key, value in declared.items():
        if value is None:
            relpath = VERSION_SOURCES[key][0]
            return [
                f"{relpath} no longer declares {key}; update "
                "scripts/container_corpus_gate.py's VERSION_SOURCES"
            ]
    problems: list[str] = []
    entries = manifest.get("entries") or {}
    for sample in SAMPLES:
        mine = sorted(
            (k for k, e in entries.items() if e.get("sample") == sample), key=_number
        )
        if not mine:
            problems.append(
                f"no frozen {sample} sample: run "
                f"`python scripts/freeze_container_corpus.py --sample {sample}`"
            )
            continue
        for key in mine:
            entry = entries[key]
            path = corpus / str(entry.get("path", ""))
            if not path.exists():
                problems.append(f"frozen entry {key} names a missing {path.name}")
            elif digest(path) != entry.get("sha256"):
                problems.append(
                    f"frozen entry {key} was modified after it was frozen "
                    "(sha256 differs); refreeze it instead of editing it"
                )
        newest = entries[mine[-1]]
        recorded = newest.get("versions") or {}
        for name, value in sorted(declared.items()):
            if name in recorded and recorded[name] != value:
                problems.append(
                    f"{name} is {value} but the newest frozen {sample} sample "
                    f"({mine[-1]}) was written under {recorded[name]}: run "
                    f"`python scripts/freeze_container_corpus.py --sample {sample}` "
                    "(it adds an entry and keeps the old one)"
                )
    return problems


def check_container_corpus(findings: _FindingsLike) -> None:
    manifest_path = CORPUS / "manifest.json"
    manifest = (
        json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest_path.is_file()
        else None
    )
    for problem in evaluate(manifest, CORPUS, declared_versions()):
        findings.err(CHECK, problem)
