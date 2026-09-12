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

"""One CLI input per evidence *role* (plan Phase 7n), at three levels.

``--debug-root`` merged into ``--debug-info``, ``--devel-pkg`` into
``-H/--header``, and ``--probe-matrix`` into ``--build-info``. What has to
hold, and what this file states:

1. **The classifier is content-only.** Exhaustive over shape x filename,
   with each shape's *conventional* name among the misleading ones, so a
   suffix-based implementation fails rather than coincidentally passing
   (AGENTS.md's primitive-level property-test rule).
2. **The splitter preserves every transport's destination and side
   ownership**, against an oracle derived from the pre-merge flags'
   documented semantics rather than from the splitter's own helpers.
3. **The pipeline behind the classifier accepts what it was handed** --
   asserted by running the whole public invocation under non-conventional
   names. That corollary is the registered bug class
   ``cli_surface.name_independent_dispatch_undone_downstream``
   (`tests/regressions/manifest.py`, fixed by PR #1242): proving a
   classifier name-independent proves nothing about the pipeline behind
   it, and here the pipeline is `package.detect_extractor`, whose own
   format detection had to become content-first for this merge to mean
   anything.

Transport *equivalence* is the fourth claim, and the one that says the
merge lost nothing: the same evidence supplied through two different
transports of one role produces the same findings. Stated with an
independent oracle (the other transport's run), not against a recorded
expectation.
"""

from __future__ import annotations

import io
import itertools
import json
import struct
import tarfile
import zipfile
from pathlib import Path

import pytest
from click.testing import CliRunner, Result

from abicheck.cli import main
from abicheck.errors import ExtractionSecurityError
from abicheck.frontends.cli.options.evidence_roles import (
    split_build_evidence,
    split_debug_evidence,
    split_header_evidence,
    unsided_debug_packages,
)
from abicheck.model import AbiSnapshot, Function, Visibility
from abicheck.package import detect_extractor, is_package
from abicheck.serialization import snapshot_to_json
from abicheck.workflows.evidence_transport import (
    PROBE_MATRIX_SCHEMA,
    BuildInfoTransport,
    DebugTransport,
    HeaderTransport,
    classify_build_info_transport,
    classify_debug_transport,
    classify_header_transport,
)

# ── shape builders: content only, the caller picks the name ──────────────────

_RPM_MAGIC = b"\xed\xab\xee\xdb"
_DEB_MAGIC = b"!<arch>\n"


def _tar_bytes(members: dict[str, bytes], *, mode: str = "w:gz") -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode=mode) as tf:  # type: ignore[call-overload]
        for name, data in members.items():
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def _write_tar(path: Path, members: dict[str, bytes], *, mode: str = "w:gz") -> Path:
    path.write_bytes(_tar_bytes(members, mode=mode))
    return path


def _write_zip(path: Path, members: dict[str, bytes]) -> Path:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    path.write_bytes(buf.getvalue())
    return path


def _write_wheel(path: Path) -> Path:
    return _write_zip(
        path,
        {
            "foo-1.0.dist-info/WHEEL": b"Wheel-Version: 1.0\n",
            "foo-1.0.dist-info/METADATA": b"Name: foo\n",
            "include/foo.h": b"int foo(void);\n",
        },
    )


def _write_conda(path: Path) -> Path:
    return _write_zip(
        path,
        {
            "metadata.json": b"{}",
            "pkg-foo-1.0.tar.zst": b"\x28\xb5\x2f\xfd" + b"\x00" * 8,
            "info-foo-1.0.tar.zst": b"\x28\xb5\x2f\xfd" + b"\x00" * 8,
        },
    )


def _write_rpm(path: Path) -> Path:
    path.write_bytes(_RPM_MAGIC + b"\x00" * 64)
    return path


def _write_deb(path: Path) -> Path:
    path.write_bytes(_DEB_MAGIC + b"\x00" * 64)
    return path


def _elf_with_sections(
    path: Path, section_names: list[str], *, build_id: str | None = None
) -> Path:
    """A minimal, real 64-bit little-endian ELF carrying *section_names*.

    Hand-assembled rather than compiled so the debug-artifact classifier's
    contract can be stated in the fast unit lane: what it reads is the
    section-header table, which is exactly what this builds. *build_id* adds
    a real ``.note.gnu.build-id`` note section, so the identity check can be
    exercised against `extract_build_id`'s actual parse rather than a stand-in.
    """
    sections: list[tuple[str, int, bytes]] = [
        (name, 1, b"\x00" * 16) for name in section_names
    ]
    if build_id is not None:
        desc = bytes.fromhex(build_id)
        note = (
            struct.pack("<III", 4, len(desc), 3)  # namesz, descsz, NT_GNU_BUILD_ID
            + b"GNU\x00"
            + desc
            + b"\x00" * (-len(desc) % 4)
        )
        sections.append((".note.gnu.build-id", 7, note))  # SHT_NOTE

    shstrtab = b"\x00" + b"".join(n.encode() + b"\x00" for n, _t, _c in sections)
    ehsize, shentsize = 64, 64

    # Layout: ehdr | each section's content | shstrtab | section headers.
    body = bytearray()
    offsets: list[tuple[int, int]] = []
    for _n, _t, content in sections:
        offsets.append((ehsize + len(body), len(content)))
        body += content
    shstrtab_off = ehsize + len(body)
    body += shstrtab
    sh_off = ehsize + len(body)

    header = bytearray(ehsize)
    header[0:4] = b"\x7fELF"
    header[4] = 2  # ELFCLASS64
    header[5] = 1  # ELFDATA2LSB
    header[6] = 1  # EV_CURRENT
    header[16:18] = struct.pack("<H", 3)  # ET_DYN
    header[18:20] = struct.pack("<H", 62)  # EM_X86_64
    header[20:24] = struct.pack("<I", 1)
    header[40:48] = struct.pack("<Q", sh_off)
    header[52:54] = struct.pack("<H", ehsize)
    header[58:60] = struct.pack("<H", shentsize)
    header[60:62] = struct.pack("<H", len(sections) + 2)
    header[62:64] = struct.pack("<H", len(sections) + 1)

    shdrs = [bytes(shentsize)]  # SHT_NULL
    name_off = 1
    for (name, sh_type, _c), (off, size) in zip(sections, offsets, strict=True):
        shdrs.append(
            struct.pack(
                "<IIQQQQIIQQ", name_off, sh_type, 0, 0, off, size, 0, 0, 1, 0
            )
        )
        name_off += len(name) + 1
    # The .shstrtab section itself, last, as the header's shstrndx says.
    shdrs.append(
        struct.pack(
            "<IIQQQQIIQQ", 0, 3, 0, 0, shstrtab_off, len(shstrtab), 0, 0, 1, 0
        )
    )
    path.write_bytes(bytes(header) + bytes(body) + b"".join(shdrs))
    return path


