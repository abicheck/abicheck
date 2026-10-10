# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""storage-format-v2 A1.1: one-file zip transport for a ProjectSnapshot package.

Covers the round trip (file-for-file, byte-for-byte), determinism, every
adversarial member shape the unpacker must refuse before writing anything,
and the public workflow: ``compare`` on two archives answers exactly as on
the two directories they were packed from.
"""

from __future__ import annotations

import stat
import sys
import zipfile
from pathlib import Path

import pytest

from abicheck.errors import SnapshotError
from abicheck.storage.project_package_archive import (
    MIMETYPE,
    is_project_package_archive,
    pack_project_package,
    unpack_project_package,
)
from abicheck.storage.zip_member import deterministic_zipinfo

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_cli_compare_release_project_snapshot_package as pkg_fixture  # noqa: E402


def _package(root: Path, *, new: bool = False) -> Path:
    old, new_libs = pkg_fixture._old_new_libraries()
    pkg_fixture._write_package(root, new_libs if new else old)
    return root


def _tree(root: Path) -> dict[str, bytes]:
    return {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def test_round_trip_reproduces_the_tree_exactly(tmp_path: Path) -> None:
    src = _package(tmp_path / "pkg")
    archive = tmp_path / "pkg.zip"
    pack_project_package(src, archive)
    assert is_project_package_archive(archive)
    out = tmp_path / "out"
    unpack_project_package(archive, out)
    assert _tree(out) == _tree(src)


def test_packing_is_deterministic(tmp_path: Path) -> None:
    """Two packages of the same content written separately (different
    directory creation order on disk) pack to identical bytes."""
    a = _package(tmp_path / "a")
    b = _package(tmp_path / "b")
    pack_project_package(a, tmp_path / "a.zip")
    pack_project_package(b, tmp_path / "b.zip")
    assert (tmp_path / "a.zip").read_bytes() == (tmp_path / "b.zip").read_bytes()


def test_a_non_package_or_stray_file_is_not_packed(tmp_path: Path) -> None:
    with pytest.raises(SnapshotError):
        pack_project_package(tmp_path, tmp_path / "x.zip")
    src = _package(tmp_path / "pkg")
    (src / "notes.txt").write_text("hi")
    with pytest.raises(SnapshotError):
        pack_project_package(src, tmp_path / "x.zip")
    assert not (tmp_path / "x.zip").exists()


def test_other_zips_are_not_mistaken_for_a_package(tmp_path: Path) -> None:
    plain = tmp_path / "plain.zip"
    with zipfile.ZipFile(plain, "w") as zf:
        zf.writestr("manifest.json", "{}")
    assert not is_project_package_archive(plain)
    assert not is_project_package_archive(tmp_path)
    (tmp_path / "text").write_text("PK not really")
    assert not is_project_package_archive(tmp_path / "text")


def _hostile(
    tmp_path: Path, members: list[tuple[zipfile.ZipInfo | str, bytes]]
) -> Path:
    src = _package(tmp_path / "pkg")
    archive = tmp_path / "hostile.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr(deterministic_zipinfo("mimetype"), MIMETYPE)
        zf.writestr(
            deterministic_zipinfo("manifest.json"), (src / "manifest.json").read_bytes()
        )
        for info, data in members:
            zf.writestr(
                info if isinstance(info, zipfile.ZipInfo) else _raw_zipinfo(info),
                data,
            )
    return archive


def _raw_zipinfo(name: str) -> zipfile.ZipInfo:
    """A member stored under exactly *name*. ``ZipInfo`` rewrites a
    backslash to "/" on Windows, which would turn a hostile name into a
    benign one before it ever reached the archive under test."""
    info = deterministic_zipinfo(name)
    info.filename = name
    return info


def _symlink_member(name: str) -> zipfile.ZipInfo:
    info = deterministic_zipinfo(name)
    info.external_attr = (stat.S_IFLNK | 0o777) << 16
    return info


def _deflated(name: str) -> zipfile.ZipInfo:
    info = deterministic_zipinfo(name)
    info.compress_type = zipfile.ZIP_DEFLATED
    return info


@pytest.mark.parametrize(
    "member",
    [
        "../escape.json",
        "/etc/passwd",
        "refs/../../escape.json",
        "refs\\artifacts\\x.json",
        "refs/artifacts/../x.json",
        "objects/sha256/aa/not-a-digest.json.zst",
        "objects/sha256/zz/" + "0" * 64 + ".json.zst",  # wrong fan-out
        "refs/artifacts/",
        "stray.txt",
        _symlink_member("refs/artifacts/lib.json"),
        _deflated("refs/artifacts/lib.json"),
    ],
    ids=lambda m: m if isinstance(m, str) else f"special:{m.filename}",
)
def test_hostile_members_are_refused_before_anything_is_written(
    tmp_path: Path, member
) -> None:
    archive = _hostile(tmp_path, [(member, b"{}")])
    dest = tmp_path / "dest"
    with pytest.raises(SnapshotError):
        unpack_project_package(archive, dest)
    assert not dest.exists() or not any(dest.iterdir())
    assert not (tmp_path / "escape.json").exists()


def test_duplicate_members_are_refused(tmp_path: Path) -> None:
    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # zipfile warns on the duplicate
        archive = _hostile(
            tmp_path, [("refs/variants/v.json", b"{}"), ("refs/variants/v.json", b"[]")]
        )
    with pytest.raises(SnapshotError):
        unpack_project_package(archive, tmp_path / "dest")


def test_a_missing_or_wrong_mimetype_is_refused(tmp_path: Path) -> None:
    archive = tmp_path / "a.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr(deterministic_zipinfo("mimetype"), "application/zip")
        zf.writestr(deterministic_zipinfo("manifest.json"), "{}")
    with pytest.raises(SnapshotError):
        unpack_project_package(archive, tmp_path / "dest")


def test_a_non_empty_destination_is_refused(tmp_path: Path) -> None:
    src = _package(tmp_path / "pkg")
    pack_project_package(src, tmp_path / "p.zip")
    dest = tmp_path / "dest"
    dest.mkdir()
    (dest / "keep").write_text("mine")
    with pytest.raises(SnapshotError):
        unpack_project_package(tmp_path / "p.zip", dest)
    assert (dest / "keep").read_text() == "mine"


def test_compare_on_archives_matches_compare_on_directories(tmp_path: Path) -> None:
    old_dir = _package(tmp_path / "old_pkg")
    new_dir = _package(tmp_path / "new_pkg", new=True)
    pack_project_package(old_dir, tmp_path / "old.zip")
    pack_project_package(new_dir, tmp_path / "new.zip")
    ec_dir, out_dir = pkg_fixture._invoke(
        "compare", str(old_dir), str(new_dir), "-o", "json=-"
    )
    ec_zip, out_zip = pkg_fixture._invoke(
        "compare", str(tmp_path / "old.zip"), str(tmp_path / "new.zip"), "-o", "json=-"
    )
    assert ec_zip == ec_dir == 4
    assert pkg_fixture._sorted_outcomes(out_zip) == pkg_fixture._sorted_outcomes(
        out_dir
    )


def test_a_corrupt_archive_operand_is_a_usage_error(tmp_path: Path) -> None:
    old_dir = _package(tmp_path / "old_pkg")
    archive = _hostile(tmp_path, [("../escape.json", b"{}")])
    ec, _ = pkg_fixture._invoke("compare", str(archive), str(old_dir))
    assert ec == 64


def test_resolve_input_reads_a_single_artifact_archive_like_its_directory(
    tmp_path: Path,
) -> None:
    """The typed-API input path (``resolve_input``) accepts the archive
    interchangeably with the directory, and leaves no unpacked copy behind."""
    import tempfile

    from abicheck.model import AbiSnapshot, Function, Visibility
    from abicheck.project_snapshot_legacy import write_legacy_snapshot_package
    from abicheck.serialization import SCHEMA_VERSION, snapshot_to_dict
    from abicheck.workflows.input_resolution import (
        is_stored_snapshot_operand,
        resolve_input,
    )

    snap = AbiSnapshot(
        library="libone.so",
        version="1",
        functions=[
            Function(
                name="f",
                mangled="_Z1fv",
                return_type="int",
                visibility=Visibility.PUBLIC,
            )
        ],
    )
    pkg = tmp_path / "one"
    write_legacy_snapshot_package(
        snapshot_to_dict(snap),
        pkg,
        artifact_id="libone.so",
        max_known_schema_version=SCHEMA_VERSION,
    )
    archive = tmp_path / "one.zip"
    pack_project_package(pkg, archive)
    before = set(Path(tempfile.gettempdir()).glob("abicheck-package-*"))
    from_dir = resolve_input(pkg)
    from_zip = resolve_input(archive)
    assert (
        [f.mangled for f in from_zip.declarations.functions]
        == [f.mangled for f in from_dir.declarations.functions]
        == ["_Z1fv"]
    )
    assert set(Path(tempfile.gettempdir()).glob("abicheck-package-*")) == before
    assert is_stored_snapshot_operand(archive)


@pytest.mark.parametrize(
    "member",
    ["refs\\artifacts\\x.json", "refs\\variants\\v.json", "manifest\\x.json"],
)
def test_a_name_zipfile_rewrites_is_refused_as_on_windows(
    tmp_path: Path, member: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Read the archive the way Windows does: ``zipfile`` turns each
    backslash into "/" in ``ZipInfo.filename`` there, so validating that
    field alone accepts a name every other platform refuses."""
    archive = _hostile(tmp_path, [(member, b"{}")])
    real = zipfile._sanitize_filename

    def _windows(name: str) -> str:
        return real(name).replace("\\", "/")

    monkeypatch.setattr(zipfile, "_sanitize_filename", _windows)
    dest = tmp_path / "dest"
    with pytest.raises(SnapshotError, match="portable"):
        unpack_project_package(archive, dest)
    assert not dest.exists() or not any(dest.iterdir())


