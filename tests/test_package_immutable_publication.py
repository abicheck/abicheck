"""ADR-062 D17: refs are immutable and publication is atomic.

Bug class: a republish under ids that are already live overwrote
`refs/<kind>/<id>.json` before checking objects, so a failed or interrupted
publish left `manifest.json` naming the *old* package while its refs held
the *new* content -- the previous package became unreadable.

The invariant, checked against independent oracles (the manifest object
that was published, and a byte-level snapshot of every file on disk):
whatever happens to a republish -- a failed object check of any section
owner, an interruption at *any* write, or a successful change of content
under the same ids -- the previously published package reads back exactly,
and no file the live manifest names is ever rewritten.
"""

from __future__ import annotations

import json
import shutil
import threading
from pathlib import Path

import pytest

import abicheck.project_snapshot_store as store_module
from abicheck.project_snapshot_store import (
    DirectoryObjectStore,
    read_artifact_ref,
    read_manifest_summary,
    write_project_manifest,
)
from abicheck.storage.cross_process_lock import single_flight
from abicheck.storage.package import (
    ArtifactRef,
    ObjectRef,
    PackageManifest,
    VariantRef,
)
from abicheck.storage.package_publish import (
    REF_SCHEMA_VERSION,
    REF_SCHEMA_VERSION_KEY,
    publish_lock_name,
    ref_digest_of,
    versioned_ref_relpath,
)
from abicheck.storage.project_package_archive import (
    pack_project_package,
    unpack_project_package,
)
from abicheck.storage.versioning import PACKAGE_FORMAT_VERSION
from tests._project_manifest_reader import read_project_manifest

_MISSING = "sha256:" + "1" * 64


def _package(root: Path, tag: str, *, n_artifacts: int = 2) -> PackageManifest:
    """A two-variant package whose every section object is stored, with
    content that depends on *tag* so two tags differ in every ref."""
    store = DirectoryObjectStore(root)
    variants: list[VariantRef] = []
    artifacts: list[ArtifactRef] = []
    for variant_id in ("cpu", "gpu"):
        ids = tuple(f"{variant_id}-lib{i}" for i in range(n_artifacts))
        for artifact_id in ids:
            digest = store.put({"kind": "binary", "tag": tag, "id": artifact_id})
            artifacts.append(
                ArtifactRef(
                    artifact_id=artifact_id,
                    variant_id=variant_id,
                    kind="elf",
                    native_identity={"build_id": f"{tag}-{artifact_id}"},
                    sections={"binary": ObjectRef(kind="binary", digest=digest)},
                )
            )
        composition = store.put({"kind": "bundle_composition", "tag": tag})
        variants.append(
            VariantRef(
                variant_id=variant_id,
                captured={"compiler": f"gcc-{tag}"},
                artifact_ids=ids,
                sections={
                    "bundle_composition": ObjectRef(
                        kind="bundle_composition", digest=composition
                    )
                },
            )
        )
    shared = store.put({"kind": "build", "tag": tag})
    return PackageManifest(
        variant_refs=tuple(variants),
        artifact_refs=tuple(artifacts),
        project_sections={"build": ObjectRef(kind="build", digest=shared)},
    )


