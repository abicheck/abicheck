"""Frozen storage-v2 container corpus (plan ``storage-format-v2.md`` A1.9,
ADR-062 D14-D18).

``tests/fixtures/containers/`` holds one frozen sample of every container a
stored operand arrives as: the G38 ``BundleFacts`` document, the G40 archive,
the directory ``ProjectSnapshot`` package, and a single-file snapshot from
each of the clang header frontend, a Mach-O dylib and a PE DLL
(``tests/_container_corpus.py`` says how each is produced;
``scripts/freeze_container_corpus.py`` freezes them). The generation corpus
(``test_generation_corpus.py``) covers the single-file castxml/ELF shape.

Each frozen entry is held to the generation corpus's three invariants:

* **it loads with no unexpected notice** -- a load notice is allowed only for
  the generation drift the entry's own producer generations predict;
* **it compares clean against a fresh production** -- the same committed
  sources, produced again by this build through the same CLI, compare with
  no ``breaking``/``api_break`` outcome; the Mach-O and PE samples need no
  toolchain, so this runs in the fast lane for them;
* **any byte or respelling drift is a declared migration** -- decoding the
  frozen container and encoding it again with this build reproduces its
  bytes exactly, unless an encoder version moved since the freeze (each moved
  section version with a registered D13 migration from the frozen one), and a
  fresh production spells every entity the frozen one spells, up to the
  ``SPELLING_MIGRATIONS`` rewrites of the generations in between.

The newest entry of every sample must also be written under the encoder
versions this build declares: a bump without a refreeze fails here and in the
``container-corpus`` gate.
"""

from __future__ import annotations

import gzip
import json
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from abicheck.serialization import snapshot_from_dict, snapshot_to_json
from abicheck.storage.bundle_facts_codec import load_bundle_facts, save_bundle_facts
from abicheck.storage.dto import migration_source_versions
from abicheck.storage.generation_migrations import SPELLING_MIGRATIONS
from abicheck.storage.versioning import (
    EXTRACTOR_GENERATION,
    GENERATION_DRIFT_NOTICE_MARKER,
    RESOLVER_GENERATION,
)
from tests._abicheck_subprocess import abicheck_command
from tests._container_corpus import (
    CORPUS_ROOT,
    SAMPLES,
    VARIANT,
    abicheck_env,
    encoder_versions,
    entries_of,
    file_digest,
    load_manifest,
    missing_tools,
    produce,
    read_json_document,
    sources_sha256,
    toolchain,
)
from tests.test_generation_corpus import _undeclared_respellings

MANIFEST: dict[str, Any] = load_manifest()
ENTRIES = sorted(MANIFEST["entries"])
_GATING = {"BREAKING", "API_BREAK"}


def _entry(key: str) -> dict[str, Any]:
    return MANIFEST["entries"][key]


def _frozen_path(key: str) -> Path:
    return CORPUS_ROOT / _entry(key)["path"]


def _drift_expected(key: str) -> bool:
    generations = _entry(key)["generations"]
    return (generations["extractor"], generations["resolver"]) != (
        EXTRACTOR_GENERATION,
        RESOLVER_GENERATION,
    )


# ── loading each container the way compare does ─────────────────────────────


def _package_members(path: Path, work: Path) -> Iterator[tuple[str, Any, dict]]:
    """``(library, snapshot, stored document)`` per member of the package's
    one variant, through the stored-release path ``compare`` takes."""
    from abicheck.project_snapshot_legacy import (
        materialize_release_variant_artifacts,
        read_stored_snapshot_document,
    )
    from abicheck.workflows.project_package_input import resolve_project_package

    members = materialize_release_variant_artifacts(
        path, variant_id=None, dest_root=work / "members"
    )
    for _artifact_id, (sub, _ref) in sorted(members.items()):
        snapshot = resolve_project_package(sub)
        document, _artifact = read_stored_snapshot_document(sub)
        yield snapshot.library, snapshot, document


def _members(sample: str, path: Path, work: Path) -> dict[str, tuple[Any, dict]]:
    """Every library *path* stores, as ``{library: (snapshot, document)}``."""
    if sample == "package":
        return {lib: (snap, doc) for lib, snap, doc in _package_members(path, work)}
    if sample in ("g38_document", "g40_archive"):
        facts = load_bundle_facts(path)
        documents = _bundle_member_documents(sample, path)
        return {
            lib: (snap, documents[lib])
            for lib, snap in facts.per_library_snapshots.items()
        }
    document = read_json_document(path)
    snapshot = snapshot_from_dict(document)
    return {snapshot.library: (snapshot, document)}


