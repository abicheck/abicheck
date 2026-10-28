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

"""A verdict-affecting caller of the platform parsers never reads an unread
export table as an empty one.

``parse_{elf,pe,macho}_metadata`` keep going past a malformed section, so an
export table that could not be read comes back as an empty surface that looks
like a library exporting nothing. The dump refuses that binary
(``extract/export_table_read.py``), but several other callers read the same
empty result directly: a package contract generated from it, a manifest or
symbols file validated against it, a bundle member, a dependency graph whose
DSO exports nothing, a stranded library's bundle-facts snapshot.

Each such caller is a row of :data:`CALLERS`. Every row is fed the same
corruption (the section header table of a gcc-built ``.so`` zeroed) through
its closest public entry point, and must either raise an error or report the
export table as not read -- never a clean, empty, or "missing" result. A
control row (the dump itself) and a row whose caller was already routed
(``read_library_export_facts``) pin the behaviour that already held.
:func:`test_intact_library_is_not_refused` runs the same table on an intact
library, so a refusal cannot be satisfied by a caller that always complains.

The oracle is each entry point's observable output (raised error text, CLI
output, the rendered result, or the persisted snapshot's export-table state),
not the parser's own failure list.
"""

from __future__ import annotations

import json
import shutil
import struct
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pytest
from click.testing import CliRunner

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(shutil.which("gcc") is None, reason="gcc required"),
    pytest.mark.skipif(sys.platform != "linux", reason="corrupts an ELF .so"),
]

_NOT_READ = "not read"
_PROFILE = "linux-x86_64-gcc13-release"
_SONAME = "libr2.so.1"
_SYMBOLS = "libr2.so.1 libr2.so.1 #MINVER#\n foo@Base 0\n bar@Base 0\n"


@dataclass(frozen=True)
class _Side:
    """One library variant (intact or corrupt) and the paths a caller needs."""

    lib: Path
    app: Path
    work: Path


# ---------------------------------------------------------------------------
# Fixture construction
# ---------------------------------------------------------------------------


def _build_library(directory: Path) -> Path:
    directory.mkdir(parents=True)
    src = directory / "r2.c"
    src.write_text("int foo(int x) { return x + 1; }\nint bar(void) { return 2; }\n")
    lib = directory / _SONAME
    built = subprocess.run(
        [
            "gcc",
            "-shared",
            "-fPIC",
            "-g",
            f"-Wl,-soname,{_SONAME}",
            "-o",
            str(lib),
            str(src),
        ],
        capture_output=True,
        text=True,
    )
    # gcc is present (the skipif above): a failing build is a failure, with
    # its diagnostics attached, not a skip.
    assert built.returncode == 0, built.stderr
    return lib


def _build_app(directory: Path, lib: Path) -> Path:
    directory.mkdir(parents=True)
    src = directory / "app.c"
    src.write_text(
        "int foo(int); int bar(void);\nint main(void) { return foo(1) + bar() - 3; }\n"
    )
    app = directory / "app"
    built = subprocess.run(
        ["gcc", "-o", str(app), str(src), str(lib)], capture_output=True, text=True
    )
    assert built.returncode == 0, built.stderr
    return app


def _zero_section_table(data: bytes) -> bytes:
    """Zero every section header and drop PT_DYNAMIC: neither the section
    table nor the loader's own view can locate the export table any more
    (a zeroed section table alone is still read through PT_DYNAMIC)."""
    shoff = struct.unpack_from("<Q", data, 0x28)[0]
    entsize, count = struct.unpack_from("<HH", data, 0x3A)
    out = bytearray(data)
    out[shoff : shoff + entsize * count] = b"\0" * (entsize * count)
    phoff = struct.unpack_from("<Q", data, 0x20)[0]
    phentsize, phnum = struct.unpack_from("<HH", data, 0x36)
    for i in range(phnum):
        at = phoff + i * phentsize
        if struct.unpack_from("<I", out, at)[0] == 2:  # PT_DYNAMIC
            struct.pack_into("<I", out, at, 0)  # PT_NULL
    return bytes(out)


@pytest.fixture
def libraries(tmp_path: Path) -> dict[str, Path]:
    intact = _build_library(tmp_path / "intact")
    corrupt_dir = tmp_path / "corrupt"
    corrupt_dir.mkdir()
    corrupt = corrupt_dir / _SONAME
    corrupt.write_bytes(_zero_section_table(intact.read_bytes()))
    return {
        "intact": intact,
        "corrupt": corrupt,
        "app": _build_app(tmp_path / "app", intact),
    }


