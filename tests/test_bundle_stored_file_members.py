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
    _stored_document_library_filename,
    _stored_file_snapshot_evidence,
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

    @pytest.mark.parametrize(
        "padding", [0, 1, 63, 64, 65, 4095, 4096, 4097, 8192, 100_000]
    )
    def test_any_leading_whitespace_the_canonical_reader_tolerates(
        self, tmp_path: Path, padding: int
    ) -> None:
        """Codex review, PR #1269 (P2): the first version sniffed a fixed
        64-byte window, so a document with >=64 leading whitespace bytes
        classified as "not a snapshot", was routed to live-ELF handling and
        dropped from the graph -- the same silent pass this module exists to
        prevent.

        The oracle is deliberately independent of the predicate: a document
        the *canonical* reader (`json.loads` over `read_snapshot_text`)
        accepts as an object must classify here too. The padding values sweep
        the old boundary and both sides of the new chunk size, so a future
        window-shaped implementation fails rather than passing on one
        hand-picked input.
        """
        from abicheck.snapshot_io import read_snapshot_text

        path = tmp_path / f"ws-{padding}.json"
        path.write_bytes(b"\n" * padding + b'{"elf": {}}')

        canonical = json.loads(read_snapshot_text(path))
        assert isinstance(canonical, dict), "oracle did not parse -- test is vacuous"
        assert _looks_like_stored_snapshot_file(path) is True

    def test_mixed_whitespace_kinds_are_skipped(self, tmp_path: Path) -> None:
        """All four JSON whitespace bytes, not only newlines."""
        path = tmp_path / "mixed.json"
        path.write_bytes(b" \t\r\n" * 2048 + b'{"elf": {}}')
        assert _looks_like_stored_snapshot_file(path) is True

    def test_whitespace_only_file_is_not_a_snapshot(self, tmp_path: Path) -> None:
        """The complement: skipping whitespace must not turn a blank file
        into a document."""
        path = tmp_path / "blank.json"
        path.write_bytes(b"\n" * 10_000)
        assert _looks_like_stored_snapshot_file(path) is False

    def test_unbounded_whitespace_is_still_refused(self, tmp_path: Path) -> None:
        """The bound is real -- a pathological file is rejected rather than
        scanned forever. Asserted at the documented bound, so a future change
        to it is deliberate and visible."""
        from abicheck.bundle import _STORED_SNAPSHOT_MAX_LEADING_WHITESPACE

        path = tmp_path / "pathological.json"
        path.write_bytes(
            b"\n" * (_STORED_SNAPSHOT_MAX_LEADING_WHITESPACE + 8192) + b'{"elf": {}}'
        )
        assert _looks_like_stored_snapshot_file(path) is False


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


class TestRecoveredLibraryIdentity:
    """Codex review, PR #1269 (P1): a release key is canonicalized, so a
    file-backed member must recover the *represented* filename from its own
    document or a sibling's `DT_NEEDED` stops resolving.

    The failure this guards is not "a field is missing" but "a real
    intra-bundle dependency edge is classified as external", so it is
    asserted at the graph, not only at the reader.
    """

    def test_versioned_dt_needed_resolves_to_the_canonicalized_key(
        self, tmp_path: Path
    ) -> None:
        # The consumer records DT_NEEDED against the *versioned* filename...
        consumer = _snapshot("libcons.so", needed=("libprov.so.1",))
        # ...while the provider's release key is the canonicalized name, and
        # the provider has no DT_SONAME of its own to fall back on.
        provider = AbiSnapshot(
            library="libprov.so.1",
            version="1",
            functions=[],
            variables=[],
            types=[],
            elf=ElfMetadata(soname="", symbols=[ElfSymbol(name="_Z4provv")]),
        )
        libraries = {
            "libcons.so": _write(consumer, tmp_path / "c.abicheck.json", "zstd"),
            "libprov.so": _write(provider, tmp_path / "p.abicheck.json", "zstd"),
        }

        snap = build_bundle_snapshot_mixed(libraries)

        # The user-facing consequence, not the lookup table: the edge must
        # be intra-bundle, never "extra" (external).
        assert "libprov.so.1" in snap.resolution.intra_needed.get("libcons.so", ()), (
            "the provider's represented filename was discarded, so the "
            "consumer's DT_NEEDED=libprov.so.1 resolved to nothing: "
            f"intra={snap.resolution.intra_needed}, "
            f"extra={snap.resolution.extra_needed}"
        )
        assert "libprov.so.1" not in snap.resolution.extra_needed.get("libcons.so", ())
        assert snap.resolution.soname_to_name["libprov.so.1"] == "libprov.so"

    def test_filename_is_recovered_from_the_document(self, tmp_path: Path) -> None:
        path = _write(
            _snapshot("libnamed.so.2"), tmp_path / "renamed.abicheck.json", "none"
        )
        _elf, filename = _stored_file_snapshot_evidence(path)
        # The oracle is the snapshot's own recorded library name -- not the
        # file it happens to be stored under, which differs here on purpose.
        assert filename == Path("libnamed.so.2")