def _write_probe_matrix(path: Path, *, tagged: bool = True, floor: int = 17) -> Path:
    doc: dict[str, object] = {
        "library": "libfoo.so",
        "version": "1.0",
        "spec_name": "probes",
        "cxx_stds": {"gcc-13": floor},
        "defaults": {},
        "results": [],
    }
    if tagged:
        doc = {"schema": PROBE_MATRIX_SCHEMA, **doc}
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path


def _write_compile_db(path: Path) -> Path:
    path.write_text(
        json.dumps([{"directory": "/b", "command": "cc -c a.c", "file": "a.c"}]),
        encoding="utf-8",
    )
    return path


#: Every recognized shape, keyed by the transport each role must classify it
#: to. Content only -- no filename appears here on purpose.
SHAPE_BUILDERS: dict[str, object] = {
    "rpm": _write_rpm,
    "deb": _write_deb,
    "tar_gz": lambda p: _write_tar(p, {"lib/libfoo.so": b"\x7fELF"}),
    "tar_plain": lambda p: _write_tar(p, {"lib/libfoo.so": b"\x7fELF"}, mode="w"),
    "wheel": _write_wheel,
    "conda": _write_conda,
}

#: Shapes that are *not* packages, with the debug/header/build transport each
#: must fall through to.
NON_PACKAGE_BUILDERS: dict[str, object] = {
    "detached_dwarf": lambda p: _elf_with_sections(p, [".debug_info", ".debug_abbrev"]),
    "probe_matrix": _write_probe_matrix,
    "compile_db": _write_compile_db,
}

#: Names chosen to disagree with the content they hold -- including each
#: shape's own conventional name, so a name-based implementation fails
#: rather than coincidentally passing.
MISLEADING_NAMES: tuple[str, ...] = (
    "evidence",
    "libfoo-dbg.rpm",
    "libfoo-dev.deb",
    "debug.tar.gz",
    "foo-1.0-py3-none-any.whl",
    "foo-1.0-h123-0.conda",
    "compile_commands.json",
    "probes.json",
    "libfoo.so.debug",
    "NOTES",
)


def _run(*args: str) -> Result:
    return CliRunner().invoke(main, list(args))


# ── 1. the classifier: content only ──────────────────────────────────────────


