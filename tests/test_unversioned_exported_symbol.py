# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""``unversioned_exported_symbol``: flag only exports that carry no version.

Bug class ``evidence.distinct_encoded_states_collapsed_to_one_value``: an
exported symbol bound to a versioned library's ``VER_FLG_BASE`` definition
(index 1, ``objdump -T`` prints ``Base``) and one carrying no version at all
(no ``.gnu.version``, or ``VER_NDX_LOCAL``) both left ``ElfSymbol.version``
empty, so the check flagged every base-API function of a GNU-versioned
library (zlib's ``deflate``/``inflate``...). ``ElfSymbol.version_binding``
(schema v58) records which one it is.

Three layers:

* the check over hand-built snapshots, one per binding state, including the
  ``unknown`` a pre-v58 snapshot loads as (never flagged, never "clean");
* the storage round trip, and how a document without the field decodes;
* real ELF objects built by gcc with GNU ld, gold and lld and version
  scripts (``integration``), checked through the ``compare`` CLI against
  ``objdump -T``'s own version column as the oracle -- an independent reader
  of the same ``.gnu.version``/``.gnu.version_d`` tables.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from abicheck.buildsource.cross_source_checks import (
    CHECK_UNVERSIONED_EXPORTED_SYMBOL,
    PROVIDER_BINARY_EXPORTS,
    run_crosschecks,
)
from abicheck.checker_policy import API_BREAK_KINDS, ChangeKind
from abicheck.model import AbiSnapshot
from abicheck.model.elf_facts import ElfMetadata, ElfSymbol, ElfVersionBinding
from abicheck.serialization import snapshot_from_dict, snapshot_to_dict

_B = ElfVersionBinding


def _snap(**kw) -> AbiSnapshot:
    kw.setdefault("library", "libfoo.so")
    kw.setdefault("version", "1.0")
    kw.setdefault("from_headers", True)
    return AbiSnapshot(**kw)


def _findings_of(result, kind: ChangeKind):
    return [c for c in result.findings if c.kind == kind]


def _coverage(result, check: str) -> dict:
    return next(r for r in result.coverage if r["layer"] == f"crosscheck:{check}")


def _velf(symbols, versions_defined=()) -> ElfMetadata:
    """``(name, version, visibility)`` triples; an empty version is a
    symbol with no version at all (``UNVERSIONED``)."""
    return ElfMetadata(
        symbols=[
            ElfSymbol(
                n,
                version=v,
                visibility=vis,
                version_binding=_B.NAMED if v else _B.UNVERSIONED,
            )
            for (n, v, vis) in symbols
        ],
        versions_defined=list(versions_defined),
    )


# --------------------------------------------------------------------------- #
# The check, one binding state at a time
# --------------------------------------------------------------------------- #


def test_unversioned_exported_symbol_flags_unversioned_under_scheme():
    snap = _snap(
        elf=_velf(
            [("_Z3apiv", "FOO_1.0", "default"), ("_Z6legacyv", "", "default")],
            versions_defined=["FOO_1.0"],
        )
    )
    res = run_crosschecks(snap)
    hits = _findings_of(res, ChangeKind.UNVERSIONED_EXPORTED_SYMBOL)
    assert [c.symbol for c in hits] == ["_Z6legacyv"]
    assert ChangeKind.UNVERSIONED_EXPORTED_SYMBOL not in API_BREAK_KINDS
    assert res.providers[CHECK_UNVERSIONED_EXPORTED_SYMBOL] == [PROVIDER_BINARY_EXPORTS]


def test_unversioned_exported_symbol_silent_without_scheme():
    snap = _snap(elf=_velf([("_Z3apiv", "", "default")], versions_defined=[]))
    res = run_crosschecks(snap)
    assert _findings_of(res, ChangeKind.UNVERSIONED_EXPORTED_SYMBOL) == []
    assert _coverage(res, CHECK_UNVERSIONED_EXPORTED_SYMBOL)["status"] == "present"


def test_unversioned_exported_symbol_silent_under_unattached_private_marker_only():
    """An unattached ``*PRIVATE*``-only version node is linker bookkeeping, not
    a real versioning scheme (``diff_versioning._is_unattached_private_version_node``'s
    own definition, reused here) -- an unversioned export must not be flagged
    purely because such a marker exists. Regression for the false positive
    ``checker.compare()``'s automatic cross-source stage surfaced against
    ``tests/test_sprint2_elf.py::test_marker_only_private_version_script_removed_emits_no_warning``'s
    exact fixture shape once the check started running on an ordinary compare.
    """
    snap = _snap(
        elf=_velf([("_Z3apiv", "", "default")], versions_defined=["LIBFOO_PRIVATE"])
    )
    res = run_crosschecks(snap)
    assert _findings_of(res, ChangeKind.UNVERSIONED_EXPORTED_SYMBOL) == []
    assert _coverage(res, CHECK_UNVERSIONED_EXPORTED_SYMBOL)["status"] == "present"


def test_unversioned_exported_symbol_still_flags_alongside_unattached_private_marker():
    """The unattached-marker exemption is scoped to the marker node itself --
    a real, attached scheme flags an unversioned export exactly as before even
    when an unattached private marker also happens to be present."""
    snap = _snap(
        elf=_velf(
            [
                ("_Z3apiv", "FOO_1.0", "default"),
                ("_Z6legacyv", "", "default"),
            ],
            versions_defined=["FOO_1.0", "LIBFOO_PRIVATE"],
        )
    )
    res = run_crosschecks(snap)
    hits = _findings_of(res, ChangeKind.UNVERSIONED_EXPORTED_SYMBOL)
    assert [c.symbol for c in hits] == ["_Z6legacyv"]


def test_unversioned_exported_symbol_skips_hidden_and_structors():
    snap = _snap(
        elf=_velf(
            [
                ("_Z3apiv", "FOO_1.0", "default"),
                ("_Z6hiddenv", "", "hidden"),  # not exported-visible
                ("_ZN6WidgetC1Ev", "", "default"),  # ctor artifact
            ],
            versions_defined=["FOO_1.0"],
        )
    )
    res = run_crosschecks(snap)
    assert _findings_of(res, ChangeKind.UNVERSIONED_EXPORTED_SYMBOL) == []


def test_unversioned_exported_symbol_skipped_on_non_elf():
    snap = _snap(elf=None)
    res = run_crosschecks(snap)
    assert _coverage(res, CHECK_UNVERSIONED_EXPORTED_SYMBOL)["status"] == "skipped"


@pytest.mark.parametrize(
    ("binding", "flagged", "status"),
    [
        (_B.UNVERSIONED, True, "present"),
        (_B.BASE, False, "present"),
        # A pre-v58 snapshot cannot tell base from unversioned: never a
        # finding, and never read as a clean, fully evaluated pass either.
        (_B.UNKNOWN, False, "partial"),
    ],
)
def test_only_an_unversioned_binding_is_flagged(binding, flagged, status):
    elf = ElfMetadata(
        symbols=[
            ElfSymbol("api", version="FOO_1.0", version_binding=_B.NAMED),
            ElfSymbol("legacy", version_binding=binding),
        ],
        versions_defined=["FOO_1.0"],
    )
    res = run_crosschecks(_snap(elf=elf))
    hits = _findings_of(res, ChangeKind.UNVERSIONED_EXPORTED_SYMBOL)
    assert [c.symbol for c in hits] == (["legacy"] if flagged else [])
    row = _coverage(res, CHECK_UNVERSIONED_EXPORTED_SYMBOL)
    assert row["status"] == status
    if status == "partial":
        assert "1 export(s)" in row["detail"]
        assert CHECK_UNVERSIONED_EXPORTED_SYMBOL not in res.providers


# --------------------------------------------------------------------------- #
# Storage: round trip and pre-v58 decoding
# --------------------------------------------------------------------------- #


def _round_trip(elf: ElfMetadata) -> tuple[dict, AbiSnapshot]:
    encoded = json.loads(json.dumps(snapshot_to_dict(_snap(elf=elf))))
    return encoded, snapshot_from_dict(encoded)


@pytest.mark.parametrize("binding", list(_B))
@pytest.mark.parametrize("version", ["", "FOO_1.0"])
def test_every_binding_round_trips(binding, version):
    _encoded, back = _round_trip(
        ElfMetadata(symbols=[ElfSymbol("f", version=version, version_binding=binding)])
    )
    got = back.elf.symbols[0].version_binding
    if binding is _B.UNKNOWN and version:
        # Only a resolved node ever wrote a version name, so a named
        # version with no recorded binding is NAMED, never left unknown.
        assert got is _B.NAMED
    else:
        assert got is binding


def test_binding_is_written_only_where_version_cannot_say_it():
    encoded, _back = _round_trip(
        ElfMetadata(
            symbols=[
                ElfSymbol("named", version="V1", version_binding=_B.NAMED),
                ElfSymbol("base", version_binding=_B.BASE),
                ElfSymbol("bare", version_binding=_B.UNVERSIONED),
                ElfSymbol("unknown"),
            ]
        )
    )
    written = {s["name"]: s.get("version_binding") for s in encoded["elf"]["symbols"]}
    assert written == {
        "named": None,
        "base": "base",
        "bare": "unversioned",
        "unknown": None,
    }


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ({"name": "f", "version": "V1"}, _B.NAMED),
        ({"name": "f", "version": ""}, _B.UNKNOWN),
        ({"name": "f"}, _B.UNKNOWN),
        ({"name": "f", "version_binding": "from-the-future"}, _B.UNKNOWN),
    ],
)
def test_a_document_without_a_binding_never_guesses_base_or_unversioned(raw, expected):
    encoded = json.loads(json.dumps(snapshot_to_dict(_snap(elf=ElfMetadata()))))
    encoded["schema_version"] = 56
    encoded["elf"]["symbols"] = [raw]
    assert snapshot_from_dict(encoded).elf.symbols[0].version_binding is expected


