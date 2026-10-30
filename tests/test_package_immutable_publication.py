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
from abicheck.storage.package import (
    ArtifactRef,
    ObjectRef,
    PackageManifest,
    VariantRef,
)
from abicheck.storage.package_publish import (
    PUBLISH_LOCK_NAME,
    ConcurrentPublishError,
    publish_lock,
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
    def _count_ref_puts(self, root: Path) -> int:
        calls = 0
        real_put = DirectoryObjectStore.put

        def counting(self: DirectoryObjectStore, content: object, **kw: object) -> str:
            nonlocal calls
            calls += 1
            return real_put(self, content, **kw)  # type: ignore[arg-type]

        probe = _package(root / "probe", "p")
        try:
            DirectoryObjectStore.put = counting  # type: ignore[method-assign]
            write_project_manifest(root / "probe", probe)
        finally:
            DirectoryObjectStore.put = real_put  # type: ignore[method-assign]
        return calls

    def test_an_interruption_at_every_ref_write_and_the_manifest_write(
        self,
        published: tuple[Path, PackageManifest, dict[str, bytes]],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Exhaustive over the small domain of write points: crash at the
        k-th ref object write for every k, and at the manifest write."""
        root, first, before = published
        n_ref_writes = self._count_ref_puts(root.parent)
        assert n_ref_writes == 6  # 2 variants + 4 artifacts
        second = _package(root, "v2")
        real_put = DirectoryObjectStore.put
        real_write = store_module._write_canonical_json_text

        for crash_at in range(1, n_ref_writes + 2):
            calls = 0

            def crashing_put(
                self: DirectoryObjectStore, content: object, **kw: object
            ) -> str:
                nonlocal calls
                calls += 1
                if calls == crash_at:
                    raise OSError("simulated crash during a ref write")
                return real_put(self, content, **kw)  # type: ignore[arg-type]

            def crashing_manifest(text: str, path: Path, **kw: object) -> None:
                if path.name == "manifest.json":
                    raise OSError("simulated crash before the manifest replace")
                real_write(text, path, **kw)  # type: ignore[arg-type]

            monkeypatch.setattr(DirectoryObjectStore, "put", crashing_put)
            monkeypatch.setattr(
                store_module, "_write_canonical_json_text", crashing_manifest
            )
            with pytest.raises(OSError, match="simulated crash"):
                write_project_manifest(root, second)
            monkeypatch.undo()

            assert read_project_manifest(root) == first, crash_at
            after = _files(root)
            assert all(after[path] == data for path, data in before.items())
            assert PUBLISH_LOCK_NAME not in after


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


class TestConcurrentWritersAreRefused:
    def test_a_second_writer_is_refused_while_the_first_holds_the_root(
        self, published: tuple[Path, PackageManifest, dict[str, bytes]]
    ) -> None:
        root, first, before = published
        with publish_lock(root):
            with pytest.raises(ConcurrentPublishError, match="another writer"):
                write_project_manifest(root, _package(root, "v2"))
        assert read_project_manifest(root) == first
        # Released: the lock file is gone and the next publish proceeds.
        assert not (root / PUBLISH_LOCK_NAME).exists()
        write_project_manifest(root, first)
        after = _files(root)
        assert all(after[path] == data for path, data in before.items())

    def test_writer_threads_are_serialized_or_refused_never_interleaved(
        self, tmp_path: Path
    ) -> None:
        root = tmp_path / "pkg"
        candidates = [_package(root, f"t{i}") for i in range(6)]
        outcomes: list[str] = []
        barrier = threading.Barrier(len(candidates))

        def publish(manifest: PackageManifest) -> None:
            barrier.wait()
            try:
                write_project_manifest(root, manifest)
                outcomes.append("published")
            except ConcurrentPublishError:
                outcomes.append("refused")

        threads = [threading.Thread(target=publish, args=(m,)) for m in candidates]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert "published" in outcomes
        assert set(outcomes) <= {"published", "refused"}
        # Whatever won, the live package is exactly one candidate, whole.
        assert read_project_manifest(root) in candidates

    def test_a_stale_lock_from_a_dead_writer_does_not_block(
        self, published: tuple[Path, PackageManifest, dict[str, bytes]]
    ) -> None:
        """`flock` dies with its holder: a leftover, unlocked lock file is
        not a held lock."""
        root, first, _before = published
        (root / PUBLISH_LOCK_NAME).write_text("", encoding="utf-8")
        write_project_manifest(root, first)
        assert read_project_manifest(root) == first


def test_the_transport_archive_skips_the_lock_and_round_trips(
    published: tuple[Path, PackageManifest, dict[str, bytes]], tmp_path: Path
) -> None:
    root, first, _before = published
    (root / PUBLISH_LOCK_NAME).write_text("", encoding="utf-8")
    archive = tmp_path / "pkg.zip"
    pack_project_package(root, archive)
    out = tmp_path / "unpacked"
    unpack_project_package(archive, out)
    assert read_project_manifest(out) == first
    assert not (out / PUBLISH_LOCK_NAME).exists()


class TestTheRefDigestMapIsValidated:
    @pytest.mark.parametrize(
        ("field", "mutate"),
        [
            ("variant_refs", lambda m: m.pop("cpu")),
            ("artifact_refs", lambda m: m.update({"ghost": next(iter(m.values()))})),
            ("artifact_refs", lambda m: m.update({"cpu-lib0": "not-a-digest"})),
        ],
    )
    def test_a_map_not_naming_exactly_the_published_ids_is_refused(
        self,
        published: tuple[Path, PackageManifest, dict[str, bytes]],
        field: str,
        mutate: object,
    ) -> None:
        root, _first, _before = published
        path = root / "manifest.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        mutate(data[field])  # type: ignore[operator]
        path.write_text(json.dumps(data), encoding="utf-8")
        with pytest.raises(ValueError, match=field):
            read_manifest_summary(root)

    def test_a_tampered_ref_object_is_refused_by_digest(
        self, published: tuple[Path, PackageManifest, dict[str, bytes]]
    ) -> None:
        root, _first, _before = published
        digest = read_manifest_summary(root).ref_digests["artifact"]["cpu-lib0"]
        hexdigest = digest.split(":", 1)[1]
        (path,) = (root / "objects").rglob(f"{hexdigest}.json.zst")
        DirectoryObjectStore(root.parent / "scratch").put({"x": 1})
        (scratch,) = (root.parent / "scratch" / "objects").rglob("*.json.zst")
        path.write_bytes(scratch.read_bytes())
        with pytest.raises(ValueError, match="does not match its requested digest"):
            read_artifact_ref(root, "cpu-lib0")
