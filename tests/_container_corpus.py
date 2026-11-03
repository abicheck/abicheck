# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""How each frozen storage-v2 container sample is produced (plan
``storage-format-v2.md`` A1.9, ADR-062 D14-D18).

``tests/fixtures/containers/`` freezes one sample of every container and
every platform/frontend a stored operand can arrive as. The generation corpus
(``tests/fixtures/generations/``) covers only a single-file ELF/castxml
snapshot. Each sample is produced from committed sources through the public
CLI, so ``scripts/freeze_container_corpus.py`` (which writes them) and
``tests/test_container_corpus.py`` (which re-produces them as the fresh
counterpart) run the exact same steps:

=================  ==========================================  ===============
sample             container                                   produced by
=================  ==========================================  ===============
``g38_document``   G38 ``BundleFacts`` JSON document            ``dump DIR --bundle-facts-out x.json.gz``
``g40_archive``    G40 content-addressed zip archive            ``dump DIR --bundle-facts-out x.zip``
``package``        directory ``ProjectSnapshot`` package        ``project capture-variants``
``clang_snapshot`` single-file snapshot, clang header frontend  ``dump`` with ``ABICHECK_AST_FRONTEND=clang``
``macho_snapshot`` single-file snapshot of a Mach-O dylib       ``dump`` of ``_macho_synthetic.write_dylib``
``pe_snapshot``    single-file snapshot of a PE DLL             ``dump`` of ``sources/corpus_pe.dll``
=================  ==========================================  ===============

The ELF release is two libraries: the generation corpus's C++ library
(``tests/fixtures/generations/corpus/``, shared, not copied) and the C
library in ``sources/extra.{h,c}``. Two members are what make a release
capture record a public surface. The Mach-O image is built in Python, so
nothing outside this checkout is needed for it. The PE DLL is committed
next to the source it was built from (``PE_BUILD``), because the CI Linux
image has no ``ld.lld``.

A non-``test_`` helper: import it, do not copy it.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import shutil
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tests._abicheck_subprocess import REPO_ROOT, abicheck_command
from tests._macho_synthetic import (
    EXPORT_WEAK_DEFINITION,
    N_EXT,
    N_SECT,
    N_UNDF,
    MachoSymbol,
    write_dylib,
)

CORPUS_ROOT = REPO_ROOT / "tests" / "fixtures" / "containers"
SOURCES = CORPUS_ROOT / "sources"
#: The C++ library is the generation corpus's own (its header stores a type
#: spelling at each position an extractor writes one).
CXX_SOURCES = REPO_ROOT / "tests" / "fixtures" / "generations" / "corpus"
MANIFEST_PATH = CORPUS_ROOT / "manifest.json"

#: The one declared bundle variant of the ``package`` sample.
VARIANT = "linux-x86_64-gcc"
VARIANT_CONFIG = f"""\
bundle_variants:
  {VARIANT}:
    target_triple: x86_64-linux-gnu
    compiler_family: gcc
    feature_toggles: {{threads: true}}
"""

#: The command ``sources/corpus_pe.dll`` was built with (run from
#: ``sources/``); ``--no-insert-timestamp`` makes the image reproducible.
PE_BUILD = (
    "clang",
    "--target=x86_64-windows-gnu",
    "-shared",
    "-nostdlib",
    "-fuse-ld=lld",
    "-O1",
    "-Wl,--no-insert-timestamp",
    "-o",
    "corpus_pe.dll",
    "corpus_pe.c",
)

#: The Mach-O dylib's symbol table and export trie: plain exports, a weak
#: definition, a private-extern symbol (not exported) and an import.
MACHO_SYMBOLS = (
    MachoSymbol("_macho_corpus_open", N_EXT | N_SECT),
    MachoSymbol("_macho_corpus_close", N_EXT | N_SECT),
    MachoSymbol("__ZN6corpus5Codec6encodeEi", N_EXT | N_SECT),
    MachoSymbol("_macho_corpus_weak", N_EXT | N_SECT, n_desc=0x0080),
    MachoSymbol("_macho_corpus_hidden", 0x10 | N_EXT | N_SECT),
    MachoSymbol("_malloc", N_EXT | N_UNDF, n_sect=0),
)
MACHO_EXPORT_TRIE = (
    ("_macho_corpus_open", 0),
    ("_macho_corpus_close", 0),
    ("__ZN6corpus5Codec6encodeEi", 0),
    ("_macho_corpus_weak", EXPORT_WEAK_DEFINITION),
)


class ProduceError(RuntimeError):
    """A configured tool ran and failed: a failure, never a skip."""


@dataclass(frozen=True)
class Sample:
    """One frozen container: where it lives and how it is produced."""

    name: str
    #: Path under ``CORPUS_ROOT`` (a file, or the ``package`` directory).
    path: str
    #: The external tools producing it needs; empty when nothing beyond
    #: this checkout is (the fresh counterpart then runs in the fast lane).
    tools: tuple[str, ...]
    produce: Callable[[Path, Path], None]