# --------------------------------------------------------------------------- #
# Real ELF objects: gcc + GNU ld / gold / lld + version scripts
# --------------------------------------------------------------------------- #

_SOURCE = "int base_fn(void){return 1;}\nint named_fn(void){return 2;}\nint other_fn(void){return 3;}\n"
_FUNCS = ("base_fn", "named_fn", "other_fn")


def _require_tools(*tools: str) -> None:
    if sys.platform != "linux":
        pytest.skip("GNU symbol versioning is ELF-only")
    missing = [t for t in tools if shutil.which(t) is None]
    if missing:
        pytest.skip(f"missing tool(s): {missing}")


def _build(tmp: Path, name: str, script: str | None, linker: str = "bfd") -> Path:
    """Build ``lib<name>.so``; a configured toolchain that fails is a failure."""
    src = tmp / "a.c"
    src.write_text(_SOURCE)
    out = tmp / f"lib{name}.so"
    cmd = ["gcc", "-shared", "-fPIC", str(src), "-o", str(out)]
    cmd += [f"-fuse-ld={linker}", f"-Wl,-soname,lib{name}.so.1"]
    if script is not None:
        (tmp / f"{name}.map").write_text(script)
        cmd.append(f"-Wl,--version-script={tmp / f'{name}.map'}")
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    assert proc.returncode == 0, f"{cmd}\n{proc.stderr}"
    return out