def _bundle_member_documents(sample: str, path: Path) -> dict[str, dict]:
    """The member documents exactly as the container stores them."""
    if sample == "g38_document":
        return dict(read_json_document(path)["per_library_snapshots"])
    from abicheck.storage.bundle_archive import BundleArchiveReader

    out = {}
    with BundleArchiveReader.open(path) as reader:
        for library, digest in reader.read_manifest()["library_blobs"].items():
            out[library] = json.loads(reader.read_blob(digest))
    return out


def _load_notices(sample: str, path: Path, work: Path) -> list[str]:
    notices = [
        notice
        for snap, _doc in _members(sample, path, work).values()
        for notice in snap.load_notices
    ]
    if sample == "package":
        from abicheck.project_snapshot_store import read_manifest_summary

        summary_notice = read_manifest_summary(path).semantics_notice
        if summary_notice:
            notices.append(summary_notice)
    return notices


# ── bookkeeping (fast) ──────────────────────────────────────────────────────


def test_frozen_entries_match_the_committed_sources() -> None:
    """A frozen sample is evidence only for the sources it was produced from;
    editing ``sources/`` (or the shared C++ corpus) must come with a refreeze."""
    assert sources_sha256() == MANIFEST["sources_sha256"]


def test_every_sample_is_frozen() -> None:
    assert {_entry(key)["sample"] for key in ENTRIES} == set(SAMPLES)


@pytest.mark.parametrize("key", ENTRIES)
def test_frozen_bytes_are_the_recorded_ones(key: str) -> None:
    assert file_digest(_frozen_path(key)) == _entry(key)["sha256"]


@pytest.mark.parametrize("sample", sorted(SAMPLES))
def test_newest_entry_is_written_under_this_builds_encoder_versions(
    sample: str,
) -> None:
    """A bumped encoder version owes a refreeze (older entries are kept):
    ``python scripts/freeze_container_corpus.py --sample <sample>``."""
    _key, newest = entries_of(MANIFEST, sample)[-1]
    assert newest["versions"] == encoder_versions(sample)


def test_the_corpus_exercises_each_containers_distinguishing_shape() -> None:
    """Non-vacuity: each sample carries the shape it is frozen for."""
    g38 = read_json_document(_frozen_path(entries_of(MANIFEST, "g38_document")[-1][0]))
    assert g38["public_surface"] is not None  # two members: a release surface
    assert len(g38["per_library_snapshots"]) == 2
    package = _frozen_path(entries_of(MANIFEST, "package")[-1][0])
    stored = json.loads((package / "manifest.json").read_text(encoding="utf-8"))
    assert len(stored["artifact_refs"]) == 2
    assert stored["versions"]["extractor_generation"] > 0
    clang = json.dumps(
        read_json_document(_frozen_path(entries_of(MANIFEST, "clang_snapshot")[-1][0]))
    )
    assert '"frontend": "clang"' in clang
    for sample, block in (("macho_snapshot", "macho"), ("pe_snapshot", "pe")):
        document = read_json_document(_frozen_path(entries_of(MANIFEST, sample)[-1][0]))
        assert document["sections"]["binary"]["payload"].get(block), sample


# ── it loads with no unexpected notice (fast) ───────────────────────────────


@pytest.mark.parametrize("key", ENTRIES)
def test_frozen_container_loads_with_no_unexpected_notice(
    key: str, tmp_path: Path
) -> None:
    notices = _load_notices(_entry(key)["sample"], _frozen_path(key), tmp_path)
    unexpected = [n for n in notices if GENERATION_DRIFT_NOTICE_MARKER not in n]
    assert unexpected == []
    drift = [n for n in notices if GENERATION_DRIFT_NOTICE_MARKER in n]
    assert bool(drift) == _drift_expected(key), notices


# ── byte drift is a declared migration (fast) ───────────────────────────────


