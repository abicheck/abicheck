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

"""ADR-086: ``compare --no-baseline libfoo.a -H include/``.

The export set of a static archive is its own symbol index. The unit half
checks the read's refusal states on hand-built archives (no index, thin,
non-ELF or mixed-target members never read as an empty export set). The
integration half builds real archives with gcc/ar and checks the export set
against an independent oracle (``nm --defined-only --extern-only`` over the
members), then drives the CLI end to end.
"""

from __future__ import annotations

import json
import shutil
import struct
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner
from test_archive_graph import build_gnu_archive

from abicheck.buildsource.cross_source_checks import (
    CHECK_UNVERSIONED_EXPORTED_SYMBOL,
    run_crosschecks,
)
from abicheck.cli import main as abicheck_main
from abicheck.errors import ValidationError
from abicheck.extract.ar_archive import (
    ARCHIVE_EXPORTS_NO_INDEX,
    ARCHIVE_EXPORTS_READ,
    ARCHIVE_EXPORTS_UNSUPPORTED,
    archive_elf_metadata,
    read_archive_exports,
)
from abicheck.model import AbiSnapshot
from abicheck.model.export_index import build_raw_export_index, read_export_platforms
from abicheck.workflows.input_resolution import resolve_input


def _elf_ident(machine: int = 62, elf_class: int = 2) -> bytes:
    """A 20-byte little-endian ELF header prefix (e_ident, e_type, e_machine)."""
    ident = b"\x7fELF" + bytes([elf_class, 1, 1, 0]) + b"\0" * 8
    return ident + struct.pack("<HH", 1, machine)


def _member(*symbols: str, ident: bytes | None = None) -> bytes:
    body = b"".join(b"\nSYM:" + s.encode() for s in symbols) + b"\n"
    return (_elf_ident() if ident is None else ident) + body


def _write(tmp_path: Path, data: bytes, name: str = "libx.a") -> Path:
    path = tmp_path / name
    path.write_bytes(data)
    return path


# ── unit: the read and its refusal states ───────────────────────────────────


def test_indexed_elf_archive_reads_every_member_symbol(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        build_gnu_archive([("a.o", _member("f1", "g1")), ("b.o", _member("_Z2f2v"))]),
    )
    read = read_archive_exports(path)
    assert read.status == ARCHIVE_EXPORTS_READ
    assert read.names == {"f1", "g1", "_Z2f2v"}
    assert (read.machine, read.elf_class, read.ei_data) == ("EM_X86_64", 64, "LSB")
    assert read.member_count == 2


def test_archive_without_index_is_no_evidence_not_an_empty_set(tmp_path: Path) -> None:
    path = _write(
        tmp_path, build_gnu_archive([("a.o", _member("f1"))], with_index=False)
    )
    read = read_archive_exports(path)
    assert read.status == ARCHIVE_EXPORTS_NO_INDEX
    assert read.names == frozenset()
    assert "ranlib" in read.reason
    with pytest.raises(ValueError):
        archive_elf_metadata(read)


@pytest.mark.parametrize(
    ("members", "thin"),
    [
        # A Mach-O member: underscore-prefixed names, out of scope.
        ([("a.o", _member("f", ident=b"\xcf\xfa\xed\xfe" + b"\0" * 16))], False),
        # A non-object member (LLVM bitcode magic).
        ([("a.o", _member("f", ident=b"BC\xc0\xde" + b"\0" * 16))], False),
        # Two ELF members for different machines.
        ([("a.o", _member("f")), ("b.o", _member("g", ident=_elf_ident(183)))], False),
        # A thin archive keeps member bodies outside the file.
        ([("a.o", _member("f"))], True),
    ],
)
def test_unsupported_archives_are_refused_with_a_reason(
    tmp_path: Path, members: list[tuple[str, bytes]], thin: bool
) -> None:
    read = read_archive_exports(_write(tmp_path, build_gnu_archive(members, thin=thin)))
    assert read.status == ARCHIVE_EXPORTS_UNSUPPORTED
    assert read.names == frozenset()
    assert read.reason


def test_empty_index_is_a_read_empty_table(tmp_path: Path) -> None:
    """An indexed archive defining nothing is a *read* table (machine set)."""
    read = read_archive_exports(
        _write(tmp_path, build_gnu_archive([("a.o", _member())]))
    )
    assert read.status == ARCHIVE_EXPORTS_READ and read.names == frozenset()
    snap = AbiSnapshot(library="libx.a", version="1", elf=archive_elf_metadata(read))
    assert read_export_platforms(snap) == {"elf": True}
    index = build_raw_export_index(snap)
    assert index is not None and index.entries == ()