def _bind_to_local(lib: Path, symbol: str) -> None:
    """Rewrite *symbol*'s ``.gnu.version`` entry to ``VER_NDX_LOCAL`` (0): an
    export that carries no version, not even the base -- the one encoding of
    "unversioned" a versioned library can hold, which no linker here emits."""
    from elftools.elf.elffile import ELFFile

    with open(lib, "r+b") as fh:
        elf = ELFFile(fh)
        versym = elf.get_section_by_name(".gnu.version")
        dynsym = elf.get_section_by_name(".dynsym")
        (index,) = [i for i, s in enumerate(dynsym.iter_symbols()) if s.name == symbol]
        fh.seek(versym["sh_offset"] + 2 * index)
        fh.write(b"\x00\x00")


def _objdump_bindings(lib: Path) -> dict[str, ElfVersionBinding]:
    """The oracle: ``objdump -T``'s version column for each of ``_FUNCS`` --
    ``Base`` for the base definition, empty for no version, else a name.
    An unversioned library has no version column at all."""
    out = subprocess.run(
        ["objdump", "-T", str(lib)], capture_output=True, text=True, check=True
    ).stdout
    has_versions = "Base" in out or bool(re.search(r"\sV\d\s", out))
    found: dict[str, ElfVersionBinding] = {}
    for line in out.splitlines():
        fields = line.split()
        if not fields or fields[-1] not in _FUNCS:
            continue
        # `<addr> g DF .text <size> [version] <name>`
        tail = fields[fields.index(".text") + 2 : -1]
        version = tail[0] if tail else ""
        if not has_versions or not version:
            found[fields[-1]] = _B.UNVERSIONED
        elif version == "Base":
            found[fields[-1]] = _B.BASE
        else:
            found[fields[-1]] = _B.NAMED
    assert set(found) == set(_FUNCS), out
    return found


