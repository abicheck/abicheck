#!/usr/bin/env python3
"""Freeze the extractor-generation corpus at one historical commit.

Bug class ``storage.cross_generation_spelling_drift``: every generation bump
must leave a snapshot of ``tests/fixtures/generations/corpus/`` dumped by the
*previous* generation, so ``tests/test_generation_corpus.py`` can prove that
a stored older dump and a fresh one of the same source still compare clean,
and that every spelling that differs is covered by a ``SPELLING_MIGRATIONS``
row. ``check_ai_readiness.py``'s ``generation-corpus`` check fails a bump
that leaves no entry (or recorded skip) for the generation it retires.

``--ref REF``
    Export ``abicheck/`` at REF (``git archive``, no worktree), build the
    corpus library with ``g++``, dump it with that code, and write
    ``gen<N>/corpus.json.gz`` plus its ``manifest.json`` entry. N is the
    ``EXTRACTOR_GENERATION`` REF declares. Run it *before* raising the
    constant, with ``--ref HEAD``.

``--skip N --reason TEXT``
    Record that generation N has no frozen snapshot, and why (it never
    shipped on main, or its code cannot run any more).

Needs castxml and g++ on PATH.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CORPUS_ROOT = REPO / "tests" / "fixtures" / "generations"
MANIFEST = CORPUS_ROOT / "manifest.json"
SOURCES = CORPUS_ROOT / "corpus"
_GENERATION = re.compile(r"^EXTRACTOR_GENERATION\s*=\s*(\d+)", re.MULTILINE)


def sources_digest() -> str:
    """SHA-256 over the corpus sources; a frozen snapshot is only valid for
    the sources it was dumped from."""
    digest = hashlib.sha256()
    for path in sorted(SOURCES.iterdir()):
        digest.update(path.name.encode() + b"\0" + path.read_bytes() + b"\0")
    return digest.hexdigest()


def load_manifest() -> dict:
    if MANIFEST.exists():
        return json.loads(MANIFEST.read_text(encoding="utf-8"))
    return {"entries": {}, "skipped": {}}


def write_manifest(manifest: dict) -> None:
    MANIFEST.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def _tool_version(argv: list[str]) -> str:
    out = subprocess.run(argv, capture_output=True, text=True, check=True).stdout
    return out.splitlines()[0].strip()


def freeze(ref: str) -> int:
    sha = subprocess.run(
        ["git", "rev-parse", "--short=12", ref],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    with tempfile.TemporaryDirectory() as tmp_name:
        tmp = Path(tmp_name)
        tree = tmp / "tree"
        tree.mkdir()
        archive = subprocess.run(
            ["git", "archive", sha, "abicheck"],
            cwd=REPO,
            capture_output=True,
            check=True,
        ).stdout
        subprocess.run(["tar", "-x", "-C", str(tree)], input=archive, check=True)
        text = (tree / "abicheck/storage/versioning.py").read_text(encoding="utf-8")
        match = _GENERATION.search(text)
        if match is None:
            print(f"{sha} declares no EXTRACTOR_GENERATION", file=sys.stderr)
            return 2
        generation = int(match.group(1))
        lib = tmp / "libcorpus.so"
        subprocess.run(
            [
                "g++",
                "-std=c++17",
                "-shared",
                "-fPIC",
                "-O0",
                "-o",
                str(lib),
                str(SOURCES / "corpus.cpp"),
            ],
            check=True,
        )
        out = tmp / "snapshot.json"
        archived_cli = [
            sys.executable,
            "-I",
            "-c",
            "import sys; sys.path.insert(0, sys.argv[1]); "
            "from abicheck.cli import main; sys.argv = ['abicheck'] + sys.argv[2:]; "
            "main()",
            str(tree),
        ]
        # The archived revision may predate --label (it then only has --version),
        # so ask that revision's own CLI which spelling it accepts.
        help_text = subprocess.run(
            [*archived_cli, "dump", "--help"],
            cwd=tmp,
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        label_flag = "--label" if "--label" in help_text else "--version"
        subprocess.run(
            [
                *archived_cli,
                "dump",
                str(lib),
                "-H",
                str(SOURCES / "corpus.h"),
                label_flag,
                "1",
                "-o",
                str(out),
            ],
            cwd=tmp,
            check=True,
        )
        document = json.loads(out.read_text(encoding="utf-8"))
        target = CORPUS_ROOT / f"gen{generation}" / "corpus.json.gz"
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(document, separators=(",", ":"), sort_keys=True)
        target.write_bytes(gzip.compress(payload.encode(), 9, mtime=0))
    manifest = load_manifest()
    manifest["entries"][str(generation)] = {
        "commit": sha,
        "castxml": _tool_version(["castxml", "--version"]),
        "compiler": _tool_version(["g++", "--version"]),
        "snapshot": f"gen{generation}/corpus.json.gz",
    }
    manifest["skipped"].pop(str(generation), None)
    manifest["sources_sha256"] = sources_digest()
    write_manifest(manifest)
    print(f"froze generation {generation} at {sha} -> {target.relative_to(REPO)}")
    return 0


def skip(generation: int, reason: str) -> int:
    if not reason.strip():
        print("--reason must not be empty", file=sys.stderr)
        return 2
    manifest = load_manifest()
    manifest["skipped"][str(generation)] = reason.strip()
    write_manifest(manifest)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--ref", help="commit whose extractor dumps the corpus")
    group.add_argument("--skip", type=int, metavar="N", help="generation to skip")
    parser.add_argument("--reason", default="", help="why --skip N has no snapshot")
    args = parser.parse_args(argv)
    if args.ref is not None:
        if shutil.which("castxml") is None or shutil.which("g++") is None:
            print("castxml and g++ are required", file=sys.stderr)
            return 2
        return freeze(args.ref)
    return skip(args.skip, args.reason)


if __name__ == "__main__":
    sys.exit(main())