def _spelling_migration_declared(key: str) -> bool:
    """Whether loading *key* under this build may rewrite stored spellings
    or facts: a generation after the frozen one declares a rewrite, an extent
    shift or a fact migration (or the frozen one predates castxml's
    array-count fix, generation 15)."""
    frozen = _entry(key)["generations"]["extractor"]
    return frozen < 15 or any(
        change.rewrites or change.extent_shift or change.migrates_facts
        for introduced, change in SPELLING_MIGRATIONS.items()
        if frozen < introduced <= EXTRACTOR_GENERATION
    )


def _as_stored(snapshot: Any, key: str) -> Any:
    """*snapshot* as its frozen document stated it. A load under a newer
    extractor generation marks the spellings migrated (the stamp's
    ``spellings`` key) even when no generation in between declared a
    rewrite; with none declared that mark is the only legitimate difference,
    so it is undone and every other byte must survive the re-encode."""
    if not _spelling_migration_declared(key):
        snapshot.spellings_generation = None
    return snapshot


def _reencode(key: str, work: Path) -> bytes:
    """*key*'s frozen container decoded and encoded again by this build, as
    the bytes the frozen file is compared with (the JSON text, not its gzip
    envelope)."""
    sample, path = _entry(key)["sample"], _frozen_path(key)
    out = work / "reencoded"
    out.mkdir(parents=True, exist_ok=True)
    if sample in ("g38_document", "g40_archive"):
        facts = load_bundle_facts(path)
        for snap in facts.per_library_snapshots.values():
            _as_stored(snap, key)
        if sample == "g38_document":
            save_bundle_facts(facts, out / "x.json")
            return (out / "x.json").read_bytes()
        save_bundle_facts(facts, out / "x.zip", format="archive")
        return (out / "x.zip").read_bytes()
    if sample == "package":
        return _tree_bytes(_repackage(key, work))
    snapshot = _as_stored(snapshot_from_dict(read_json_document(path)), key)
    return snapshot_to_json(snapshot).encode()


def _frozen_bytes(sample: str, path: Path) -> bytes:
    if sample == "package":
        return _tree_bytes(path)
    raw = path.read_bytes()
    return gzip.decompress(raw) if path.suffix == ".gz" else raw


def _tree_bytes(root: Path) -> bytes:
    return b"".join(
        p.relative_to(root).as_posix().encode() + b"\0" + p.read_bytes() + b"\0"
        for p in sorted(root.rglob("*"))
        if p.is_file()
    )


def _repackage(key: str, work: Path) -> Path:
    """The package's members captured again by this build's
    ``capture_variants``, each library dumped as its stored snapshot."""
    from abicheck.model.bundle_variants import parse_bundle_variants
    from abicheck.workflows.bundle_variants_capture import (
        VariantCaptureInput,
        capture_variants,
        plan_variant_capture,
    )
    from tests._container_corpus import VARIANT_CONFIG
    from tests._yaml_fast import safe_load

    snapshots = {
        lib: _as_stored(snap, key)
        for lib, snap, _doc in _package_members(_frozen_path(key), work)
    }
    inputs = work / "inputs"
    inputs.mkdir()
    for library in snapshots:
        (inputs / library).write_bytes(b"")
    config = parse_bundle_variants(safe_load(VARIANT_CONFIG)["bundle_variants"])
    plan = plan_variant_capture(config, [VariantCaptureInput(VARIANT, inputs)])
    out = work / "repackaged"
    capture_variants(plan, out, dump=lambda p, _item: snapshots[p.name])
    return out


def _moved_versions(key: str) -> list[str]:
    """The encoder versions that moved since *key* was frozen, each with
    whether its move is declared; raises for an undeclared section move."""
    recorded = _entry(key)["versions"]
    current = encoder_versions(_entry(key)["sample"])
    moved = []
    for name, value in current.items():
        if name == "section_schema_versions":
            for kind, version in value.items():
                old = recorded[name].get(kind)
                if old is None or old == version:
                    continue
                steps = migration_source_versions(kind)
                assert old < version and all(v in steps for v in range(old, version)), (
                    f"section {kind!r} moved {old} -> {version} with no "
                    "registered D13 migration chain from the frozen version"
                )
                moved.append(f"{kind} {old}->{version}")
        elif recorded.get(name) != value:
            moved.append(f"{name} {recorded.get(name)}->{value}")
    return moved