def _audit(lib: Path) -> list[str]:
    """``unversioned_exported_symbol`` findings of ``compare LIB LIB``."""
    proc = subprocess.run(
        [sys.executable, "-m", "abicheck", "compare", str(lib), str(lib)]
        + ["-o", "json=-"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    report = json.loads(proc.stdout)
    return sorted(
        c["symbol"]
        for c in report["changes"]
        if c["kind"] == "unversioned_exported_symbol"
    )


def _dumped_bindings(lib: Path) -> dict[str, ElfVersionBinding]:
    from abicheck.elf_metadata import parse_elf_metadata

    meta = parse_elf_metadata(lib)
    return {s.name: s.version_binding for s in meta.symbols if s.name in _FUNCS}


_CASES = [
    # (name, version script, linker, symbol rewritten to VER_NDX_LOCAL)
    ("baseonly", "V1 { };\n", "bfd", None),
    ("mixed", "V1 { global: named_fn; };\n", "bfd", None),
    ("mixedgold", "V1 { global: named_fn; };\n", "gold", None),
    ("mixedlld", "V1 { global: named_fn; };\n", "lld", None),
    ("unversionedinside", "V1 { global: named_fn; };\n", "bfd", "other_fn"),
    ("nolocal", "V1 { global: named_fn; };\n", "gold", "base_fn"),
    ("unversionedlib", None, "bfd", None),
]


@pytest.mark.integration
@pytest.mark.parametrize(("name", "script", "linker", "to_local"), _CASES)
def test_real_elf_findings_match_the_objdump_oracle(
    tmp_path: Path, name: str, script: str | None, linker: str, to_local: str | None
) -> None:
    _require_tools("gcc", "objdump", {"bfd": "ld.bfd"}.get(linker, f"ld.{linker}"))
    lib = _build(tmp_path, name, script, linker)
    if to_local is not None:
        _bind_to_local(lib, to_local)
    oracle = _objdump_bindings(lib)

    # The extractor records exactly what objdump reads.
    assert _dumped_bindings(lib) == oracle
    # The check flags exactly the exports with no version in a library that
    # defines a scheme: never a base-bound one, never in an unversioned library.
    expected = (
        sorted(n for n, b in oracle.items() if b is _B.UNVERSIONED)
        if script is not None
        else []
    )
    assert _audit(lib) == expected
    # Every case states a real distinction, not a vacuous pass.
    if name in ("baseonly", "mixed", "mixedgold", "mixedlld"):
        assert _B.BASE in oracle.values() and expected == []
    if to_local is not None:
        assert expected == [to_local]


@pytest.mark.integration
def test_a_stored_dump_keeps_the_binding(tmp_path: Path) -> None:
    """A v58 snapshot dumped from a real library compares exactly like the
    live library: the binding survives ``dump`` → JSON → ``compare``."""
    _require_tools("gcc", "objdump")
    lib = _build(tmp_path, "stored", "V1 { global: named_fn; };\n")
    _bind_to_local(lib, "other_fn")
    snap = tmp_path / "stored.abi.json"
    proc = subprocess.run(
        [sys.executable, "-m", "abicheck", "dump", str(lib), "-o", str(snap)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert _audit(snap) == _audit(lib) == ["other_fn"]