def test_other_commands_still_reject_an_archive(tmp_path: Path) -> None:
    path = _write(tmp_path, build_gnu_archive([("a.o", _member("f"))]))
    with pytest.raises(ValidationError, match="--no-baseline"):
        resolve_input(path)


def test_archive_operand_without_index_fails_with_ranlib_hint(tmp_path: Path) -> None:
    path = _write(
        tmp_path, build_gnu_archive([("a.o", _member("f"))], with_index=False)
    )
    with pytest.raises(ValidationError, match="ranlib"):
        resolve_input(path, static_archive=True)


def test_archive_operand_requires_headers(tmp_path: Path) -> None:
    path = _write(tmp_path, build_gnu_archive([("a.o", _member("f"))]))
    with pytest.raises(ValidationError, match="needs public headers"):
        resolve_input(path, static_archive=True)


@pytest.mark.parametrize("static_archive", [True, False])
def test_versioning_check_is_not_collected_on_an_archive(static_archive: bool) -> None:
    from abicheck.model.elf_facts import ElfMetadata, ElfSymbol

    snap = AbiSnapshot(
        library="libx",
        version="1",
        elf=ElfMetadata(symbols=[ElfSymbol(name="f")], machine="EM_X86_64"),
        platform="elf",
    )
    snap.static_archive = static_archive
    row = next(
        r
        for r in run_crosschecks(snap).coverage
        if r["layer"] == f"crosscheck:{CHECK_UNVERSIONED_EXPORTED_SYMBOL}"
    )
    if static_archive:
        assert row["status"] == "not_collected"
        assert "static archive" in row["detail"]
    else:
        assert row["status"] != "not_collected"


# ── integration: real gcc/ar archives ───────────────────────────────────────

_TOOLS = ("gcc", "g++", "ar", "nm")
needs_toolchain = pytest.mark.skipif(
    any(shutil.which(t) is None for t in _TOOLS), reason="needs gcc, g++, ar and nm"
)


def _compile(tmp_path: Path, name: str, source: str, cxx: bool = False) -> Path:
    src = tmp_path / (name + (".cpp" if cxx else ".c"))
    src.write_text(source)
    obj = tmp_path / (name + ".o")
    compiler = "g++" if cxx else "gcc"
    subprocess.run([compiler, "-c", "-fPIC", str(src), "-o", str(obj)], check=True)
    return obj


def _archive(tmp_path: Path, name: str, objs: list[Path], flags: str = "rcs") -> Path:
    path = tmp_path / name
    subprocess.run(["ar", flags, str(path), *map(str, objs)], check=True)
    return path