def tool_banner(tool: str) -> str:
    """First line of ``tool --version`` (castxml prints it on stderr)."""
    proc = subprocess.run([tool, "--version"], capture_output=True, text=True)
    lines = (proc.stdout + proc.stderr).strip().splitlines()
    return lines[0].strip() if lines else ""


def abicheck_env(work: Path, **extra: str) -> dict[str, str]:
    """This checkout's ``abicheck``, with no snapshot cache read or written
    (``ABICHECK_REFERENCE_MODE``) and a private cache root, so a sample is
    always a fresh extraction."""
    env = {
        **os.environ,
        "PYTHONPATH": str(REPO_ROOT),
        "ABICHECK_REFERENCE_MODE": "1",
        "ABICHECK_PROGRESS": "0",
        "XDG_CACHE_HOME": str(work / "cache"),
        "LOCALAPPDATA": str(work / "cache"),
    }
    env.pop("ABICHECK_AST_FRONTEND", None)
    env.update(extra)
    return env


def _run(cmd: list[str], *, cwd: Path, env: Mapping[str, str] | None = None) -> None:
    proc = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd, env=env)
    if proc.returncode != 0:
        raise ProduceError(
            f"{' '.join(cmd)} exited {proc.returncode}:\n{proc.stdout}\n{proc.stderr}"
        )


def _abicheck(work: Path, *args: str | Path, **env: str) -> None:
    _run(abicheck_command(*args), cwd=work, env=abicheck_env(work, **env))


def build_elf_release(work: Path) -> Path:
    """``work/release/{libcorpus,libextra}.so``, built from the committed
    sources; returns the release directory."""
    release = work / "release"
    if release.is_dir():
        return release
    staging = work / "release.tmp"
    staging.mkdir(parents=True, exist_ok=True)
    _run(
        [
            "g++",
            "-std=c++17",
            "-shared",
            "-fPIC",
            "-O0",
            "-o",
            str(staging / "libcorpus.so"),
            str(CXX_SOURCES / "corpus.cpp"),
        ],
        cwd=work,
    )
    _run(
        [
            "gcc",
            "-shared",
            "-fPIC",
            "-O0",
            "-o",
            str(staging / "libextra.so"),
            str(SOURCES / "extra.c"),
        ],
        cwd=work,
    )
    staging.rename(release)
    return release


def _release_headers() -> tuple[str, ...]:
    return ("-H", str(CXX_SOURCES / "corpus.h"), "-H", str(SOURCES / "extra.h"))


def _bundle_facts(work: Path, out: Path) -> None:
    release = build_elf_release(work)
    _abicheck(work, "dump", release, *_release_headers(), "--bundle-facts-out", out)


def _package(work: Path, out: Path) -> None:
    release = build_elf_release(work)
    config = work / "variants.abicheck.yml"
    config.write_text(VARIANT_CONFIG, encoding="utf-8")
    _abicheck(
        work,
        "project",
        "capture-variants",
        "--config",
        config,
        "--variant",
        f"{VARIANT}={release}",
        "--variant-header",
        f"{VARIANT}={CXX_SOURCES / 'corpus.h'}",
        "--variant-header",
        f"{VARIANT}={SOURCES / 'extra.h'}",
        "--package",
        out,
    )


def _clang_snapshot(work: Path, out: Path) -> None:
    release = build_elf_release(work)
    _abicheck(
        work,
        "dump",
        release / "libcorpus.so",
        "-H",
        CXX_SOURCES / "corpus.h",
        "--label",
        "1",
        "-o",
        out,
        ABICHECK_AST_FRONTEND="clang",
    )


def write_macho_dylib(path: Path) -> Path:
    return write_dylib(path, list(MACHO_SYMBOLS), export_trie=MACHO_EXPORT_TRIE)


def _macho_snapshot(work: Path, out: Path) -> None:
    dylib = write_macho_dylib(work / "libcorpus.dylib")
    _abicheck(work, "dump", dylib, "--label", "1", "-o", out)


def _pe_snapshot(work: Path, out: Path) -> None:
    dll = work / "corpus_pe.dll"
    shutil.copyfile(SOURCES / "corpus_pe.dll", dll)
    _abicheck(work, "dump", dll, "--label", "1", "-o", out)


_ELF = ("g++", "gcc", "castxml")
SAMPLES: dict[str, Sample] = {
    s.name: s
    for s in (
        Sample("g38_document", "g38/release.json.gz", _ELF, _bundle_facts),
        Sample("g40_archive", "g40/release.zip", _ELF, _bundle_facts),
        Sample("package", "package", _ELF, _package),
        Sample(
            "clang_snapshot",
            "clang/libcorpus.json.gz",
            ("g++", "clang"),
            _clang_snapshot,
        ),
        Sample("macho_snapshot", "macho/libcorpus.json.gz", (), _macho_snapshot),
        Sample("pe_snapshot", "pe/corpus_pe.json.gz", (), _pe_snapshot),
    )
}