# ---------------------------------------------------------------------------
# One row per caller: render what the caller tells its user about *lib*
# ---------------------------------------------------------------------------


def _dump_cli(side: _Side) -> str:
    from abicheck.cli import main

    result = CliRunner().invoke(
        main, ["dump", str(side.lib), "-o", str(side.work / "dump.json")]
    )
    return result.output if result.exit_code != 0 else "dumped"


def _debian_generate(side: _Side) -> str:
    from abicheck.debian_symbols import generate_from_binary

    return f"symbols: {[s.name for s in generate_from_binary(side.lib).symbols]}"


def _debian_validate(side: _Side) -> str:
    from abicheck.debian_symbols import validate_from_binary

    symbols = side.work / "libr2.symbols"
    symbols.write_text(_SYMBOLS, encoding="utf-8")
    result = validate_from_binary(side.lib, symbols)
    return f"missing: {[e.name for e in result.missing]}"


def _post_manifest(side: _Side) -> str:
    from abicheck.post_manifest import validate_from_binary

    manifest = side.work / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "post_abi": 1,
                "exports": [
                    {
                        "name": "foo",
                        "c_symbol": "foo",
                        "params": ["Int32"],
                        "return_dtype": "Int32",
                    },
                    {
                        "name": "bar",
                        "c_symbol": "bar",
                        "params": [],
                        "return_dtype": "Int32",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    return f"missing: {validate_from_binary(manifest, side.lib).missing}"


def _bundle(side: _Side) -> str:
    from abicheck.bundle import build_bundle_snapshot

    snap = build_bundle_snapshot({_SONAME: side.lib})
    return f"members: {[(k, len(m.symbols)) for k, m in sorted(snap.metadata.items())]}"


def _package_claim(side: _Side) -> str:
    from abicheck.workflows.package_claim_conflicts import (
        debian_symbols_binary_conflict_lines,
    )

    symbols = side.work / "libr2.symbols"
    symbols.write_text(_SYMBOLS, encoding="utf-8")
    return (
        "\n".join(debian_symbols_binary_conflict_lines(symbols, side.lib.parent))
        or "no conflict"
    )


def _baseline_set(side: _Side) -> str:
    from abicheck.buildsource.baseline_set import resolve_bundle

    baseline = side.work / "baseline"
    (baseline / "binaries").mkdir(parents=True)
    (baseline / "binaries" / _SONAME).write_bytes(side.lib.read_bytes())
    manifest = {
        "manifest_version": 1,
        "project_ref": "v1.0.0",
        "profile": _PROFILE,
        "snapshot_schema": 9,
        "fact_set": None,
        "baseline_generation": None,
        "artifacts": [
            {
                "library": "libr2",
                "artifact": "build/libr2.so",
                "snapshot": "",
                "sha256": "",
                "binary": f"binaries/{_SONAME}",
            }
        ],
    }
    (baseline / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    result = resolve_bundle(
        baseline,
        bundle="r2-release",
        members=["libr2"],
        profile=_PROFILE,
        required=True,
    )
    return f"{result.outcome}: {result.message}"


def _dependency_stack(side: _Side) -> str:
    from abicheck.stack_checker import check_single_env

    result = check_single_env(side.app, search_paths=[side.lib.parent])
    return f"missing: {sorted(b.symbol for b in result.missing_symbols)}"


def _library_facts(side: _Side) -> str:
    from abicheck.extract.library_export_facts import read_library_export_facts
    from abicheck.model.availability import FactStatus

    facts = read_library_export_facts(side.lib)
    if facts.status is FactStatus.FAILED:
        return f"export table not read: {facts.failure_reason}"
    return f"export table read: {sorted(facts.export_names)}"


def _stranded_library(side: _Side) -> str:
    from abicheck.model import AbiSnapshot, Function, Visibility
    from abicheck.model.export_index import (
        ExportTableState,
        snapshot_export_table_state,
    )
    from abicheck.serialization import load_bundle_facts, snapshot_to_json

    old_dir = side.work / "old"
    new_dir = side.work / "new"
    old_dir.mkdir()
    new_dir.mkdir()
    kept = AbiSnapshot(
        library="libfoo.so",
        version="1.0",
        functions=[
            Function(
                name="foo",
                mangled="_Z3foov",
                return_type="int",
                visibility=Visibility.PUBLIC,
            )
        ],
        from_headers=True,
    )
    (old_dir / "libfoo.json").write_text(snapshot_to_json(kept), encoding="utf-8")
    (new_dir / "libfoo.json").write_text(snapshot_to_json(kept), encoding="utf-8")
    # OLD-only: the stranded resolver is what dumps it into --bundle-facts-out.
    shutil.copy(side.lib, old_dir / "libbroken.so")
    out = side.work / "old.bundlefacts.json"
    from abicheck.cli import main

    CliRunner().invoke(
        main, ["compare", str(old_dir), str(new_dir), "--bundle-facts-out", str(out)]
    )
    stranded = load_bundle_facts(out).per_library_snapshots["libbroken.so"]
    if snapshot_export_table_state(stranded) is ExportTableState.FAILED:
        return "export table not read"
    return (
        f"export table read: {len(stranded.elf.symbols) if stranded.elf else 0} symbols"
    )


CALLERS: list[tuple[str, Callable[[_Side], str]]] = [
    # Control: the dump's own guard already refuses the binary.
    ("cli_dump", _dump_cli),
    ("debian_symbols.generate_from_binary", _debian_generate),
    ("debian_symbols.validate_from_binary", _debian_validate),
    ("post_manifest.validate_from_binary", _post_manifest),
    ("bundle.build_bundle_snapshot", _bundle),
    ("package_claim_conflicts.debian_symbols_binary_conflict_lines", _package_claim),
    ("buildsource.baseline_set.resolve_bundle", _baseline_set),
    ("elf_dependency_resolver via stack_checker.check_single_env", _dependency_stack),
    # Control: already routed through the failure sink (FAILED fact).
    ("extract.library_export_facts.read_library_export_facts", _library_facts),
    (
        "release_compare._resolve_stranded_library via compare --bundle-facts-out",
        _stranded_library,
    ),
]


def _observe(caller: Callable[[_Side], str], side: _Side) -> str:
    """The caller's observable outcome: its rendered result, or its error."""
    from abicheck.errors import SnapshotError

    try:
        return caller(side)
    except SnapshotError as exc:
        return f"error: {exc}"


def _side(libraries: dict[str, Path], variant: str, tmp_path: Path) -> _Side:
    work = tmp_path / f"work-{variant}"
    work.mkdir()
    return _Side(lib=libraries[variant], app=libraries["app"], work=work)


@pytest.mark.parametrize(("name", "caller"), CALLERS, ids=[n for n, _ in CALLERS])
def test_unread_export_table_is_never_reported_as_clean(
    name: str,
    caller: Callable[[_Side], str],
    libraries: dict[str, Path],
    tmp_path: Path,
) -> None:
    side = _side(libraries, "corrupt", tmp_path)
    observed = _observe(caller, side)
    assert _NOT_READ in observed, (
        f"{name} reported an unread export table as a clean/empty result: {observed!r}"
    )


@pytest.mark.parametrize(("name", "caller"), CALLERS, ids=[n for n, _ in CALLERS])
def test_intact_library_is_not_refused(
    name: str,
    caller: Callable[[_Side], str],
    libraries: dict[str, Path],
    tmp_path: Path,
) -> None:
    side = _side(libraries, "intact", tmp_path)
    observed = _observe(caller, side)
    assert not observed.startswith("error:"), (
        f"{name} refused an intact library: {observed!r}"
    )
    assert _NOT_READ not in observed, (
        f"{name} called an intact table unread: {observed!r}"
    )


def test_corrupt_fixture_really_loses_the_export_table(
    libraries: dict[str, Path],
) -> None:
    """The fixture is the corruption the table claims: a parse of it records
    the lost table, and the intact library parses with its exports."""
    from abicheck.elf_metadata import parse_elf_metadata
    from abicheck.extract.parse_failures import recording_export_read_failures

    with recording_export_read_failures() as failures:
        corrupt = parse_elf_metadata(libraries["corrupt"])
    assert corrupt.symbols == []
    assert failures, (
        "a zeroed section table without PT_DYNAMIC must record an export-read failure"
    )

    intact = parse_elf_metadata(libraries["intact"])
    assert sorted(s.name for s in intact.symbols) == ["bar", "foo"]