def _files(root: Path) -> dict[str, bytes]:
    return {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def _missing_section(manifest: PackageManifest, owner: str) -> PackageManifest:
    """*manifest* with one section of the named *owner* pointing at an
    object that was never stored."""
    missing = ObjectRef(kind="binary", digest=_MISSING)
    if owner == "artifact":
        first, *rest = manifest.artifact_refs
        replaced = ArtifactRef(
            artifact_id=first.artifact_id,
            variant_id=first.variant_id,
            kind=first.kind,
            sections={"binary": missing},
        )
        return PackageManifest(
            variant_refs=manifest.variant_refs,
            artifact_refs=(replaced, *rest),
            project_sections=manifest.project_sections,
        )
    if owner == "variant":
        first_v, *rest_v = manifest.variant_refs
        replaced_v = VariantRef(
            variant_id=first_v.variant_id,
            artifact_ids=first_v.artifact_ids,
            sections={"bundle_composition": missing},
        )
        return PackageManifest(
            variant_refs=(replaced_v, *rest_v),
            artifact_refs=manifest.artifact_refs,
            project_sections=manifest.project_sections,
        )
    return PackageManifest(
        variant_refs=manifest.variant_refs,
        artifact_refs=manifest.artifact_refs,
        project_sections={"build": missing},
    )


@pytest.fixture
def published(tmp_path: Path) -> tuple[Path, PackageManifest, dict[str, bytes]]:
    root = tmp_path / "pkg"
    first = _package(root, "v1")
    write_project_manifest(root, first)
    return root, first, _files(root)


class TestFailedRepublishLeavesThePreviousPackageReadable:
    @pytest.mark.parametrize("owner", ["artifact", "variant", "project"])
    def test_a_failed_object_check_changes_nothing_on_disk(
        self, published: tuple[Path, PackageManifest, dict[str, bytes]], owner: str
    ) -> None:
        root, first, before = published
        second = _missing_section(_package(root, "v2"), owner)
        stored_before_attempt = _files(root)

        with pytest.raises(ValueError, match="refusing to publish"):
            write_project_manifest(root, second)

        assert read_project_manifest(root) == first
        # Nothing the check rejected was written: no ref object, no
        # manifest, and no lock left behind.
        assert _files(root) == stored_before_attempt
        assert all(_files(root)[path] == data for path, data in before.items())


class TestInterruptedRepublishLeavesThePreviousPackageReadable:
    def test_an_interruption_at_every_write(
        self,
        published: tuple[Path, PackageManifest, dict[str, bytes]],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Exhaustive over the small domain of write points: crash at the
        k-th file write of a republish for every k -- each of the 6 ref
        documents, then the manifest replace."""
        root, first, before = published
        second = _package(root, "v2")
        real_write = store_module._write_canonical_json_text
        written: list[str] = []

        def recording(text: str, path: Path, **kw: object) -> None:
            written.append(path.name)
            real_write(text, path, **kw)  # type: ignore[arg-type]

        probe = tmp = root.parent / "probe"
        probe_manifest = _package(tmp, "v2")
        monkeypatch.setattr(store_module, "_write_canonical_json_text", recording)
        write_project_manifest(probe, probe_manifest)
        monkeypatch.undo()
        assert len(written) == 7 and written[-1] == "manifest.json"

        for crash_at in range(1, len(written) + 1):
            # A fresh copy each time: earlier attempts' immutable refs would
            # otherwise be reused, shifting which write is the k-th.
            target = root.parent / f"attempt{crash_at}"
            shutil.copytree(root, target)
            calls = 0

            def crashing(text: str, path: Path, **kw: object) -> None:
                nonlocal calls
                calls += 1
                if calls == crash_at:
                    raise OSError(f"simulated crash at write {crash_at}")
                real_write(text, path, **kw)  # type: ignore[arg-type]

            monkeypatch.setattr(store_module, "_write_canonical_json_text", crashing)
            with pytest.raises(OSError, match="simulated crash"):
                write_project_manifest(target, second)
            monkeypatch.undo()

            assert read_project_manifest(target) == first, crash_at
            after = _files(target)
            assert all(after[path] == data for path, data in before.items())


class TestChangedContentRepublishUnderTheSameIds:
    @pytest.mark.parametrize("n_artifacts", [1, 2, 3])
    def test_republish_never_rewrites_a_file_the_old_manifest_named(
        self, tmp_path: Path, n_artifacts: int
    ) -> None:
        root = tmp_path / "pkg"
        first = _package(root, "v1", n_artifacts=n_artifacts)
        write_project_manifest(root, first)
        before = _files(root)
        old_summary = read_manifest_summary(root)

        second = _package(root, "v2", n_artifacts=n_artifacts)
        write_project_manifest(root, second)

        assert read_project_manifest(root) == second
        after = _files(root)
        for path, data in before.items():
            if path != "manifest.json":
                assert after[path] == data, path
        # Every ref of the old package is still a readable, verified object:
        # a reader holding the old summary sees the old package exactly.
        for artifact in first.artifact_refs:
            assert (
                read_artifact_ref(root, artifact.artifact_id, summary=old_summary)
                == artifact
            )
        new_summary = read_manifest_summary(root)
        for kind in ("variant", "artifact"):
            assert set(new_summary.ref_digests[kind]) == set(
                old_summary.ref_digests[kind]
            )
            assert all(
                new_summary.ref_digests[kind][i] != old_summary.ref_digests[kind][i]
                for i in old_summary.ref_digests[kind]
            )

    def test_republishing_identical_content_is_byte_identical(
        self, published: tuple[Path, PackageManifest, dict[str, bytes]]
    ) -> None:
        root, first, before = published
        write_project_manifest(root, first)
        assert _files(root) == before

    def test_the_writer_stamps_the_format_it_writes(self, tmp_path: Path) -> None:
        root = tmp_path / "pkg"
        write_project_manifest(root, _package(root, "v1"))
        raw = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
        assert raw["versions"]["package_format_version"] == PACKAGE_FORMAT_VERSION == 2


class TestConcurrentWritersAreSerialized:
    def test_a_writer_waits_while_another_holds_the_root(
        self, published: tuple[Path, PackageManifest, dict[str, bytes]]
    ) -> None:
        root, first, _before = published
        second = _package(root, "v2")
        writer = threading.Thread(target=write_project_manifest, args=(root, second))
        with single_flight(publish_lock_name(str(root.resolve()))):
            writer.start()
            writer.join(timeout=0.5)
            assert writer.is_alive(), "the second writer did not wait for the lock"
            assert read_project_manifest(root) == first
        writer.join(timeout=30)
        assert not writer.is_alive()
        assert read_project_manifest(root) == second

    @pytest.mark.parametrize(
        "locked", [True, False], ids=["locked", "lock-unavailable"]
    )
    def test_concurrent_writers_never_mix_packages(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, locked: bool
    ) -> None:
        """With the lock, writers are serialized; without it (no `fcntl`,
        reference mode) they still cannot mix packages, since each commits
        by one atomic rename over immutable refs."""
        if not locked:
            import contextlib

            import abicheck.storage.cross_process_lock as lock_module

            monkeypatch.setattr(
                lock_module, "single_flight", lambda _name: contextlib.nullcontext()
            )
        root = tmp_path / "pkg"
        candidates = [_package(root, f"t{i}") for i in range(6)]
        errors: list[BaseException] = []
        barrier = threading.Barrier(len(candidates))

        def publish(manifest: PackageManifest) -> None:
            barrier.wait()
            try:
                write_project_manifest(root, manifest)
            except BaseException as exc:  # pragma: no cover - reported below
                errors.append(exc)

        threads = [threading.Thread(target=publish, args=(m,)) for m in candidates]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert errors == []
        # Whatever won, the live package is exactly one candidate, whole.
        assert read_project_manifest(root) in candidates


def test_the_transport_archive_round_trips_the_v2_layout(
    published: tuple[Path, PackageManifest, dict[str, bytes]], tmp_path: Path
) -> None:
    root, first, _before = published
    archive = tmp_path / "pkg.zip"
    pack_project_package(root, archive)
    out = tmp_path / "unpacked"
    unpack_project_package(archive, out)
    assert read_project_manifest(out) == first
    assert _files(out) == _files(root)


def _manifest(root: Path) -> dict[str, object]:
    return json.loads((root / "manifest.json").read_text(encoding="utf-8"))


def _set_manifest(root: Path, data: dict[str, object]) -> None:
    (root / "manifest.json").write_text(json.dumps(data), encoding="utf-8")


class TestTheRefEntriesAreValidated:
    @pytest.mark.parametrize(
        ("field", "mutate", "match"),
        [
            ("variant_refs", lambda e: e.pop(0), None),
            ("artifact_refs", lambda e: e.append(dict(e[0])), "repeats"),
            ("artifact_refs", lambda e: e[0].update(ref_digest="nope"), "ref_digest"),
            ("variant_refs", lambda e: e[0].update(extra=1), "exactly"),
            ("artifact_refs", lambda e: e[0].update(id="../x"), None),
            (
                "variant_refs",
                lambda e: e.append({"id": "CPU", "ref_digest": e[0]["ref_digest"]}),
                None,
            ),
        ],
        ids=[
            "missing-entry",
            "duplicate-id",
            "bad-digest",
            "extra-key",
            "unsafe-id",
            "case-collision",
        ],
    )
    def test_a_malformed_entry_list_is_refused(
        self,
        published: tuple[Path, PackageManifest, dict[str, bytes]],
        field: str,
        mutate: object,
        match: str | None,
    ) -> None:
        root, _first, _before = published
        data = _manifest(root)
        mutate(data[field])  # type: ignore[operator]
        _set_manifest(root, data)
        if match is None:
            # A dropped entry leaves a variant whose artifacts name it but
            # that the manifest no longer publishes; the others are refused
            # by the id guards every manifest's membership passes.
            with pytest.raises(ValueError):
                read_project_manifest(root)
        else:
            with pytest.raises(ValueError, match=match):
                read_manifest_summary(root)

    def test_a_tampered_ref_document_fails_closed(
        self, published: tuple[Path, PackageManifest, dict[str, bytes]]
    ) -> None:
        root, _first, _before = published
        digest = read_manifest_summary(root).ref_digests["artifact"]["cpu-lib0"]
        path = root / versioned_ref_relpath("artifact", "cpu-lib0", digest)
        document = json.loads(path.read_text(encoding="utf-8"))
        document["kind"] = "pe"
        path.write_text(json.dumps(document), encoding="utf-8")
        with pytest.raises(ValueError, match="does not match its ref_digest"):
            read_artifact_ref(root, "cpu-lib0")

    def test_a_manifest_naming_another_refs_digest_fails_closed(
        self, published: tuple[Path, PackageManifest, dict[str, bytes]]
    ) -> None:
        """The manifest entry and the path disagree with the document."""
        root, _first, _before = published
        data = _manifest(root)
        entries = data["artifact_refs"]
        entries[0]["ref_digest"] = entries[1]["ref_digest"]  # type: ignore[index]
        _set_manifest(root, data)
        with pytest.raises((ValueError, OSError)):
            read_artifact_ref(root, "cpu-lib0")

    def test_a_newer_ref_schema_version_is_refused(
        self, published: tuple[Path, PackageManifest, dict[str, bytes]]
    ) -> None:
        root, _first, _before = published
        summary = read_manifest_summary(root)
        old = root / versioned_ref_relpath(
            "variant", "cpu", summary.ref_digests["variant"]["cpu"]
        )
        document = json.loads(old.read_text(encoding="utf-8"))
        document[REF_SCHEMA_VERSION_KEY] = REF_SCHEMA_VERSION + 1
        digest = ref_digest_of(document)
        new = root / versioned_ref_relpath("variant", "cpu", digest)
        new.write_text(json.dumps(document), encoding="utf-8")
        data = _manifest(root)
        data["variant_refs"][0]["ref_digest"] = digest  # type: ignore[index]
        _set_manifest(root, data)
        with pytest.raises(ValueError, match=REF_SCHEMA_VERSION_KEY):
            read_project_manifest(root)

    def test_a_corrupted_ref_file_is_repaired_by_the_next_publish(
        self, published: tuple[Path, PackageManifest, dict[str, bytes]]
    ) -> None:
        root, first, before = published
        digest = read_manifest_summary(root).ref_digests["variant"]["gpu"]
        path = root / versioned_ref_relpath("variant", "gpu", digest)
        path.write_text("{}", encoding="utf-8")
        write_project_manifest(root, first)
        assert _files(root) == before