def produce(sample: Sample, work: Path) -> Path:
    """Produce *sample* afresh under *work* and return its path there."""
    out = work / "fresh" / sample.path
    out.parent.mkdir(parents=True, exist_ok=True)
    sample.produce(work, out)
    return out


def missing_tools(sample: Sample) -> list[str]:
    return [tool for tool in sample.tools if shutil.which(tool) is None]


def toolchain(sample: Sample) -> dict[str, str]:
    """The banner of every tool *sample* is produced with: a different
    compiler or castxml changes the compile profile the snapshot records, and
    ``compare`` rightly refuses that as a profile mismatch."""
    return {tool: tool_banner(tool) for tool in sample.tools}


def sources_sha256() -> str:
    """Digest of every committed input the samples are produced from."""
    digest = hashlib.sha256()
    for root in (CXX_SOURCES, SOURCES):
        for path in sorted(root.iterdir()):
            digest.update(path.name.encode() + b"\0" + path.read_bytes() + b"\0")
    for symbol in MACHO_SYMBOLS:
        digest.update(repr(symbol).encode())
    digest.update(repr(MACHO_EXPORT_TRIE).encode())
    return digest.hexdigest()


def file_digest(path: Path) -> str:
    """sha256 of *path*, or of a directory's files keyed by relative path."""
    digest = hashlib.sha256()
    if path.is_dir():
        for member in sorted(p for p in path.rglob("*") if p.is_file()):
            rel = member.relative_to(path).as_posix()
            digest.update(rel.encode() + b"\0" + member.read_bytes() + b"\0")
    else:
        digest.update(path.read_bytes())
    return digest.hexdigest()


def read_json_document(path: Path) -> Any:
    """A ``.json``/``.json.gz`` document's decoded JSON."""
    raw = path.read_bytes()
    if path.suffix == ".gz":
        raw = gzip.decompress(raw)
    return json.loads(raw)


def load_manifest() -> dict[str, Any]:
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


#: Which of :func:`encoder_versions`' keys each sample's container is written
#: under. A key outside a sample's list never forces it to be refrozen.
_SNAPSHOT_KEYS = ("snapshot_schema_version", "section_schema_versions")
VERSION_KEYS: dict[str, tuple[str, ...]] = {
    "g38_document": (*_SNAPSHOT_KEYS, "bundle_facts_schema_version"),
    "g40_archive": (
        *_SNAPSHOT_KEYS,
        "bundle_facts_schema_version",
        "bundle_archive_schema_version",
    ),
    "package": (
        *_SNAPSHOT_KEYS,
        "package_format_version",
        "ref_schema_version",
        "bundle_variant_spec_schema_version",
    ),
    "clang_snapshot": _SNAPSHOT_KEYS,
    "macho_snapshot": _SNAPSHOT_KEYS,
    "pe_snapshot": _SNAPSHOT_KEYS,
}


def encoder_versions(sample: str) -> dict[str, Any]:
    """The version of every encoder *sample*'s container passes through, as
    this build declares them. A freeze records them, and the newest frozen
    entry of each sample must equal them (``scripts/container_corpus_gate.py``
    checks the scalar ones before install, the test checks all of them)."""
    from abicheck.model.bundle_facts import BUNDLE_FACTS_SCHEMA_VERSION
    from abicheck.storage.bundle_facts_archive import BUNDLE_ARCHIVE_SCHEMA_VERSION
    from abicheck.storage.dto import SECTION_SCHEMA_VERSIONS
    from abicheck.storage.package_publish import REF_SCHEMA_VERSION
    from abicheck.storage.snapshot_schema_versions import SCHEMA_VERSION
    from abicheck.storage.versioning import PACKAGE_FORMAT_VERSION
    from abicheck.workflows.bundle_variants_capture import (
        BUNDLE_VARIANT_SPEC_SCHEMA_VERSION,
    )

    every: dict[str, Any] = {
        "snapshot_schema_version": SCHEMA_VERSION,
        "section_schema_versions": dict(sorted(SECTION_SCHEMA_VERSIONS.items())),
        "bundle_facts_schema_version": BUNDLE_FACTS_SCHEMA_VERSION,
        "bundle_archive_schema_version": BUNDLE_ARCHIVE_SCHEMA_VERSION,
        "package_format_version": PACKAGE_FORMAT_VERSION,
        "ref_schema_version": REF_SCHEMA_VERSION,
        "bundle_variant_spec_schema_version": BUNDLE_VARIANT_SPEC_SCHEMA_VERSION,
    }
    return {key: every[key] for key in VERSION_KEYS[sample]}


def entries_of(manifest: Mapping[str, Any], sample: str) -> list[tuple[str, dict]]:
    """*sample*'s frozen entries, oldest first (``<sample>/<n>``)."""
    found = [
        (key, entry)
        for key, entry in manifest["entries"].items()
        if entry["sample"] == sample
    ]
    return sorted(found, key=lambda item: int(item[0].rsplit("/", 1)[1]))