@pytest.mark.parametrize("key", ENTRIES)
def test_reencoding_a_frozen_container_is_identity_or_a_declared_migration(
    key: str, tmp_path: Path
) -> None:
    """The fast encoder-drift detector: a runtime refactor that moves any
    container's bytes without a version bump fails here."""
    sample = _entry(key)["sample"]
    path = _frozen_path(key)
    moved = _moved_versions(key)
    if _reencode(key, tmp_path) == _frozen_bytes(sample, path):
        return
    # Only a moved encoder version, or a declared spelling migration (its
    # respellings are held to the fresh-dump oracle below), may move bytes.
    # Generation drift alone may not: _as_stored undid its only mark.
    assert moved or _spelling_migration_declared(key), (
        f"{key}: this build encodes the frozen container to different bytes "
        "with no encoder version moved and no spelling migration declared "
        "since it was frozen -- bump the version that moved (with its "
        "migration) and refreeze"
    )


# ── it compares clean against a fresh production ────────────────────────────


def _fresh(sample: str, work: Path) -> Path:
    missing = missing_tools(SAMPLES[sample])
    if missing:
        pytest.skip(f"needs {', '.join(missing)}")
    key, newest = entries_of(MANIFEST, sample)[-1]
    host = toolchain(SAMPLES[sample])
    if host != newest["toolchain"]:
        # A different compiler/castxml/clang changes the compile profile the
        # snapshot records, which compare rightly refuses (see
        # test_generation_corpus._toolchain_mismatch); CI's integration leg
        # runs the frozen toolchain.
        pytest.skip(f"host toolchain {host} differs from {key}'s {newest['toolchain']}")
    return produce(SAMPLES[sample], work)


def _compare_report(old: Path, new: Path, work: Path) -> tuple[int, dict]:
    report = work / "report.json"
    proc = subprocess.run(
        abicheck_command("compare", old, new, "-o", f"json={report}"),
        capture_output=True,
        text=True,
        cwd=work,
        env=abicheck_env(work),
    )
    assert report.is_file(), proc.stderr
    return proc.returncode, json.loads(report.read_text(encoding="utf-8"))


def _assert_clean_against_fresh(key: str, fresh: Path, work: Path) -> None:
    sample = _entry(key)["sample"]
    frozen = _frozen_path(key)
    code, report = _compare_report(frozen, fresh, work)
    # A frozen and a fresh dump of a sample without debug info are two inputs
    # with no type-level evidence: their clean claim reads UNCONFIRMED
    # (ADR-090), exit 1 through that axis alone. The oracle is the observed
    # verdict -- regeneration must still fabricate no gating finding.
    observed = (report.get("unconfirmed") or {}).get(
        "observed_verdict", report["verdict"]
    )
    assert observed not in _GATING, observed
    if report["verdict"] == "UNCONFIRMED":
        assert report["exit"]["reasons"] == ["unconfirmed_compatibility"], report[
            "exit"
        ]
        assert code == 1
    else:
        assert code == 0, report.get("exit")
    old = _members(sample, frozen, work / "old")
    new = _members(sample, fresh, work / "new")
    assert sorted(old) == sorted(new)
    generation = _entry(key)["generations"]["extractor"]
    for library, (_snap, document) in old.items():
        assert _undeclared_respellings(document, new[library][1], generation) == [], (
            library
        )


@pytest.mark.parametrize(
    "key", [k for k in ENTRIES if not SAMPLES[_entry(k)["sample"]].tools]
)
def test_toolchain_free_sample_compares_clean_against_a_fresh_dump(
    key: str, tmp_path: Path
) -> None:
    """Mach-O and PE: produced from this checkout alone, so every lane runs
    the fresh-production oracle for them."""
    fresh = _fresh(_entry(key)["sample"], tmp_path / "fresh-work")
    _assert_clean_against_fresh(key, fresh, tmp_path)


@pytest.mark.integration
@pytest.mark.parametrize(
    "key", [k for k in ENTRIES if SAMPLES[_entry(k)["sample"]].tools]
)
def test_toolchain_sample_compares_clean_against_a_fresh_production(
    key: str, tmp_path: Path
) -> None:
    fresh = _fresh(_entry(key)["sample"], tmp_path / "fresh-work")
    _assert_clean_against_fresh(key, fresh, tmp_path)