class TestRecoveredFilenameIsUntrustedInput:
    """The recovered name is whatever a document on disk claims, and it lands
    in a path mapping -- so the predicate is tested directly against hostile
    values, not only through a well-formed writer's output."""

    @pytest.mark.parametrize(
        "value",
        [
            "../../etc/passwd",
            "/etc/passwd",
            "a/b.so",
            "a\\b.so",
            "..",
            ".",
            "",
            "   ",
        ],
    )
    def test_path_bearing_or_empty_names_are_rejected(self, value: str) -> None:
        assert _stored_document_library_filename({"library": value}) is None

    @pytest.mark.parametrize("value", [None, 42, ["libx.so"], {"name": "libx.so"}])
    def test_non_string_names_are_rejected(self, value: object) -> None:
        assert _stored_document_library_filename({"library": value}) is None

    def test_absent_key_is_rejected(self) -> None:
        assert _stored_document_library_filename({}) is None

    @pytest.mark.parametrize(
        "value", ["libx.so", "libx.so.1", "libx.so.1.2.3", "lib-x_y+z.so"]
    )
    def test_plain_basenames_are_accepted(self, value: str) -> None:
        assert _stored_document_library_filename({"library": value}) == Path(value)


class TestSchemaCeiling:
    """Codex review, PR #1269 (P1): reading the `elf` section directly
    bypassed the ADR-050 D1 hard-rejection ceiling every other snapshot
    reader applies, admitting a future-schema document into the bundle graph
    on partially interpreted evidence.

    The oracle is `storage.snapshot_schema_versions`' own constants, derived
    independently of the branch under test, so this cannot pass by agreeing
    with a hardcoded copy of the number.
    """

    @staticmethod
    def _document(schema_version: int) -> str:
        return json.dumps(
            {
                "library": "libfuture.so",
                "schema_version": schema_version,
                "elf": {"soname": "libfuture.so.1"},
            }
        )

    def test_future_schema_is_not_admitted(self, tmp_path: Path) -> None:
        from abicheck.storage.snapshot_schema_versions import SCHEMA_VERSION

        path = tmp_path / "future.abicheck.json"
        path.write_text(self._document(SCHEMA_VERSION + 1))

        assert _stored_file_snapshot_evidence(path) == (None, None)
        assert (
            "libfuture.so"
            not in build_bundle_snapshot_mixed({"libfuture.so": path}).metadata
        )

    def test_the_current_schema_is_admitted(self, tmp_path: Path) -> None:
        """The complement, so a ceiling accidentally set one too low -- which
        would reject every snapshot this build itself writes -- fails here
        rather than passing as 'nothing was admitted'."""
        from abicheck.storage.snapshot_schema_versions import SCHEMA_VERSION

        path = tmp_path / "current.abicheck.json"
        path.write_text(self._document(SCHEMA_VERSION))

        elf, _filename = _stored_file_snapshot_evidence(path)
        assert elf is not None
        assert elf.soname == "libfuture.so.1"

    def test_older_schemas_are_admitted(self, tmp_path: Path) -> None:
        """A stored snapshot from an older abicheck is an ordinary operand --
        the ceiling rejects *newer*, never older."""
        from abicheck.storage.snapshot_schema_versions import SCHEMA_VERSION

        for older in (1, SCHEMA_VERSION // 2, SCHEMA_VERSION - 1):
            path = tmp_path / f"old-{older}.abicheck.json"
            path.write_text(self._document(older))
            elf, _ = _stored_file_snapshot_evidence(path)
            assert elf is not None, f"schema_version {older} was rejected"

    def test_below_the_hard_rejection_threshold_still_decodes(
        self, tmp_path: Path
    ) -> None:
        """The rule mirrors `decode_snapshot`'s two tiers exactly: a version
        newer than this build but *below* the ADR-050 threshold warns and
        decodes rather than being refused. Asserted so this reader cannot
        drift into a stricter third rule of its own."""
        from abicheck.storage.snapshot_schema_versions import (
            _MIN_SCHEMA_VERSION_REQUIRING_HARD_REJECTION,
            SCHEMA_VERSION,
        )

        below = _MIN_SCHEMA_VERSION_REQUIRING_HARD_REJECTION - 1
        if below <= 0 or below <= SCHEMA_VERSION:
            pytest.skip(
                "this build's SCHEMA_VERSION is already at or past the "
                "hard-rejection threshold, so the warn tier is unreachable"
            )
        path = tmp_path / "warn.abicheck.json"
        path.write_text(self._document(below))
        elf, _ = _stored_file_snapshot_evidence(path)
        assert elf is not None


class TestDiscoveryAndBundleSniffAgree:
    """Codex review, PR #1269 (P2): the bundle sniff is not the first gate a
    release member passes -- `workflows.release_inputs.collect_release_inputs`
    filters the directory first, through `classify.AbiJsonClassifier`, which
    reads a bounded 4096-byte probe of its own.

    The invariant that actually matters is the *relationship* between the two,
    and its direction: whatever discovery hands downstream, the bundle sniff
    must accept. A member surviving discovery only to be dropped by the sniff
    is the silent-pass failure this module exists to prevent; the reverse
    (the sniff being more permissive than discovery) costs nothing, because
    the sniff never sees a file discovery already refused.

    Pinned here through the *real* discovery function rather than by
    comparing two constants, so widening either side without the other fails.
    """

    def _release_dir(self, tmp_path: Path) -> Path:
        release = tmp_path / "release"
        release.mkdir()
        _write(_snapshot("liba.so.1"), release / "a.abicheck.json", "none")
        _write(_snapshot("libb.so.1"), release / "b.abicheck.json", "gzip")
        _write(_snapshot("libc.so.1"), release / "c.abicheck.json", "zstd")
        _write(_snapshot("libd.so.1"), release / "d.abicheck.json", "auto")
        # Non-members that must not be discovered either way.
        (release / "README.md").write_text("# notes\n")
        (release / "manifest.txt").write_text("liba.so.1\n")
        return release

    def test_everything_discovery_accepts_the_bundle_sniff_accepts(
        self, tmp_path: Path
    ) -> None:
        from abicheck.workflows.release_inputs import collect_release_inputs

        release = self._release_dir(tmp_path)
        discovered = collect_release_inputs(release)

        assert discovered, "discovery found nothing -- test would be vacuous"
        rejected = [
            p.name for p in discovered if not _looks_like_stored_snapshot_file(p)
        ]
        assert rejected == [], (
            "discovery handed these members downstream but the bundle sniff "
            f"refuses them, so they are dropped from the graph: {rejected}"
        )

    def test_discovered_members_all_resolve_into_the_graph(
        self, tmp_path: Path
    ) -> None:
        """The end-to-end consequence of the same relationship: each
        discovered member reaches `metadata`, not just the predicate."""
        from abicheck.workflows.release_inputs import collect_release_inputs

        release = self._release_dir(tmp_path)
        discovered = collect_release_inputs(release)

        snap = build_bundle_snapshot_mixed({p.stem: p for p in discovered})
        assert sorted(snap.metadata) == sorted(p.stem for p in discovered)


class TestUpstreamDiscoveryProbeBoundCanary:
    """Canary for the tracked residual on
    `evidence.operand_shape_silently_unresolved`
    (`tests/regressions/manifest_evidence.py`).

    It asserts the *residual's own bound* rather than the eventually-correct
    behaviour, which is what makes it fail loudly in **either** direction
    without an xfail: if discovery widens (the gap closes) or narrows (the
    gap widens), this test breaks and the registry entry must be revisited.

    The residual: `classify.AbiJsonClassifier` reads a bounded probe, so a
    valid uncompressed snapshot padded with more leading JSON whitespace
    than that probe is dropped at discovery -- upstream of the bundle sniff,
    which would accept it. Closing this means changing a tool-wide input
    classifier, which governs far more than bundle members, so it is
    tracked rather than fixed here.
    """

    def test_discovery_still_drops_a_whitespace_padded_snapshot(
        self, tmp_path: Path
    ) -> None:
        from abicheck.classify import AbiJsonClassifier
        from abicheck.workflows.release_inputs import collect_release_inputs

        release = tmp_path / "release"
        release.mkdir()
        # A control member, so discovery succeeds and the assertion below is
        # about *this* file rather than about an empty directory.
        _write(_snapshot("libok.so.1"), release / "ok.abicheck.json", "none")

        padded = release / "padded.abicheck.json"
        padded.write_bytes(
            b"\n" * (AbiJsonClassifier._JSON_PROBE_BYTES + 1)
            + (release / "ok.abicheck.json").read_bytes()
        )

        discovered = {p.name for p in collect_release_inputs(release)}

        assert "ok.abicheck.json" in discovered, "control member was not discovered"
        assert "padded.abicheck.json" not in discovered, (
            "discovery now accepts a whitespace-padded snapshot -- the "
            "residual tracked on evidence.operand_shape_silently_unresolved "
            "has closed; update that registry entry and drop this canary"
        )
        # ...while the bundle sniff would have accepted it. That asymmetry is
        # the gap, stated executably rather than only in prose.
        assert _looks_like_stored_snapshot_file(padded) is True
