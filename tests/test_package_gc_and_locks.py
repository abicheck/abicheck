# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""ADR-062 D16/D17 residuals: package garbage collection, the Windows
publish lock, and the `transport` digest of a package archive.

Oracles are independent of the code under test: the live set is recomputed
here by parsing `manifest.json` and the ref documents directly; lock
exclusion is observed as overlapping critical sections; transport digests
are `hashlib.sha256` over the zip member bytes.
"""

from __future__ import annotations

import errno
import hashlib
import json
import os
import threading
import time
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

import abicheck.storage.cross_process_lock as lock_module
from abicheck.errors import SnapshotError
from abicheck.model import AbiSnapshot, Function
from abicheck.project_snapshot_legacy import read_stored_snapshot_document
from abicheck.project_snapshot_store import DirectoryObjectStore, write_project_manifest
from abicheck.serialization import SCHEMA_VERSION, snapshot_from_dict, snapshot_to_dict
from abicheck.storage.import_v1 import import_legacy_snapshot
from abicheck.storage.package import InMemoryObjectStore, PackageManifest
from abicheck.storage.project_package_archive import (
    pack_project_package,
    unpack_project_package,
)
from abicheck.storage.transport_digest import TRANSPORT_EXTRA_ID
from abicheck.workflows.package_gc import collect_package_garbage

# -- helpers -----------------------------------------------------------------


def _stage(root: Path, version: str) -> PackageManifest:
    """Put one version's objects into *root* (a writer before its publish)."""
    snap = AbiSnapshot(
        library="libgc.so.1",
        version=version,
        functions=[
            Function(name=f"f{version}", mangled=f"f{version}", return_type="int")
        ],
    )
    staging = InMemoryObjectStore()
    manifest = import_legacy_snapshot(
        snapshot_to_dict(snap),
        store=staging,
        artifact_id="a",
        max_known_schema_version=SCHEMA_VERSION,
    )
    store = DirectoryObjectStore(root)
    for artifact in manifest.artifact_refs:
        for ref in artifact.sections.values():
            store.put(staging.get(ref.digest))
    return manifest


def _publish(root: Path, version: str) -> None:
    write_project_manifest(root, _stage(root, version))


def _oracle_live(root: Path) -> set[str]:
    """Live files, from the JSON on disk alone."""
    manifest = json.loads((root / "manifest.json").read_text())
    live: set[str] = set()
    digests: list[str] = []
    for kind, field in (("variants", "variant_refs"), ("artifacts", "artifact_refs")):
        for entry in manifest[field]:
            hexdigest = entry["ref_digest"].split(":", 1)[1]
            rel = f"refs/{kind}/{entry['id']}/{hexdigest}.json"
            live.add(rel)
            ref = json.loads((root / rel).read_text())
            digests += [s["digest"] for s in ref.get("sections", {}).values()]
            if "carriers" in ref:
                digests.append(ref["carriers"]["digest"])
    digests += [s["digest"] for s in manifest.get("project_sections", {}).values()]
    for digest in digests:
        algorithm, hexdigest = digest.split(":", 1)
        live.add(f"objects/{algorithm}/{hexdigest[:2]}/{hexdigest}.json.zst")
    return live


def _files(root: Path) -> set[str]:
    return {
        p.relative_to(root).as_posix()
        for top in ("refs", "objects")
        for p in (root / top).rglob("*")
        if p.is_file()
    }


def _age(root: Path, seconds: float) -> None:
    past = time.time() - seconds
    for path in root.rglob("*"):
        if path.is_file():
            os.utime(path, (past, past))


# -- garbage collection --------------------------------------------------------


@pytest.mark.parametrize("dry_run", [False, True])
@pytest.mark.parametrize("staged_unpublished", [False, True])
@pytest.mark.parametrize("old", [False, True], ids=["young", "old"])
@pytest.mark.parametrize("republishes", [0, 1, 3])
def test_gc_removes_exactly_the_old_unnamed_files(
    tmp_path: Path, republishes: int, old: bool, staged_unpublished: bool, dry_run: bool
) -> None:
    root = tmp_path / "pkg"
    for i in range(republishes + 1):
        _publish(root, f"{i}.0")
    if staged_unpublished:
        _stage(root, "crashed")  # a writer that died before publishing
    if old:
        _age(root, 7200)
    live = _oracle_live(root)
    before = _files(root)
    garbage = before - live
    result = collect_package_garbage(root, dry_run=dry_run)
    expected_removed = garbage if old else set()
    assert set(result.removed) == expected_removed
    assert set(result.kept_recent) == (set() if old else garbage)
    assert _files(root) == (before if dry_run else before - expected_removed)
    assert _oracle_live(root) == live
    document, _ref = read_stored_snapshot_document(root)
    assert snapshot_from_dict(document).version == f"{republishes}.0"
    # Idempotent.
    if not dry_run:
        assert collect_package_garbage(root).removed == ()


