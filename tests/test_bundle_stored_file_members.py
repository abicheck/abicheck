# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""`build_bundle_snapshot_mixed` resolves a *file*-backed stored snapshot
member, not only a directory-backed `ProjectSnapshot` package.

Bug class `evidence.operand_shape_silently_unresolved`
(`tests/regressions/manifest_evidence.py`). The reported instance: a release
comparison pointed at a directory of six loose header-depth
`.abicheck.json.zst` snapshots produced an *empty* OLD-side bundle graph, so
every cross-DSO check (provider migration, intra-dependency symbol removal,
SONAME skew) degraded to "every library in this release is new" and the
bundle layer scored `COMPATIBLE` with five phantom `BUNDLE_LIBRARY_ADDED`
findings -- a silent pass on exactly the class of break
`build_bundle_snapshot_mixed` was added to catch.

The mechanism was the stored-member split's own predicate, `path.is_dir()`:
a loose snapshot file is neither a directory nor ELF, so it fell through to
`build_bundle_snapshot` and was dropped there as "not ELF".

These tests state the invariant over the *operand-shape axis* rather than
the one reported input: every storage spelling the project's own writer can
produce (plain / gzip / zstd, flat or ADR-062/063-sectioned) must resolve to
the same `ElfMetadata`, and the oracle is the snapshot that was written --
never a second call to the resolver under test.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from abicheck.bundle import (
    _looks_like_stored_snapshot_file,
    build_bundle_snapshot_mixed,
)
from abicheck.elf_metadata import ElfMetadata, ElfSymbol
from abicheck.model import Function, Visibility
from abicheck.model.snapshot import AbiSnapshot
from abicheck.serialization import write_snapshot

#: Every compression the project's own writer supports. "auto" is
#: deliberately included: it is what an ordinary `abicheck dump` uses, so a
#: fix that handled only the explicitly-spelled ones would still miss the
#: shape users actually produce.
WRITER_COMPRESSIONS = ("none", "gzip", "zstd", "auto")


def _snapshot(soname: str, *, needed: tuple[str, ...] = ()) -> AbiSnapshot:
    elf = ElfMetadata(
        soname=soname,
        symbols=[ElfSymbol(name="_Z3pubv")],
        needed=list(needed),
    )
    return AbiSnapshot(
        library=soname,
        version="1",
        functions=[
            Function(
                name="pub",
                mangled="_Z3pubv",
                return_type="void",
                params=[],
                visibility=Visibility.PUBLIC,
            )
        ],
        variables=[],
        types=[],
        elf=elf,
    )


def _write(snap: AbiSnapshot, path: Path, compression: str) -> Path:
    write_snapshot(snap, path, compression=compression)
    return path


