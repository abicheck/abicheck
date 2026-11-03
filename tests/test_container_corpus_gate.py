"""``check_ai_readiness.py``'s ``container-corpus`` gate
(``scripts/container_corpus_gate.py``): bumping a container or snapshot
encoder version requires a frozen sample written under the new version, and a
frozen sample may not be edited in place."""

from __future__ import annotations

import copy
import importlib.util
import json
import shutil
import sys
from pathlib import Path

import pytest

from tests._container_corpus import SAMPLES, VERSION_KEYS, encoder_versions

_PATH = Path(__file__).resolve().parents[1] / "scripts" / "container_corpus_gate.py"
_SPEC = importlib.util.spec_from_file_location("container_corpus_gate", _PATH)
assert _SPEC is not None and _SPEC.loader is not None
gate = importlib.util.module_from_spec(_SPEC)
sys.modules["container_corpus_gate"] = gate
_SPEC.loader.exec_module(gate)

_MANIFEST = json.loads((gate.CORPUS / "manifest.json").read_text(encoding="utf-8"))
_DECLARED = gate.declared_versions()


def test_the_gate_and_the_producers_name_the_same_samples() -> None:
    assert set(gate.SAMPLES) == set(SAMPLES)


@pytest.mark.parametrize("sample", sorted(SAMPLES))
def test_the_regex_reads_the_versions_the_package_declares(sample: str) -> None:
    """Oracle: the imported constants. A renamed or reformatted declaration
    the regex no longer matches must not read as some other number."""
    imported = encoder_versions(sample)
    for key in VERSION_KEYS[sample]:
        if key in gate.VERSION_SOURCES:
            assert _DECLARED[key] == imported[key], key


def test_repository_corpus_passes() -> None:
    assert gate.evaluate(_MANIFEST, gate.CORPUS, _DECLARED) == []


@pytest.mark.parametrize("key", sorted(gate.VERSION_SOURCES))
def test_any_scalar_bump_without_a_refreeze_fails(key: str) -> None:
    bumped = {**_DECLARED, key: _DECLARED[key] + 1}
    problems = gate.evaluate(_MANIFEST, gate.CORPUS, bumped)
    affected = sorted(s for s in SAMPLES if key in VERSION_KEYS[s])
    assert affected, key
    for sample in affected:
        assert any(sample in p and key in p for p in problems), (sample, problems)
    unaffected = set(SAMPLES) - set(affected)
    assert not any(f"frozen {s} " in p for s in unaffected for p in problems)


def test_a_refreeze_under_the_new_version_satisfies_the_gate(tmp_path: Path) -> None:
    """The newest entry decides; the older one stays and still must exist."""
    corpus = tmp_path / "containers"
    shutil.copytree(gate.CORPUS, corpus)
    manifest = copy.deepcopy(_MANIFEST)
    key = "bundle_archive_schema_version"
    bumped = {**_DECLARED, key: _DECLARED[key] + 1}
    old_key = "g40_archive/1"
    new_entry = copy.deepcopy(manifest["entries"][old_key])
    new_entry["path"] = "g40/2/release.zip"
    new_entry["versions"][key] = bumped[key]
    (corpus / "g40" / "2").mkdir()
    shutil.copyfile(corpus / "g40/1/release.zip", corpus / "g40/2/release.zip")
    manifest["entries"]["g40_archive/2"] = new_entry
    assert gate.evaluate(manifest, corpus, bumped) == []
    (corpus / "g40/1/release.zip").unlink()
    assert any("g40_archive/1" in p for p in gate.evaluate(manifest, corpus, bumped))


def test_numbering_is_numeric_not_lexicographic(tmp_path: Path) -> None:
    """Entry 10 is newer than entry 9: the old one's versions must not be
    the ones held to the declaration."""
    corpus = tmp_path / "containers"
    shutil.copytree(gate.CORPUS, corpus)
    manifest = copy.deepcopy(_MANIFEST)
    stale = copy.deepcopy(manifest["entries"]["pe_snapshot/1"])
    stale["versions"]["snapshot_schema_version"] -= 1
    manifest["entries"]["pe_snapshot/9"] = stale
    manifest["entries"]["pe_snapshot/10"] = manifest["entries"].pop("pe_snapshot/1")
    assert gate.evaluate(manifest, corpus, _DECLARED) == []


def test_an_edited_frozen_sample_fails(tmp_path: Path) -> None:
    corpus = tmp_path / "containers"
    shutil.copytree(gate.CORPUS, corpus)
    package = corpus / _MANIFEST["entries"]["package/1"]["path"]
    (package / "manifest.json").write_text("{}\n", encoding="utf-8")
    problems = gate.evaluate(_MANIFEST, corpus, _DECLARED)
    assert problems == [
        "frozen entry package/1 was modified after it was frozen (sha256 "
        "differs); refreeze it instead of editing it"
    ]


@pytest.mark.parametrize("sample", sorted(SAMPLES))
def test_a_sample_with_no_entry_fails(sample: str) -> None:
    manifest = copy.deepcopy(_MANIFEST)
    manifest["entries"] = {
        k: e for k, e in manifest["entries"].items() if e["sample"] != sample
    }
    problems = gate.evaluate(manifest, gate.CORPUS, _DECLARED)
    assert problems == [
        f"no frozen {sample} sample: run "
        f"`python scripts/freeze_container_corpus.py --sample {sample}`"
    ]


def test_missing_manifest_and_lost_declaration_fail() -> None:
    assert gate.evaluate(None, gate.CORPUS, _DECLARED) == [
        "containers/manifest.json is missing"
    ]
    lost = {**_DECLARED, "ref_schema_version": None}
    (problem,) = gate.evaluate(_MANIFEST, gate.CORPUS, lost)
    assert "no longer declares ref_schema_version" in problem