def test_gc_keeps_an_old_object_a_writer_reuses(tmp_path: Path) -> None:
    """`DirectoryObjectStore.put` refreshes a reused object's mtime, so the
    grace period protects an object a writer is about to publish again."""
    root = tmp_path / "pkg"
    _publish(root, "1.0")
    _publish(root, "2.0")
    _age(root, 7200)
    staged = _stage(root, "1.0")  # re-stages 1.0's (now unnamed) objects
    collect_package_garbage(root)
    write_project_manifest(root, staged)
    assert snapshot_from_dict(read_stored_snapshot_document(root)[0]).version == "1.0"


def test_a_writer_whose_staged_object_was_collected_refuses(tmp_path: Path) -> None:
    root = tmp_path / "pkg"
    _publish(root, "1.0")
    staged = _stage(root, "2.0")
    collect_package_garbage(root, min_age_seconds=0)
    with pytest.raises(ValueError, match="refusing to publish"):
        write_project_manifest(root, staged)
    assert snapshot_from_dict(read_stored_snapshot_document(root)[0]).version == "1.0"


def test_gc_waits_for_a_publish_holding_the_lock(tmp_path: Path) -> None:
    from abicheck.storage.package_publish import publish_lock_name

    root = tmp_path / "pkg"
    _publish(root, "1.0")
    done = threading.Event()
    gc = threading.Thread(
        target=lambda: (collect_package_garbage(root, min_age_seconds=0), done.set())
    )
    with lock_module.single_flight(publish_lock_name(str(root.resolve()))):
        gc.start()
        assert not done.wait(0.3), "gc ran while a publisher held the lock"
    gc.join(timeout=30)
    assert done.is_set()


@pytest.mark.parametrize("seed", range(3))
def test_gc_concurrent_with_writers_never_breaks_the_package(
    tmp_path: Path, seed: int
) -> None:
    root = tmp_path / "pkg"
    _publish(root, "0.0")
    errors: list[BaseException] = []
    barrier = threading.Barrier(5)

    def run(action) -> None:
        barrier.wait()
        try:
            action()
        except ValueError as exc:  # a writer whose staged object was collected
            if "refusing to publish" not in str(exc):
                errors.append(exc)
        except BaseException as exc:  # pragma: no cover - reported below
            errors.append(exc)

    actions = [lambda i=i: _publish(root, f"{seed}.{i}") for i in range(1, 4)]
    actions += [lambda: collect_package_garbage(root, min_age_seconds=0)]
    actions += [lambda: collect_package_garbage(root, min_age_seconds=0)]
    threads = [threading.Thread(target=run, args=(a,)) for a in actions]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert _oracle_live(root) <= _files(root)
    read_stored_snapshot_document(root)


def test_gc_refuses_a_v1_package_and_negative_grace(tmp_path: Path) -> None:
    root = tmp_path / "pkg"
    _publish(root, "1.0")
    with pytest.raises(ValueError, match="negative"):
        collect_package_garbage(root, min_age_seconds=-1)
    data = json.loads((root / "manifest.json").read_text())
    data["variant_ids"] = [e["id"] for e in data.pop("variant_refs")]
    data["artifact_ids"] = [e["id"] for e in data.pop("artifact_refs")]
    data["versions"]["package_format_version"] = 1
    (root / "manifest.json").write_text(json.dumps(data))
    with pytest.raises((SnapshotError, ValueError)):
        collect_package_garbage(root)


# -- the Windows lock (msvcrt.locking simulated off Windows) ----------------------