class TestFileBackedStoredMemberResolves:
    """The invariant, across the whole operand-shape axis."""

    @pytest.mark.parametrize("compression", WRITER_COMPRESSIONS)
    def test_every_writer_compression_resolves(
        self, tmp_path: Path, compression: str
    ) -> None:
        # The oracle is the snapshot we wrote, not a second resolver call.
        expected_soname = "liba.so.1"
        expected_needed = ("libb.so.1",)
        path = _write(
            _snapshot(expected_soname, needed=expected_needed),
            tmp_path / f"a-{compression}.abicheck.json",
            compression,
        )

        snap = build_bundle_snapshot_mixed({expected_soname: path})

        assert expected_soname in snap.metadata, (
            f"{compression}-compressed stored snapshot was silently dropped "
            "from the bundle graph"
        )
        resolved = snap.metadata[expected_soname]
        assert resolved.soname == expected_soname
        assert tuple(resolved.needed) == expected_needed
        assert [s.name for s in resolved.symbols] == ["_Z3pubv"]

    def test_sectioned_document_resolves(self, tmp_path: Path) -> None:
        """The ADR-062/063 Phase 8 sectioned envelope is the on-disk default
        for every snapshot written since it landed, so reading ``["elf"]``
        without unwrapping would report every modern snapshot as carrying no
        ELF metadata. Asserted against a document this test *proves* is
        sectioned, so it cannot pass vacuously if the writer's default
        changes."""
        from abicheck.storage.sectioned_document import is_sectioned_document

        path = _write(_snapshot("libc.so.1"), tmp_path / "c.json", "none")
        document = json.loads(path.read_text())
        if not is_sectioned_document(document):
            pytest.skip("writer does not emit the sectioned envelope here")

        snap = build_bundle_snapshot_mixed({"libc.so.1": path})
        assert snap.metadata["libc.so.1"].soname == "libc.so.1"

    def test_directory_and_file_members_resolve_together(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A mixed map must not regress the directory-backed half: the two
        stored spellings resolve in one call, through their own readers."""
        monkeypatch.setattr(
            "abicheck.project_snapshot_legacy.read_legacy_snapshot_document",
            lambda path: {"elf": {"soname": "libdir.so.1"}, "schema_version": 1},
        )
        stored_dir = tmp_path / "pkg"
        stored_dir.mkdir()
        stored_file = _write(
            _snapshot("libfile.so.1"), tmp_path / "f.abicheck.json", "zstd"
        )

        snap = build_bundle_snapshot_mixed(
            {"libdir.so.1": stored_dir, "libfile.so.1": stored_file}
        )

        assert sorted(snap.metadata) == ["libdir.so.1", "libfile.so.1"]


class TestNoPhantomAdditions:
    """The bug's *consequence*, asserted at the layer a user reads.

    Resolving the member is only half the fix: the reported symptom was a
    bundle verdict of COMPATIBLE carried by phantom ``BUNDLE_LIBRARY_ADDED``
    findings. This states that an unchanged release compared against itself
    through the file-backed spelling reports no library as added.
    """

    def test_unchanged_release_reports_no_added_library(self, tmp_path: Path) -> None:
        from abicheck.bundle_analysis import analyze_bundle
        from abicheck.checker_policy import ChangeKind

        names = ("liba.so.1", "libb.so.1")
        libraries = {
            name: _write(
                _snapshot(name, needed=("libb.so.1",) if name == "liba.so.1" else ()),
                tmp_path / f"{name}.abicheck.json.zst",
                "zstd",
            )
            for name in names
        }

        old = build_bundle_snapshot_mixed(dict(libraries))
        new = build_bundle_snapshot_mixed(dict(libraries))
        result = analyze_bundle(old, new, [])

        added = [
            c
            for c in result.bundle_findings
            if c.kind is ChangeKind.BUNDLE_LIBRARY_ADDED
        ]
        assert added == [], (
            "an unchanged release reported libraries as newly added -- the "
            f"OLD-side graph is empty again: {[c.symbol or c.name for c in added]}"
        )
        assert sorted(old.metadata) == sorted(names)


class TestStoredSnapshotFileClassification:
    """`_looks_like_stored_snapshot_file` is the reusable predicate the fix
    rests on, so it gets its own contract tests rather than only being
    exercised through its caller (AGENTS.md, "Primitive-level property
    tests")."""

    @pytest.mark.parametrize("compression", WRITER_COMPRESSIONS)
    def test_true_for_every_writer_output(
        self, tmp_path: Path, compression: str
    ) -> None:
        path = _write(
            _snapshot("libx.so.1"), tmp_path / f"x-{compression}.json", compression
        )
        assert _looks_like_stored_snapshot_file(path) is True

    def test_false_for_elf(self, tmp_path: Path) -> None:
        """Classification must never claim an ELF binary: a live member
        misrouted to the stored reader would lose its real parse."""
        path = tmp_path / "libreal.so"
        path.write_bytes(b"\x7fELF" + b"\x00" * 128)
        assert _looks_like_stored_snapshot_file(path) is False

    @pytest.mark.parametrize(
        ("label", "payload"),
        [
            ("empty", b""),
            ("text", b"not a snapshot\n"),
            ("json-array", b"[1, 2, 3]"),
            ("ar-archive", b"!<arch>\n"),
            ("random-binary", bytes(range(64))),
            ("script", b"#!/bin/sh\nexit 0\n"),
        ],
    )
    def test_false_for_non_snapshot_files(
        self, tmp_path: Path, label: str, payload: bytes
    ) -> None:
        path = tmp_path / label
        path.write_bytes(payload)
        assert _looks_like_stored_snapshot_file(path) is False

    def test_false_for_directory_and_missing_path(self, tmp_path: Path) -> None:
        d = tmp_path / "d"
        d.mkdir()
        assert _looks_like_stored_snapshot_file(d) is False
        assert _looks_like_stored_snapshot_file(tmp_path / "absent") is False

    def test_leading_whitespace_is_tolerated(self, tmp_path: Path) -> None:
        """Detection is by content, not filename, so a hand-formatted
        document with leading whitespace still classifies."""
        path = tmp_path / "ws.json"
        path.write_bytes(b'\n\t  {"elf": {}}')
        assert _looks_like_stored_snapshot_file(path) is True


class TestMalformedFileMemberIsSkippedNotFatal:
    """Mirrors the directory-backed half's own contract
    (`test_cli_compare_release_evidence_preservation.py`): an unresolvable
    member is dropped, never an exception that discards every sibling's
    resolution."""

    def test_malformed_member_does_not_abort_the_snapshot(self, tmp_path: Path) -> None:
        good = _write(_snapshot("libgood.so.1"), tmp_path / "good.json", "zstd")
        bad = tmp_path / "bad.json"
        bad.write_bytes(b'{"elf": "not-an-object", "schema_version": "nope"}')

        snap = build_bundle_snapshot_mixed({"libgood.so.1": good, "libbad.so.1": bad})

        assert "libgood.so.1" in snap.metadata
        assert "libbad.so.1" not in snap.metadata
