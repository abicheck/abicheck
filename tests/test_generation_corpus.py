"""Frozen extractor-generation corpus (bug class
``storage.cross_generation_spelling_drift``, plan H1 "Generalized testing").

``tests/fixtures/generations/gen<N>/corpus.json.gz`` is the corpus library
(``corpus/corpus.{h,cpp}``: undeduced ``auto`` returns, class templates with
defaults, typedefs and aliases, namespaces, scoped and unscoped enums,
fields, parameters, variables) dumped by the last main commit of extractor
generation N (``scripts/freeze_generation_corpus.py``; commits and tool
versions in ``manifest.json``). The frozen documents are kept exactly as
those builds wrote them -- none stamps ``fact_generations``, which is how a
real pre-stamp baseline arrives.

Oracles, none of which is the diff engine's own helper:

* **identical source, nothing to report** -- comparing a frozen dump with a
  fresh one of the same source has no ``breaking``/``api_break`` finding and
  carries a generation-drift notice;
* **every respelling is declared** -- the per-entity type spellings of a
  frozen dump and a fresh one differ only where applying the
  ``SPELLING_MIGRATIONS`` rewrites the frozen generation predates (applied
  here by a plain whole-word substitution, not the loader's migration code)
  turns the old spelling into the new one.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from abicheck.checker import compare
from abicheck.checker_policy import API_BREAK_KINDS, BREAKING_KINDS
from abicheck.serialization import snapshot_from_dict
from abicheck.storage.generation_migrations import SPELLING_MIGRATIONS
from abicheck.storage.versioning import (
    EXTRACTOR_GENERATION,
    GENERATION_DRIFT_NOTICE_MARKER,
    RESOLVER_GENERATION,
)

CORPUS_ROOT = Path(__file__).parent / "fixtures" / "generations"
#: The checkout under test. The subprocesses below run from a temp or fixture
#: directory, where ``python -m abicheck`` would otherwise import whatever
#: ``abicheck`` is installed, not this tree (so a generation bump would compare
#: the installed generation against itself).
_REPO_ROOT = Path(__file__).resolve().parents[1]
_ABICHECK_ENV = {
    **os.environ,
    "PYTHONPATH": str(_REPO_ROOT),
}
SOURCES = CORPUS_ROOT / "corpus"
MANIFEST: dict[str, Any] = json.loads(
    (CORPUS_ROOT / "manifest.json").read_text(encoding="utf-8")
)
FROZEN = sorted(int(g) for g in MANIFEST["entries"])
_GATING = {"BREAKING", "API_BREAK"}


def _frozen_document(generation: int) -> dict[str, Any]:
    path = CORPUS_ROOT / MANIFEST["entries"][str(generation)]["snapshot"]
    return json.loads(gzip.decompress(path.read_bytes()))


def _stamped_current(document: dict[str, Any]) -> dict[str, Any]:
    """*document* stamped with this build's generations, so the loader applies
    no migration and its stored spellings are read back verbatim."""
    doc = json.loads(json.dumps(document))
    stamp = {
        "extractor": EXTRACTOR_GENERATION,
        "resolver": RESOLVER_GENERATION,
        "source": 0,
    }
    if "sections" in doc:
        doc["sections"]["provenance"]["payload"]["fact_generations"] = stamp
    else:
        doc["fact_generations"] = stamp
    return doc


def _spellings(document: dict[str, Any]) -> dict[tuple[str, str], str | None]:
    """Every stored type spelling, keyed by the entity and the position."""
    declarations = snapshot_from_dict(_stamped_current(document)).declarations
    out: dict[tuple[str, str], str | None] = {}
    for f in declarations.functions:
        key = f.mangled or f.name
        out[("return", key)] = f.return_type
        for index, param in enumerate(f.params):
            out[(f"param{index}", key)] = param.type
    for v in declarations.variables:
        out[("variable", v.mangled or v.name)] = v.type
    for t in declarations.types:
        out[("type", t.name)] = t.name
        for field in t.fields:
            out[("field", f"{t.name}.{field.name}")] = field.type
    for alias, target in declarations.typedefs.items():
        out[("typedef", alias)] = target
    for e in declarations.enums:
        out[("enum", e.name)] = e.name
    return out


def _declared_rewrite(spelling: str, generation: int) -> str:
    """*spelling* after every rewrite a generation above *generation*
    declares -- an independent whole-word substitution -- and, below
    generation 15, castxml's last-index array count read as an element count
    (the corpus is castxml-dumped; counts inside a template argument list are
    castxml's own, already correct, spelling)."""
    for introduced in sorted(SPELLING_MIGRATIONS):
        if introduced <= generation:
            continue
        for old, new in SPELLING_MIGRATIONS[introduced].rewrites:
            spelling = re.sub(rf"\b{re.escape(old)}\b", new, spelling)
    if generation < 15:
        head, sep, tail = spelling.rpartition(">")
        tail = re.sub(r"\[(\d+)\]", lambda m: f"[{int(m.group(1)) + 1}]", tail)
        spelling = head + sep + tail
    return spelling


def _declared_extent_shift(old: str, new: str, generation: int) -> bool:
    """Whether *old* -> *new* is the array-extent growth (``[N]`` ->
    ``[N + shift]``) a generation above *generation* declared."""
    shift = sum(
        change.extent_shift
        for introduced, change in SPELLING_MIGRATIONS.items()
        if introduced > generation
    )
    if not shift:
        return False
    old_parts, new_parts = re.split(r"\[(\d+)\]", old), re.split(r"\[(\d+)\]", new)
    if len(old_parts) != len(new_parts):
        return False
    return all(
        (a == b) if i % 2 == 0 else int(b) - int(a) in (0, shift)
        for i, (a, b) in enumerate(zip(old_parts, new_parts, strict=True))
    )


def _undeclared_respellings(
    old: dict[str, Any], new: dict[str, Any], generation: int
) -> list[str]:
    old_s, new_s = _spellings(old), _spellings(new)
    assert set(old_s) == set(new_s), "the entity sets must match first"
    return [
        f"{key}: {old_s[key]!r} -> {new_s[key]!r}"
        for key in sorted(old_s, key=str)
        if old_s[key] != new_s[key]
        and (
            old_s[key] is None
            or (
                _declared_rewrite(old_s[key], generation) != new_s[key]
                and not _declared_extent_shift(
                    _declared_rewrite(old_s[key], generation), new_s[key], generation
                )
            )
        )
    ]


def _gating(result: Any) -> list[str]:
    """Findings whose effective verdict is ``breaking`` or ``api_break``."""
    gating_kinds = BREAKING_KINDS | API_BREAK_KINDS
    return [
        f"{c.kind.value}: {c.symbol}"
        for c in result.changes
        if (
            c.effective_verdict.value in _GATING
            if c.effective_verdict is not None
            else c.kind in gating_kinds
        )
    ] + (
        []
        if result.verdict.value not in _GATING
        else [f"verdict {result.verdict.value}"]
    )


# ── corpus bookkeeping (fast) ────────────────────────────────────────────────


def test_frozen_snapshots_match_the_committed_sources() -> None:
    """A frozen dump is only evidence for the sources it was dumped from;
    editing ``corpus/`` must come with a refreeze."""
    digest = hashlib.sha256()
    for path in sorted(SOURCES.iterdir()):
        digest.update(path.name.encode() + b"\0" + path.read_bytes() + b"\0")
    assert digest.hexdigest() == MANIFEST["sources_sha256"]


def test_every_retired_generation_is_frozen_or_explained() -> None:
    covered = set(FROZEN) | {int(g) for g in MANIFEST["skipped"]}
    assert covered == set(range(1, EXTRACTOR_GENERATION))
    for reason in MANIFEST["skipped"].values():
        assert len(reason.split()) >= 5, reason
    for generation in FROZEN:
        assert (CORPUS_ROOT / f"gen{generation}" / "corpus.json.gz").is_file()


def test_corpus_exercises_the_spelling_that_drifted() -> None:
    """The corpus must contain the shape the field report escaped through,
    or the oracles below would pass vacuously for generations below 6."""
    old = _spellings(_frozen_document(min(FROZEN)))
    assert "AutoType&" in old.values()
    assert any("<" in s for s in old.values() if s)  # template spellings
    assert any(k[0] == "typedef" for k in old)
    assert any(k[0] == "enum" for k in old)


@pytest.mark.parametrize("generation", FROZEN)
def test_frozen_dump_compares_clean_against_newest_frozen(generation: int) -> None:
    """Fast stand-in for the fresh-dump oracle below: every frozen generation
    against the newest frozen one, both read as real pre-stamp baselines."""
    newest = max(FROZEN)
    old = snapshot_from_dict(_frozen_document(generation))
    new = snapshot_from_dict(_frozen_document(newest))
    result = compare(old, new)
    assert _gating(result) == []
    assert (
        _undeclared_respellings(
            _frozen_document(generation), _frozen_document(newest), generation
        )
        == []
    )


# ── against a fresh dump (integration: g++ + castxml) ────────────────────────


def _tool_banner(tool: str) -> str:
    """First line of ``tool --version`` (castxml prints it on stderr)."""
    proc = subprocess.run([tool, "--version"], capture_output=True, text=True)
    lines = (proc.stdout + proc.stderr).strip().splitlines()
    return lines[0].strip() if lines else ""


def _toolchain_mismatch() -> str:
    """Why this host cannot reproduce the frozen generations' compile profile.

    The invariant is extractor-generation drift; a different compiler or
    castxml changes the snapshot's compile profile (``compiler_version``,
    ``language_standard``) and ``compare`` rightly refuses that as a profile
    mismatch. So the fresh-dump checks run only on the manifest's toolchain
    (CI: the ubuntu-24.04 integration leg) and say what differs elsewhere.
    """
    host = {"compiler": _tool_banner("g++"), "castxml": _tool_banner("castxml")}
    entries = [MANIFEST["entries"][str(g)] for g in FROZEN]
    diffs = []
    for key, value in host.items():
        frozen = sorted({e[key] for e in entries})
        if frozen != [value]:
            diffs.append(f"{key} {value!r} vs frozen {frozen}")
    return "; ".join(diffs)


@pytest.fixture(scope="module")
def fresh_dump(tmp_path_factory: pytest.TempPathFactory) -> Path:
    if shutil.which("g++") is None or shutil.which("castxml") is None:
        pytest.skip("needs g++ and castxml")
    mismatch = _toolchain_mismatch()
    if mismatch:
        pytest.skip(f"host toolchain differs from the frozen corpus: {mismatch}")
    work = tmp_path_factory.mktemp("generation_corpus")
    lib = work / "libcorpus.so"
    cmd = [
        "g++",
        "-std=c++17",
        "-shared",
        "-fPIC",
        "-O0",
        "-o",
        str(lib),
        str(SOURCES / "corpus.cpp"),
    ]
    built = subprocess.run(cmd, capture_output=True, text=True)
    if built.returncode != 0:
        pytest.fail(f"{' '.join(cmd)} failed:\n{built.stderr}")
    out = work / "fresh.json"
    dumped = subprocess.run(
        [
            sys.executable,
            "-m",
            "abicheck.cli",
            "dump",
            str(lib),
            "-H",
            str(SOURCES / "corpus.h"),
            "--version",
            "1",
            "-o",
            str(out),
        ],
        capture_output=True,
        text=True,
        cwd=work,
        env=_ABICHECK_ENV,
    )
    if dumped.returncode != 0:
        pytest.fail(f"abicheck dump failed:\n{dumped.stderr}")
    return out


@pytest.mark.integration
@pytest.mark.parametrize("generation", FROZEN)
def test_frozen_generation_compares_clean_against_fresh_dump(
    generation: int, fresh_dump: Path
) -> None:
    old = snapshot_from_dict(_frozen_document(generation))
    new = snapshot_from_dict(json.loads(fresh_dump.read_text(encoding="utf-8")))
    result = compare(old, new)
    assert _gating(result) == []
    assert any(GENERATION_DRIFT_NOTICE_MARKER in w for w in result.coverage_warnings)


@pytest.mark.integration
@pytest.mark.parametrize("generation", FROZEN)
def test_every_respelling_against_fresh_dump_is_a_declared_migration(
    generation: int, fresh_dump: Path
) -> None:
    """The detector for the next ``AutoType``: a fresh dump that spells any
    entity differently from an older generation, without a
    ``SPELLING_MIGRATIONS`` rule mapping one to the other, fails here."""
    fresh = json.loads(fresh_dump.read_text(encoding="utf-8"))
    assert (
        _undeclared_respellings(_frozen_document(generation), fresh, generation) == []
    )


@pytest.mark.integration
def test_require_same_generation_refuses_a_frozen_baseline(fresh_dump: Path) -> None:
    baseline = CORPUS_ROOT / MANIFEST["entries"][str(max(FROZEN))]["snapshot"]
    refused = subprocess.run(
        [
            sys.executable,
            "-m",
            "abicheck.cli",
            "compare",
            str(baseline),
            str(fresh_dump),
            "--require-same-generation",
        ],
        capture_output=True,
        text=True,
        env=_ABICHECK_ENV,
    )
    assert refused.returncode == 64, refused.stderr
    assert "re-dump baseline" in refused.stderr
    same = subprocess.run(
        [
            sys.executable,
            "-m",
            "abicheck.cli",
            "compare",
            str(fresh_dump),
            str(fresh_dump),
            "--require-same-generation",
        ],
        capture_output=True,
        text=True,
        env=_ABICHECK_ENV,
    )
    assert same.returncode == 0, same.stderr