@pytest.mark.parametrize("operand", ["directory", "archive"])
def test_generation_notice_names_the_operand_the_user_gave(
    tmp_path: Path, operand: str
) -> None:
    """A drift notice on an archive operand names the archive, never the
    temporary directory it was unpacked into."""
    import json

    from abicheck.model import AbiSnapshot
    from abicheck.project_snapshot_legacy import write_legacy_snapshot_package
    from abicheck.serialization import SCHEMA_VERSION, snapshot_to_dict
    from abicheck.storage import versioning
    from abicheck.workflows.input_resolution import resolve_input

    pkg = tmp_path / "one"
    write_legacy_snapshot_package(
        snapshot_to_dict(AbiSnapshot(library="libone.so", version="1")),
        pkg,
        artifact_id="libone.so",
        max_known_schema_version=SCHEMA_VERSION,
    )
    manifest = pkg / "manifest.json"
    data = json.loads(manifest.read_text())
    data["versions"]["extractor_generation"] = versioning.EXTRACTOR_GENERATION + 1
    manifest.write_text(json.dumps(data))
    target = pkg
    if operand == "archive":
        target = tmp_path / "one.zip"
        pack_project_package(pkg, target)
    (notice,) = resolve_input(target).load_notices
    assert notice.startswith(f"stored package '{target}': ")
    assert "abicheck-package-" not in notice