class TestTransportClassificationIsContentOnly:
    """Exhaustive over shape x filename, for all three roles."""

    @pytest.mark.parametrize(
        ("shape", "name"), list(itertools.product(SHAPE_BUILDERS, MISLEADING_NAMES))
    )
    def test_a_package_classifies_as_a_package_under_any_name(
        self, tmp_path: Path, shape: str, name: str
    ) -> None:
        path = SHAPE_BUILDERS[shape](tmp_path / name)  # type: ignore[operator]
        assert classify_debug_transport(path) is DebugTransport.PACKAGE
        assert classify_header_transport(path) is HeaderTransport.PACKAGE

    @pytest.mark.parametrize(
        ("shape", "name"),
        list(itertools.product(NON_PACKAGE_BUILDERS, MISLEADING_NAMES)),
    )
    def test_a_non_package_is_never_read_as_a_package(
        self, tmp_path: Path, shape: str, name: str
    ) -> None:
        path = NON_PACKAGE_BUILDERS[shape](tmp_path / name)  # type: ignore[operator]
        assert classify_debug_transport(path) is DebugTransport.DETACHED_FILE
        assert classify_header_transport(path) is HeaderTransport.HEADERS

    @pytest.mark.parametrize("name", MISLEADING_NAMES)
    def test_a_probe_matrix_is_recognized_under_any_name(
        self, tmp_path: Path, name: str
    ) -> None:
        path = _write_probe_matrix(tmp_path / name)
        assert classify_build_info_transport(path) is BuildInfoTransport.PROBE_MATRIX

    @pytest.mark.parametrize("name", MISLEADING_NAMES)
    def test_a_compile_database_is_never_read_as_a_matrix(
        self, tmp_path: Path, name: str
    ) -> None:
        path = _write_compile_db(tmp_path / name)
        assert (
            classify_build_info_transport(path) is BuildInfoTransport.COMPILE_CONTEXT
        )

    @pytest.mark.parametrize("name", MISLEADING_NAMES)
    def test_a_directory_is_always_the_searchable_transport(
        self, tmp_path: Path, name: str
    ) -> None:
        d = tmp_path / name
        d.mkdir()
        assert classify_debug_transport(d) is DebugTransport.ROOT
        assert classify_header_transport(d) is HeaderTransport.HEADERS
        assert classify_build_info_transport(d) is BuildInfoTransport.COMPILE_CONTEXT

    @pytest.mark.parametrize("name", MISLEADING_NAMES)
    def test_a_nonexistent_path_keeps_its_pre_merge_reading(
        self, tmp_path: Path, name: str
    ) -> None:
        """``--debug-root`` and ``-H`` both accepted a path that isn't there;
        turning that into a hard error would reject a working invocation."""
        path = tmp_path / name
        assert classify_debug_transport(path) is DebugTransport.ROOT
        assert classify_header_transport(path) is HeaderTransport.HEADERS

    def test_a_tagless_matrix_still_classifies(self, tmp_path: Path) -> None:
        """A snapshot captured before Phase 7n's `schema` tag existed."""
        path = _write_probe_matrix(tmp_path / "m.json", tagged=False)
        assert "schema" not in json.loads(path.read_text())
        assert classify_build_info_transport(path) is BuildInfoTransport.PROBE_MATRIX

    def test_a_json_object_missing_a_discriminator_is_not_a_matrix(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / "m.json"
        path.write_text(json.dumps({"library": "libfoo.so", "version": "1.0"}))
        assert (
            classify_build_info_transport(path) is BuildInfoTransport.COMPILE_CONTEXT
        )

    def test_a_binary_operand_is_not_a_matrix(self, tmp_path: Path) -> None:
        path = _elf_with_sections(tmp_path / "libfoo.so", [".text"])
        assert (
            classify_build_info_transport(path) is BuildInfoTransport.COMPILE_CONTEXT
        )

    def test_a_detached_debug_file_without_debug_sections_is_not_one(
        self, tmp_path: Path
    ) -> None:
        """The classifier says "detached file"; the *resolver* is what decides
        there is no evidence in it -- see
        `TestDetachedDebugResolution::test_a_file_with_no_debug_evidence_is_skipped`.
        """
        from abicheck.extract.detached_debug import classify_detached_debug_file

        path = _elf_with_sections(tmp_path / "libfoo.so", [".text", ".dynsym"])
        assert classify_detached_debug_file(path) is None

    def test_debug_artifact_kinds_are_read_from_sections_and_magic(
        self, tmp_path: Path
    ) -> None:
        from abicheck.extract.detached_debug import classify_detached_debug_file

        dwarf = _elf_with_sections(tmp_path / "plain-name", [".debug_info"])
        compressed = _elf_with_sections(tmp_path / "z", [".zdebug_info"])
        dwp = _elf_with_sections(tmp_path / "pkg", [".debug_info.dwo"])
        pdb = tmp_path / "not-a-pdb-name"
        pdb.write_bytes(b"Microsoft C/C++ MSF 7.00" + b"\x00" * 16)
        assert classify_detached_debug_file(dwarf) == "dwarf"
        assert classify_detached_debug_file(compressed) == "dwarf"
        assert classify_detached_debug_file(dwp) == "dwp"
        assert classify_detached_debug_file(pdb) == "pdb"


# ── 2. the pipeline behind it: the registered bug class ──────────────────────


class TestPackagePipelineIsNameIndependent:
    """`is_package` and `detect_extractor` answer from content too.

    The classifier deciding "this is a package" is worth nothing if the
    extractor lookup behind it then answers `None` because the filename has
    no known suffix -- that is bug class
    ``cli_surface.name_independent_dispatch_undone_downstream`` exactly, one
    layer down. So the two predicates the pipeline actually calls are
    asserted over the same shape x name matrix.
    """

    @pytest.mark.parametrize(
        ("shape", "name"), list(itertools.product(SHAPE_BUILDERS, MISLEADING_NAMES))
    )
    def test_every_package_shape_resolves_an_extractor_under_any_name(
        self, tmp_path: Path, shape: str, name: str
    ) -> None:
        path = SHAPE_BUILDERS[shape](tmp_path / name)  # type: ignore[operator]
        assert is_package(path)
        assert detect_extractor(path) is not None

    @pytest.mark.parametrize("shape", sorted(SHAPE_BUILDERS))
    def test_the_extractor_chosen_does_not_depend_on_the_name(
        self, tmp_path: Path, shape: str
    ) -> None:
        """Same bytes, two names -> the same extractor class.

        The oracle is the *other* name's answer, so this cannot pass by
        agreeing with a recorded table that was itself derived from
        suffixes.
        """
        conventional = SHAPE_BUILDERS[shape](tmp_path / f"a-{shape}")  # type: ignore[operator]
        renamed = tmp_path / "z"
        renamed.write_bytes(Path(conventional).read_bytes())
        assert type(detect_extractor(renamed)) is type(detect_extractor(conventional))

    @pytest.mark.parametrize(
        "content",
        [b"", b"not an archive at all\n", b"\x1f\x8b\x08\x00", b"\x7fELF\x02\x01\x01"],
    )
    def test_a_non_archive_never_reads_as_a_package(
        self, tmp_path: Path, content: bytes
    ) -> None:
        """Including a *truncated* gzip header, whose decompressor raises
        EOFError rather than a TarError -- a format sniff must answer "not
        this format" for it, not propagate out of detection."""
        path = tmp_path / "blob"
        path.write_bytes(content)
        assert not is_package(path)


# ── 3. the splitter: destinations, sides, and both roles at once ─────────────


class TestSplitterPreservesDestinationsAndSides:
    """The oracle is the pre-merge flags' documented semantics, restated.

    ``--debug-root``/``-H`` were repeatable three-bucket families (a bare
    value means both sides, ``old=``/``new=`` add to one); ``--debug-info``/
    ``--devel-pkg``/``--probe-matrix``/``--build-info`` were one-per-side,
    last-wins, with a bare value setting both.
    """

    def test_debug_transports_land_in_their_own_destinations(
        self, tmp_path: Path
    ) -> None:
        root = tmp_path / "dbg-tree"
        root.mkdir()
        sidecar = _elf_with_sections(tmp_path / "sidecar", [".debug_info"])
        pkg = _write_rpm(tmp_path / "dbg-blob")
        out = split_debug_evidence(
            [("both", root), ("old", sidecar), ("new", pkg)]
        )
        assert out["debug_roots"] == (root,)
        assert out["debug_roots_old"] == (sidecar,)
        assert out["debug_roots_new"] == ()
        assert out["debug_info1"] is None
        assert out["debug_info2"] == pkg

    def test_a_bare_debug_package_fans_out_to_both_sides(self, tmp_path: Path) -> None:
        pkg = _write_deb(tmp_path / "blob")
        out = split_debug_evidence([("both", pkg)])
        assert out["debug_info1"] == pkg and out["debug_info2"] == pkg
        assert out["debug_roots"] == ()

    def test_the_last_package_per_side_wins(self, tmp_path: Path) -> None:
        first = _write_rpm(tmp_path / "one")
        second = _write_rpm(tmp_path / "two")
        out = split_debug_evidence([("old", first), ("old", second)])
        assert out["debug_info1"] == second

    def test_header_transports_land_in_their_own_destinations(
        self, tmp_path: Path
    ) -> None:
        header = tmp_path / "foo.h"
        header.write_text("int foo(void);\n")
        pkg = _write_wheel(tmp_path / "devel-blob")
        out = split_header_evidence([("both", header), ("old", pkg)])
        assert out["headers"] == (header,)
        assert out["old_headers_only"] == ()
        assert out["devel_pkg1"] == pkg and out["devel_pkg2"] is None

    def test_build_evidence_carries_both_roles_for_one_side(
        self, tmp_path: Path
    ) -> None:
        """The capability the merge must not cost: probe observations and
        compile context, together, for the same side, still told apart."""
        matrix = _write_probe_matrix(tmp_path / "ctx.json")
        build_dir = tmp_path / "build"
        build_dir.mkdir()
        out = split_build_evidence([("old", matrix), ("old", build_dir)])
        assert out["probe_matrix_old"] == matrix
        assert out["old_build_info"] == build_dir
        assert out["probe_matrix_new"] is None and out["new_build_info"] is None

    def test_two_identical_old_scoped_values_are_two_values(
        self, tmp_path: Path
    ) -> None:
        """A membership-test partition would drop both from the complement."""
        root = tmp_path / "dbg"
        root.mkdir()
        out = split_debug_evidence([("old", root), ("old", root)])
        assert out["debug_roots_old"] == (root, root)

    def test_dump_rejection_set_is_exactly_the_packages(self, tmp_path: Path) -> None:
        root = tmp_path / "dbg"
        root.mkdir()
        sidecar = _elf_with_sections(tmp_path / "s", [".debug_info"])
        pkg = _write_rpm(tmp_path / "p")
        assert unsided_debug_packages((root, sidecar, pkg)) == (pkg,)


# ── 4. the detached-debug resolver, including its identity check ─────────────


class TestDetachedDebugResolution:
    def test_a_named_sidecar_is_used_as_the_artifact(self, tmp_path: Path) -> None:
        from abicheck.debug_resolver import resolve_debug_info

        binary = _elf_with_sections(tmp_path / "libfoo.so", [".text"])
        sidecar = _elf_with_sections(tmp_path / "no-suffix-at-all", [".debug_info"])
        artifact = resolve_debug_info(binary, debug_roots=[sidecar])
        assert artifact is not None
        assert artifact.dwarf_path == sidecar

    def test_a_file_with_no_debug_evidence_is_skipped(self, tmp_path: Path) -> None:
        from abicheck.debug_resolver import resolve_debug_info

        binary = _elf_with_sections(tmp_path / "libfoo.so", [".text"])
        junk = tmp_path / "junk"
        junk.write_bytes(b"not an elf")
        assert resolve_debug_info(binary, debug_roots=[junk]) is None

    def test_a_directory_root_still_resolves_the_old_way(self, tmp_path: Path) -> None:
        """The merged input did not disturb the build-id-tree transport."""
        from abicheck.debug_resolver import resolve_debug_info

        binary = _elf_with_sections(tmp_path / "libfoo.so", [".text"])
        build_id = "ab" + "cd" * 8
        root = tmp_path / "dbg"
        (root / ".build-id" / "ab").mkdir(parents=True)
        found = root / ".build-id" / "ab" / f"{build_id[2:]}.debug"
        found.write_bytes(b"\x7fELF")
        artifact = resolve_debug_info(binary, debug_roots=[root], build_id=build_id)
        assert artifact is not None and artifact.dwarf_path == found

    def test_a_mismatched_build_id_is_refused(self, tmp_path: Path) -> None:
        """A sidecar describing a *different* build is not this binary's
        debug info, and using it would describe the wrong ABI.

        Against real ``.note.gnu.build-id`` notes on both sides, so what is
        exercised is `extract_build_id`'s own parse and the comparison over
        it -- an earlier version stubbed the check and therefore only tested
        that its caller honours a `True`.
        """
        from abicheck.extract.detached_debug import DetachedDebugFileResolver

        binary = _elf_with_sections(tmp_path / "libfoo.so", [".text"], build_id="ab" * 10)
        sidecar = _elf_with_sections(
            tmp_path / "other", [".debug_info"], build_id="cd" * 10
        )
        resolver = DetachedDebugFileResolver()
        assert resolver.resolve(binary, build_id="ab" * 10, debug_roots=[sidecar]) is None

    def test_a_matching_build_id_is_accepted(self, tmp_path: Path) -> None:
        """The negative control: the check refuses a mismatch, not everything."""
        from abicheck.extract.detached_debug import DetachedDebugFileResolver

        binary = _elf_with_sections(tmp_path / "libfoo.so", [".text"], build_id="ab" * 10)
        sidecar = _elf_with_sections(
            tmp_path / "s", [".debug_info"], build_id="ab" * 10
        )
        resolved = DetachedDebugFileResolver().resolve(
            binary, build_id="ab" * 10, debug_roots=[sidecar]
        )
        assert resolved is not None and resolved.dwarf_path == sidecar

    def test_absent_build_id_evidence_never_manufactures_a_mismatch(
        self, tmp_path: Path
    ) -> None:
        """Neither side carrying a note proves nothing either way, so the
        sidecar is used: this validates what it observes and never invents
        a conflict out of missing evidence."""
        from abicheck.extract.detached_debug import DetachedDebugFileResolver

        binary = _elf_with_sections(tmp_path / "libfoo.so", [".text"])
        sidecar = _elf_with_sections(tmp_path / "s", [".debug_info"])
        assert not DetachedDebugFileResolver._build_id_conflict(binary, sidecar, None)
        assert not DetachedDebugFileResolver._build_id_conflict(binary, sidecar, "zz")
        # ...and the binary's id is known but the sidecar carries none.
        assert not DetachedDebugFileResolver._build_id_conflict(
            binary, sidecar, "ab" * 10
        )

    @pytest.mark.parametrize(
        "shape", ["directory", "missing", "dangling-symlink", "empty", "truncated-elf"]
    )
    def test_classification_degrades_instead_of_raising(
        self, tmp_path: Path, shape: str
    ) -> None:
        """A classifier runs on whatever the user typed, so every way an
        operand can be unusable answers "not a debug artifact" rather than
        propagating out of a classification step."""
        from abicheck.extract.detached_debug import classify_detached_debug_file

        target = tmp_path / shape
        if shape == "directory":
            target.mkdir()
        elif shape == "empty":
            target.touch()
        elif shape == "truncated-elf":
            target.write_bytes(b"\x7fELF\x02\x01\x01")
        elif shape == "dangling-symlink":
            target.symlink_to(tmp_path / "nothing-here")
        assert classify_detached_debug_file(target) is None


class TestDocumentClassificationDegradesGracefully:
    """`--build-info`'s document sniff has the same obligation."""

    @pytest.mark.parametrize(
        "content", [b"", b"   \n\t ", b"\x7fELF", b"{not json", b"null", b"[]"]
    )
    def test_an_unusable_document_is_not_a_matrix(
        self, tmp_path: Path, content: bytes
    ) -> None:
        path = tmp_path / "operand"
        path.write_bytes(content)
        assert (
            classify_build_info_transport(path) is BuildInfoTransport.COMPILE_CONTEXT
        )

    def test_an_unopenable_document_is_not_a_matrix(self, tmp_path: Path) -> None:
        """The OSError path, reached portably: a dangling symlink. (A
        chmod-based fixture proves nothing in a root container, which is
        what CI runs in.)"""
        path = tmp_path / "operand"
        path.symlink_to(tmp_path / "nothing-here")
        assert (
            classify_build_info_transport(path) is BuildInfoTransport.COMPILE_CONTEXT
        )


class TestUnconsumableTransportsAreRefused:
    """A named PDB or DWARF-package file is refused, not silently ignored.

    Codex review, PR #1253: the ELF dump reads only
    ``DebugArtifact.dwarf_path`` and the PE dump never consults
    ``debug_roots``, so resolving one of these would have meant a stripped
    binary compared with none of the requested debug evidence and reported
    clean. Worse still, this resolver runs *first*, so it would have ended
    the chain before ``EmbeddedDwarfResolver`` ever looked at the binary's
    own DWARF.
    """

    @pytest.mark.parametrize("kind", ["pdb", "dwp"])
    def test_the_resolver_yields_to_the_chain_instead_of_ending_it(
        self, tmp_path: Path, kind: str
    ) -> None:
        """The false-clean path, closed at its source.

        This resolver is first in the chain, so returning an artifact no
        extraction path reads would have stopped `EmbeddedDwarfResolver`
        from ever looking at the binary's own DWARF. Answering ``None``
        is what lets the rest of the chain run.
        """
        from abicheck.extract.detached_debug import DetachedDebugFileResolver

        binary = _elf_with_sections(tmp_path / "libfoo.so", [".debug_info"])
        artifact = tmp_path / "sidecar"
        if kind == "pdb":
            artifact.write_bytes(b"Microsoft C/C++ MSF 7.00" + b"\x00" * 16)
        else:
            _elf_with_sections(artifact, [".debug_info.dwo"])
        resolved = DetachedDebugFileResolver().resolve(binary, debug_roots=[artifact])
        assert resolved is None

    def test_a_named_dwarf_sidecar_is_still_returned(self, tmp_path: Path) -> None:
        """The negative control: narrowing to DWARF did not narrow to nothing."""
        from abicheck.extract.detached_debug import DetachedDebugFileResolver

        binary = _elf_with_sections(tmp_path / "libfoo.so", [".text"])
        sidecar = _elf_with_sections(tmp_path / "sidecar", [".debug_info"])
        resolved = DetachedDebugFileResolver().resolve(binary, debug_roots=[sidecar])
        assert resolved is not None and resolved.dwarf_path == sidecar

    @pytest.mark.parametrize("kind", ["pdb", "dwp"])
    @pytest.mark.parametrize("command", ["compare", "dump"])
    def test_naming_one_is_a_usage_error_on_both_commands(
        self, tmp_path: Path, command: str, kind: str
    ) -> None:
        artifact = tmp_path / "artifact-no-suffix"
        if kind == "pdb":
            artifact.write_bytes(b"Microsoft C/C++ MSF 7.00" + b"\x00" * 16)
        else:
            _elf_with_sections(artifact, [".debug_info.dwo"])
        old = _snapshot(tmp_path / "old.json", funcs=["foo"], version="1.0")
        new = _snapshot(tmp_path / "new.json", funcs=["foo"], version="2.0")
        operands = [str(old), str(new)] if command == "compare" else [
            str(_elf_with_sections(tmp_path / "libfoo.so", [".text"]))
        ]
        res = _run(command, *operands, "--debug-info", str(artifact))
        assert res.exit_code == 64, res.output
        assert "silently ignored" in res.output

    @pytest.mark.parametrize("command", ["compare", "dump"])
    def test_a_directory_of_pdbs_is_still_accepted(
        self, tmp_path: Path, command: str
    ) -> None:
        """The supported spelling the error names must keep working."""
        root = tmp_path / "pdbs"
        root.mkdir()
        (root / "libfoo.pdb").write_bytes(b"Microsoft C/C++ MSF 7.00")
        old = _snapshot(tmp_path / "old.json", funcs=["foo"], version="1.0")
        new = _snapshot(tmp_path / "new.json", funcs=["foo"], version="2.0")
        operands = [str(old), str(new)] if command == "compare" else [
            str(_elf_with_sections(tmp_path / "libfoo.so", [".text"])), "--dry-run"
        ]
        res = _run(command, *operands, "--debug-info", str(root))
        assert res.exit_code != 64, res.output


class TestContentSniffsDegradeGracefully:
    """Every sniff `package.py` gained runs during *detection*, so an
    unreadable or unsupported operand must answer "not this format" rather
    than propagate out and abort dispatch to every other extractor."""

    def test_an_unreadable_zip_yields_no_members(self, tmp_path: Path) -> None:
        from abicheck.package import _zip_entry_names

        broken = tmp_path / "broken"
        broken.write_bytes(b"PK\x03\x04 truncated right here")
        assert _zip_entry_names(broken) == []
        assert not is_package(broken)

    def test_a_corrupt_zstd_stream_is_not_a_tar(self, tmp_path: Path) -> None:
        from abicheck.package import looks_like_tar_container, looks_like_zstd

        path = tmp_path / "operand"
        path.write_bytes(b"\x28\xb5\x2f\xfd" + b"garbage" * 8)
        assert looks_like_zstd(path)
        assert not looks_like_tar_container(path)
        assert not is_package(path)

    def test_a_real_zstd_tar_is_recognized_under_any_name(
        self, tmp_path: Path
    ) -> None:
        """The negative control for the two above: the codec `tarfile` has
        none for is still detected, and from content rather than `.tar.zst`."""
        pytest.importorskip("zstandard")
        from _package_fixtures import _write_zstd_tar

        path = tmp_path / "evidence"
        _write_zstd_tar(path)
        assert is_package(path)
        assert classify_debug_transport(path) is DebugTransport.PACKAGE

    def test_magic_read_of_a_directory_is_not_a_crash(self, tmp_path: Path) -> None:
        from abicheck.package import looks_like_zip, looks_like_zstd

        d = tmp_path / "dir"
        d.mkdir()
        assert not looks_like_zip(d)
        assert not looks_like_zstd(d)


class TestClassificationHasNoPositionalWindow:
    """Neither role's classifier may make position part of the contract.

    Both defects here are the same shape and were found together (Codex
    review, PR #1253): a bounded read that happened to cover the
    discriminator in the fixtures, and not in a real artifact. So both are
    stated as "the discriminator is last", which is what a cap fails.
    """

    def test_a_wheel_whose_metadata_trails_its_payload_is_still_a_wheel(
        self, tmp_path: Path
    ) -> None:
        """Wheel builders commonly append `*.dist-info/` after the payload."""
        members = {f"foo/mod{i}.py": b"x" for i in range(500)}
        members["foo-1.0.dist-info/WHEEL"] = b"Wheel-Version: 1.0\n"
        pkg = _write_zip(tmp_path / "big-wheel", members)
        assert is_package(pkg)
        assert detect_extractor(pkg) is not None
        assert classify_header_transport(pkg) is HeaderTransport.PACKAGE

    def test_a_matrix_whose_results_precede_its_discriminators_classifies(
        self, tmp_path: Path
    ) -> None:
        """`load_matrix_snapshot` accepts any key order, so this must too."""
        path = tmp_path / "m.json"
        path.write_text(
            json.dumps(
                {
                    "results": [{"configuration_id": "c", "probe_id": "p"}] * 20000,
                    "library": "libfoo.so",
                    "version": "1.0",
                    "spec_name": "probes",
                }
            ),
            encoding="utf-8",
        )
        assert path.stat().st_size > 512 * 1024, "fixture must exceed any plausible window"
        assert classify_build_info_transport(path) is BuildInfoTransport.PROBE_MATRIX

    def test_a_huge_compile_database_is_still_not_a_matrix(
        self, tmp_path: Path
    ) -> None:
        """The negative control: a top-level array is ruled out on sight."""
        path = tmp_path / "compile_commands.json"
        path.write_text(
            json.dumps([{"directory": "/b", "command": "cc -c a.c", "file": "a.c"}] * 20000),
            encoding="utf-8",
        )
        assert classify_build_info_transport(path) is BuildInfoTransport.COMPILE_CONTEXT


# ── 5. the whole public invocation, under non-conventional names ─────────────


# ── 5. the whole public invocation, under non-conventional names ─────────────


def _snapshot(path: Path, *, funcs: list[str], version: str) -> Path:
    snap = AbiSnapshot(
        library="libfoo.so",
        version=version,
        functions=[
            Function(
                name=n,
                mangled=f"_Z{len(n)}{n}v",
                return_type="int",
                visibility=Visibility.PUBLIC,
            )
            for n in funcs
        ],
        from_headers=True,
    )
    path.write_text(snapshot_to_json(snap), encoding="utf-8")
    return path


def _release_pair(tmp_path: Path) -> tuple[Path, Path]:
    """A directory/directory release pair with one removed function."""
    old, new = tmp_path / "old", tmp_path / "new"
    old.mkdir()
    new.mkdir()
    _snapshot(old / "libfoo.so.json", funcs=["foo", "bar"], version="1.0")
    _snapshot(new / "libfoo.so.json", funcs=["foo"], version="2.0")
    return old, new


class TestMergedInputsEndToEnd:
    """Each merged transport, driven through the real CLI.

    Names are deliberately non-conventional throughout: this is the half
    that was missing when the registered bug class shipped.
    """

    @pytest.mark.parametrize("name", ["dbg-evidence", "libfoo.so", "NOTES"])
    @pytest.mark.parametrize("flag", ["--debug-info", "-H"])
    def test_a_package_reaches_the_extraction_stage_under_any_name(
        self, tmp_path: Path, name: str, flag: str
    ) -> None:
        """The pipeline half, with a *positive* signal that it engaged.

        Asserting "the run still succeeded" proves nothing here: a package
        the classifier failed to recognise falls through to the searchable
        transport and the comparison completes anyway, which is exactly how
        this bug class hides. So the operand is a package whose *extraction*
        refuses it (a tar escaping its root), and the evidence is that the
        extractor got to say so -- reached only through
        `prepare_release_inputs`' package stage, and only if the operand was
        classified as a package from its content. Verified by mutation: with
        `is_package`/`detect_extractor` keyed on suffixes again, every
        non-conventional name below stops raising.
        """
        old, new = _release_pair(tmp_path)
        escaping = _write_tar(tmp_path / name, {"../escape.so": b"\x7fELF"})
        res = _run(
            "compare", str(old), str(new), flag, f"new={escaping}", "--format", "json"
        )
        assert isinstance(res.exception, ExtractionSecurityError), res.output

    @pytest.mark.parametrize("flag", ["--debug-info", "-H"])
    def test_a_well_formed_package_under_a_bare_name_completes(
        self, tmp_path: Path, flag: str
    ) -> None:
        """The other direction: recognised, unpacked, and the comparison runs."""
        old, new = _release_pair(tmp_path)
        pkg = _write_tar(
            tmp_path / "evidence",
            {"usr/include/foo.h": b"int foo(void);\n",
             "usr/lib/debug/libfoo.so.debug": b"\x7fELF"},
        )
        res = _run(
            "compare", str(old), str(new), flag, f"new={pkg}", "--format", "json"
        )
        assert not isinstance(res.exception, ExtractionSecurityError), res.output
        assert res.exit_code == 4, res.output

    def test_a_debug_directory_and_a_bare_debug_file_both_reach_the_resolver(
        self, tmp_path: Path
    ) -> None:
        old = _snapshot(tmp_path / "old.json", funcs=["foo", "bar"], version="1.0")
        new = _snapshot(tmp_path / "new.json", funcs=["foo"], version="2.0")
        root = tmp_path / "dbg"
        root.mkdir()
        sidecar = _elf_with_sections(tmp_path / "sidecar-no-suffix", [".debug_info"])
        for value in (root, sidecar):
            res = _run(
                "compare", str(old), str(new), "--debug-info", f"old={value}",
                "--format", "json",
            )
            assert res.exit_code == 4, res.output

    def test_a_probe_matrix_and_a_build_context_together(self, tmp_path: Path) -> None:
        """The two roles one ``--build-info`` now carries, supplied at once
        for both sides, and still told apart in the report: the matrix
        contributes its own build-config finding, the build directory does
        not become one."""
        old = _snapshot(tmp_path / "old.json", funcs=["foo"], version="1.0")
        new = _snapshot(tmp_path / "new.json", funcs=["foo"], version="2.0")
        old_matrix = _write_probe_matrix(tmp_path / "old-ctx.json", floor=17)
        new_matrix = _write_probe_matrix(tmp_path / "new-ctx.json", floor=20)
        build_dir = tmp_path / "b"
        build_dir.mkdir()
        res = _run(
            "compare", str(old), str(new),
            "--build-info", f"old={old_matrix}",
            "--build-info", f"new={new_matrix}",
            "--build-info", f"old={build_dir}",
            "--format", "json",
        )
        assert res.exit_code in (0, 2, 4), res.output
        kinds = _finding_kinds(res)
        assert "cxx_standard_floor_raised" in kinds, sorted(kinds)

    def test_a_one_sided_matrix_still_names_both_sides(self, tmp_path: Path) -> None:
        old = _snapshot(tmp_path / "old.json", funcs=["foo"], version="1.0")
        new = _snapshot(tmp_path / "new.json", funcs=["foo"], version="2.0")
        matrix = _write_probe_matrix(tmp_path / "m.json")
        res = _run(
            "compare", str(old), str(new), "--build-info", f"old={matrix}"
        )
        assert res.exit_code != 0
        assert "needs both sides" in res.output

    @pytest.mark.parametrize(
        ("command", "flag"),
        [
            ("compare", "--debug-root"),
            ("compare", "--devel-pkg"),
            ("compare", "--probe-matrix"),
            ("dump", "--debug-root"),
        ],
    )
    def test_the_retired_spellings_are_gone(
        self, tmp_path: Path, command: str, flag: str
    ) -> None:
        """Exit 64, and no hidden alias: the merge is not a rename with a
        compatibility shim behind it."""
        operands = ["a.json", "b.json"] if command == "compare" else ["a.so"]
        res = _run(command, *operands, flag, str(tmp_path))
        assert res.exit_code == 64, res.output
        assert "No such option" in res.output

    def test_dump_rejects_a_debug_package_by_naming_the_alternative(
        self, tmp_path: Path
    ) -> None:
        binary = _elf_with_sections(tmp_path / "libfoo.so", [".text"])
        pkg = _write_rpm(tmp_path / "dbg-evidence")
        res = _run("dump", str(binary), "--debug-info", str(pkg))
        assert res.exit_code == 64, res.output
        assert "compare --debug-info" in res.output

    def test_dump_accepts_a_directory_and_a_detached_file(
        self, tmp_path: Path
    ) -> None:
        binary = _elf_with_sections(tmp_path / "libfoo.so", [".text"])
        root = tmp_path / "dbg"
        root.mkdir()
        sidecar = _elf_with_sections(tmp_path / "sidecar", [".debug_info"])
        for value in (root, sidecar):
            res = _run("dump", str(binary), "--debug-info", str(value), "--dry-run")
            assert res.exit_code != 64, res.output


# ── 6. transport equivalence: the merge cost no capability ───────────────────


class TestTransportEquivalence:
    """The same evidence through two transports of one role agrees.

    The oracle for each case is the *other* transport's run, so neither
    side is a recorded expectation that could drift with the code.
    """

    def test_a_devel_package_and_its_extracted_headers_agree(
        self, tmp_path: Path
    ) -> None:
        old, new = _release_pair(tmp_path)
        header = b"int foo(void);\n"
        pkg = _write_tar(tmp_path / "devel-blob", {"usr/include/foo.h": header})
        loose = tmp_path / "inc"
        loose.mkdir()
        (loose / "foo.h").write_bytes(header)

        via_package = _run(
            "compare", str(old), str(new), "-H", f"new={pkg}", "--format", "json"
        )
        via_directory = _run(
            "compare", str(old), str(new), "-H", f"new={loose}", "--format", "json"
        )
        assert via_package.exit_code == via_directory.exit_code
        assert _finding_kinds(via_package) == _finding_kinds(via_directory)

    def test_a_tagged_and_a_tagless_matrix_agree(self, tmp_path: Path) -> None:
        """Phase 7n's own `schema` tag changes classification, never
        findings -- a snapshot captured before it existed must still
        produce the identical build-config finding."""
        old = _snapshot(tmp_path / "old.json", funcs=["foo"], version="1.0")
        new = _snapshot(tmp_path / "new.json", funcs=["foo"], version="2.0")
        runs = []
        for tagged in (True, False):
            om = _write_probe_matrix(
                tmp_path / f"o{tagged}.json", tagged=tagged, floor=17
            )
            nm = _write_probe_matrix(
                tmp_path / f"n{tagged}.json", tagged=tagged, floor=20
            )
            runs.append(
                _run(
                    "compare", str(old), str(new),
                    "--build-info", f"old={om}", "--build-info", f"new={nm}",
                    "--format", "json",
                )
            )
        assert runs[0].exit_code == runs[1].exit_code
        assert _finding_kinds(runs[0]) == _finding_kinds(runs[1])
        assert "cxx_standard_floor_raised" in _finding_kinds(runs[0])

    def test_a_renamed_package_and_its_conventional_name_agree(
        self, tmp_path: Path
    ) -> None:
        """Content routing, asserted through the whole invocation: the same
        bytes under two names produce the same findings."""
        old, new = _release_pair(tmp_path)
        members = {"usr/include/foo.h": b"int foo(void);\n"}
        conventional = _write_tar(tmp_path / "libfoo-dev.tar.gz", members)
        renamed = _write_tar(tmp_path / "evidence", members)
        a = _run("compare", str(old), str(new), "-H", f"new={conventional}", "--format", "json")
        b = _run("compare", str(old), str(new), "-H", f"new={renamed}", "--format", "json")
        assert a.exit_code == b.exit_code
        assert _finding_kinds(a) == _finding_kinds(b)


def _json_payload(res: Result) -> dict:
    """The report body, past any leading warning lines the CLI prints."""
    start = res.output.index("{")
    return json.loads(res.output[start:])


def _finding_kinds(res: Result) -> set[str]:
    return {c["kind"] for c in _json_payload(res).get("changes", [])}