def _nm_defined_globals(objs: list[Path]) -> set[str]:
    """Independent oracle: every global/weak *defined* symbol of the members."""
    out = subprocess.run(
        ["nm", "--defined-only", "--extern-only", "--format=posix", *map(str, objs)],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    names = set()
    for line in out.splitlines():
        parts = line.split()
        # posix format: "<name> <type> [value [size]]"; per-file headers end in ":".
        if len(parts) >= 2 and not parts[0].endswith(":") and len(parts[1]) == 1:
            names.add(parts[0])
    return names


_SOURCES = {
    "plain": ("int f_a(int x) { return x; }\nint g_a = 1;\n", False),
    "weak": ("__attribute__((weak)) int f_weak(void) { return 0; }\n", False),
    "hidden": (
        '__attribute__((visibility("hidden"))) int f_h(void) { return 1; }\n',
        False,
    ),
    "static_only": (
        "static int local_fn(void) { return 2; }\nint use(void){return local_fn();}\n",
        False,
    ),
    "cxx": (
        "namespace ns { struct S { int m(int); static int sv; }; "
        "int S::m(int x) { return x; } int S::sv = 3; "
        "template <class T> T tf(T t) { return t; } template int tf<int>(int); }\n",
        True,
    ),
    "cxx_inline": (
        "inline int il(int x) { return x; }\nint call_il(int x) { return il(x); }\n",
        True,
    ),
}


@pytest.mark.integration
@needs_toolchain
@pytest.mark.parametrize(
    "members",
    [
        ["plain"],
        ["plain", "weak"],
        ["weak", "hidden", "static_only"],
        ["cxx"],
        ["cxx_inline", "plain"],
        list(_SOURCES),
    ],
)
def test_export_set_matches_nm_oracle(tmp_path: Path, members: list[str]) -> None:
    objs = [_compile(tmp_path, m, *_SOURCES[m]) for m in members]
    read = read_archive_exports(_archive(tmp_path, "libo.a", objs))
    assert read.status == ARCHIVE_EXPORTS_READ
    assert read.names == _nm_defined_globals(objs)


@pytest.mark.integration
@needs_toolchain
@pytest.mark.skipif(shutil.which("llvm-ar") is None, reason="needs llvm-ar")
def test_bsd_index_matches_nm_oracle(tmp_path: Path) -> None:
    objs = [_compile(tmp_path, m, *_SOURCES[m]) for m in ("plain", "weak", "cxx")]
    path = tmp_path / "libbsd.a"
    subprocess.run(
        ["llvm-ar", "--format=bsd", "rcs", str(path), *map(str, objs)], check=True
    )
    read = read_archive_exports(path)
    assert (read.status, read.index_kind) == (ARCHIVE_EXPORTS_READ, "bsd")
    assert read.names == _nm_defined_globals(objs)


_HEADER = (
    "int f_present(int);\nint f_missing(int);\nint f_weak(void);\nint f_other(void);\n"
)


def _audit(tmp_path: Path, archive: Path, include: Path):
    out = tmp_path / "audit.json"
    result = CliRunner().invoke(
        abicheck_main,
        [
            "compare",
            "--no-baseline",
            str(archive),
            "-H",
            str(include),
            "-o",
            f"json={out}",
        ],
    )
    return result, (json.loads(out.read_text()) if out.exists() else None)


def _public_not_exported(report: dict) -> set[str]:
    return {
        f["symbol"] for f in report["findings"] if f["kind"] == "public_not_exported"
    }


@pytest.mark.integration
@needs_toolchain
@pytest.mark.skipif(shutil.which("castxml") is None, reason="needs castxml")
def test_audit_reports_exactly_the_missing_declaration(tmp_path: Path) -> None:
    """f_present (one member), f_weak (a weak definition in another) and
    f_other (a third member) are defined; only f_missing is reported."""
    include = tmp_path / "include"
    include.mkdir()
    (include / "greet.h").write_text(_HEADER)
    objs = [
        _compile(tmp_path, "a", "int f_present(int x) { return x; }\n"),
        _compile(
            tmp_path, "b", "__attribute__((weak)) int f_weak(void) { return 0; }\n"
        ),
        _compile(tmp_path, "c", "int f_other(void) { return 1; }\n"),
    ]
    result, report = _audit(tmp_path, _archive(tmp_path, "libgreet.a", objs), include)
    assert result.exit_code == 0, result.output
    assert report is not None and report["no_baseline"] is True
    assert _public_not_exported(report) == {"f_missing"}


@pytest.mark.integration
@needs_toolchain
@pytest.mark.skipif(shutil.which("castxml") is None, reason="needs castxml")
def test_audit_matches_mangled_cxx_declarations(tmp_path: Path) -> None:
    include = tmp_path / "include"
    include.mkdir()
    (include / "api.hpp").write_text(
        "namespace api { int present(int); int missing(int); }\n"
    )
    obj = _compile(
        tmp_path,
        "api",
        "namespace api { int present(int x) { return x; } }\n",
        cxx=True,
    )
    result, report = _audit(tmp_path, _archive(tmp_path, "libapi.a", [obj]), include)
    assert result.exit_code == 0, result.output
    assert _public_not_exported(report) == {"_ZN3api7missingEi"}


@pytest.mark.integration
@needs_toolchain
def test_audit_of_an_unindexed_archive_is_not_clean(tmp_path: Path) -> None:
    include = tmp_path / "include"
    include.mkdir()
    (include / "greet.h").write_text(_HEADER)
    obj = _compile(tmp_path, "a", "int f_present(int x) { return x; }\n")
    # GNU `ar` writes an index unless told not to (`S`).
    result, report = _audit(
        tmp_path, _archive(tmp_path, "libnoidx.a", [obj], "rcS"), include
    )
    assert result.exit_code != 0
    assert report is None
    assert "ranlib" in result.output