class _FakeMsvcrt:
    """`msvcrt.locking` semantics: a non-blocking byte-range lock per file,
    held by one handle; EACCES while another handle holds it; unlocking a
    range the handle does not hold is an error."""

    LK_NBLCK = 2
    LK_UNLCK = 0

    def __init__(self) -> None:
        self._guard = threading.Lock()
        self._held: dict[tuple[int, int], int] = {}
        self.calls: list[str] = []

    def locking(self, fd: int, mode: int, nbytes: int) -> None:
        assert nbytes == 1 and os.lseek(fd, 0, os.SEEK_CUR) == 0
        st = os.fstat(fd)
        key = (st.st_dev, st.st_ino)
        with self._guard:
            holder = self._held.get(key)
            if mode == self.LK_NBLCK:
                self.calls.append("lock")
                if holder is not None:
                    raise PermissionError(errno.EACCES, "locked")
                self._held[key] = fd
            elif mode == self.LK_UNLCK:
                self.calls.append("unlock")
                if holder != fd:
                    raise OSError(errno.EACCES, "not held")
                del self._held[key]


@pytest.fixture
def fake_msvcrt(monkeypatch: pytest.MonkeyPatch) -> _FakeMsvcrt:
    fake = _FakeMsvcrt()
    monkeypatch.setattr(
        lock_module, "_lock_backend", lambda: lock_module._LockBackend(fake, "msvcrt")
    )
    return fake


@pytest.mark.parametrize("workers", [2, 5])
def test_msvcrt_lock_serializes_writers(fake_msvcrt: _FakeMsvcrt, workers: int) -> None:
    inside = 0
    peak = 0
    counter = 0
    guard = threading.Lock()
    barrier = threading.Barrier(workers)

    def work() -> None:
        nonlocal inside, peak, counter
        barrier.wait()
        for _ in range(5):
            with lock_module.single_flight("msvcrt-test"):
                with guard:
                    inside += 1
                    peak = max(peak, inside)
                value = counter
                time.sleep(0.001)
                counter = value + 1
                with guard:
                    inside -= 1

    threads = [threading.Thread(target=work) for _ in range(workers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert peak == 1
    assert counter == 5 * workers
    assert fake_msvcrt._held == {}
    assert fake_msvcrt.calls.count("unlock") == 5 * workers


def test_msvcrt_deadline_while_waiting_never_unlocks_a_lock_it_lacks(
    fake_msvcrt: _FakeMsvcrt, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Expired(Exception):
        pass

    def expire() -> None:
        raise Expired

    with lock_module.single_flight("msvcrt-deadline"):
        monkeypatch.setattr(lock_module.deadline, "check", expire)
        with pytest.raises(Expired):  # not masked by an unlock error
            with lock_module.single_flight("msvcrt-deadline"):
                pass
    assert fake_msvcrt._held == {}


def test_msvcrt_unsupported_filesystem_runs_unlocked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def locking(fd: int, mode: int, nbytes: int) -> None:
        raise OSError(errno.ENOSYS, "no locking here")

    fake = SimpleNamespace(LK_NBLCK=2, LK_UNLCK=0, locking=locking)
    monkeypatch.setattr(
        lock_module, "_lock_backend", lambda: lock_module._LockBackend(fake, "msvcrt")
    )
    ran = []
    with lock_module.single_flight("msvcrt-nolock"):
        ran.append(True)
    assert ran == [True]


def test_the_package_publish_serializes_on_msvcrt(
    fake_msvcrt: _FakeMsvcrt, tmp_path: Path
) -> None:
    from abicheck.storage.package_publish import publish_lock_name

    root = tmp_path / "pkg"
    _publish(root, "1.0")
    writer = threading.Thread(target=_publish, args=(root, "2.0"))
    with lock_module.single_flight(publish_lock_name(str(root.resolve()))):
        writer.start()
        writer.join(timeout=0.3)
        assert writer.is_alive()
    writer.join(timeout=30)
    assert snapshot_from_dict(read_stored_snapshot_document(root)[0]).version == "2.0"


# -- the transport digest ----------------------------------------------------


def _packed(tmp_path: Path) -> tuple[Path, list[zipfile.ZipInfo]]:
    root = tmp_path / "pkg"
    _publish(root, "1.0")
    archive = tmp_path / "pkg.zip"
    pack_project_package(root, archive)
    with zipfile.ZipFile(archive) as zf:
        return archive, zf.infolist()[1:]


def test_every_member_carries_the_sha256_of_its_stored_bytes(tmp_path: Path) -> None:
    archive, members = _packed(tmp_path)
    with zipfile.ZipFile(archive) as zf:
        for info in members:
            assert info.extra[:4] == TRANSPORT_EXTRA_ID.to_bytes(2, "little") + (
                32
            ).to_bytes(2, "little")
            assert info.extra[4:] == hashlib.sha256(zf.read(info)).digest()


def _rewrite(archive: Path, out: Path, edit) -> None:
    with zipfile.ZipFile(archive) as src, zipfile.ZipFile(out, "w") as dst:
        for index, info in enumerate(src.infolist()):
            data = src.read(info)
            clone = zipfile.ZipInfo(info.filename, date_time=info.date_time)
            clone.compress_type = zipfile.ZIP_STORED
            clone.external_attr = info.external_attr
            clone.extra = info.extra
            data, clone.extra = edit(index, data, clone.extra)
            dst.writestr(clone, data)


def test_a_flipped_byte_in_any_member_is_refused_by_name(tmp_path: Path) -> None:
    archive, members = _packed(tmp_path)
    for target in range(1, len(members) + 1):
        bad = tmp_path / f"bad{target}.zip"

        def flip(i, data, extra, target=target):
            if i == target:
                data = bytes([data[0] ^ 1]) + data[1:]
            return data, extra

        _rewrite(archive, bad, flip)
        with pytest.raises(SnapshotError, match="transport digest") as info:
            unpack_project_package(bad, tmp_path / f"out{target}")
        assert members[target - 1].filename in str(info.value)
        assert not (tmp_path / f"out{target}").exists()


@pytest.mark.parametrize("strip", ["one", "all"])
def test_a_partial_transport_set_is_refused_and_none_is_legacy(
    tmp_path: Path, strip: str
) -> None:
    archive, _members = _packed(tmp_path)
    out = tmp_path / "x.zip"
    _rewrite(
        archive,
        out,
        lambda i, d, e: (d, b"" if strip == "all" or i == 1 else e),
    )
    if strip == "all":
        unpack_project_package(out, tmp_path / "ok")
        assert (tmp_path / "ok" / "manifest.json").is_file()
    else:
        with pytest.raises(SnapshotError, match="no transport digest"):
            unpack_project_package(out, tmp_path / "no")


def test_transport_extra_parser_refuses_malformed_fields() -> None:
    from abicheck.storage.transport_digest import stored_transport_digest

    tag = TRANSPORT_EXTRA_ID.to_bytes(2, "little")
    for bad in (b"\x01", tag + (31).to_bytes(2, "little") + b"\0" * 31, tag + b"\x20"):
        with pytest.raises(ValueError):
            stored_transport_digest(bad)
    other = (0x0001).to_bytes(2, "little") + (2).to_bytes(2, "little") + b"zz"
    assert stored_transport_digest(other) is None
    good = tag + (32).to_bytes(2, "little") + bytes(range(32))
    assert stored_transport_digest(other + good) == "sha256:" + bytes(range(32)).hex()
    # A repeated tag is ambiguous.
    with pytest.raises(ValueError):
        stored_transport_digest(good + good)


@pytest.mark.parametrize("dry_run", [False, True])
def test_gc_skips_a_file_that_vanishes_after_listing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, dry_run: bool
) -> None:
    """A writer stages objects without the lock and renames its `.tmp` file
    into place at any moment, so a file listed by GC may be gone by the time
    GC stats it. GC must skip it, not crash (bug found by the concurrent
    writer test above, which failed about half its runs)."""
    import abicheck.workflows.package_gc as gc_module

    root = tmp_path / "pkg"
    _publish(root, "1.0")
    real = gc_module._candidates
    ghost = root / "objects" / "sha256" / "ff" / ".ff.json.zst.0123.tmp"

    def with_ghost(path: Path):
        yield ghost  # listed, never on disk
        yield from real(path)

    monkeypatch.setattr(gc_module, "_candidates", with_ghost)
    result = collect_package_garbage(root, min_age_seconds=0, dry_run=dry_run)
    rel = ghost.relative_to(root).as_posix()
    assert rel not in result.removed and rel not in result.kept_recent
    assert snapshot_from_dict(read_stored_snapshot_document(root)[0]).version == "1.0"
