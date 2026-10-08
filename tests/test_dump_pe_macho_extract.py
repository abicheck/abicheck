"""PE and Mach-O primary extraction through the format adapters.

``workflows/dump/pe.py`` and ``macho.py`` only run against real Windows or
macOS binaries in the platform lanes. These tests drive them on any host by
substituting the metadata parsers (their owners are ``pe_metadata`` and
``macho_metadata``, not the modules under test) and checking the snapshot the
adapter returns through ``NativeExtractRequest``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from abicheck.errors import SnapshotError, ValidationError
from abicheck.model.macho_facts import MachoExport, MachoMetadata
from abicheck.model.pe_facts import PeExport, PeMetadata
from abicheck.workflows.dump import macho as macho_mod, pe as pe_mod
from abicheck.workflows.dump.formats import (
    MachoAdapter,
    NativeExtractRequest,
    PeAdapter,
)


def _request(path: Path, **kw) -> NativeExtractRequest:
    kw.setdefault("headers", [])
    kw.setdefault("includes", [])
    kw.setdefault("lang", "c++")
    return NativeExtractRequest(path=path, version="1.2", **kw)


def _pe_meta(
    *exports: PeExport, machine: str = "IMAGE_FILE_MACHINE_AMD64"
) -> PeMetadata:
    return PeMetadata(machine=machine, exports=list(exports))


@pytest.fixture
def no_pdb(monkeypatch):
    monkeypatch.setattr(pe_mod, "extract_pdb_debug", lambda path, pdb: (None, None))


def _use_pe(monkeypatch, meta_or_exc):
    import abicheck.pe_metadata as pm

    def fake(path):
        if isinstance(meta_or_exc, BaseException):
            raise meta_or_exc
        return meta_or_exc

    monkeypatch.setattr(pm, "parse_pe_metadata", fake)


def _use_macho(monkeypatch, meta_or_exc):
    import abicheck.macho_metadata as mm

    def fake(path):
        if isinstance(meta_or_exc, BaseException):
            raise meta_or_exc
        return meta_or_exc

    monkeypatch.setattr(mm, "parse_macho_metadata", fake)


# ── PE ──────────────────────────────────────────────────────────────────


@pytest.mark.usefixtures("no_pdb")
def test_pe_export_table_becomes_public_functions(monkeypatch, tmp_path):
    meta = _pe_meta(
        PeExport(name="plain_c", ordinal=1),
        PeExport(name="?cpp@@YAXXZ", ordinal=2),
        PeExport(name="", ordinal=7),
    )
    _use_pe(monkeypatch, meta)
    snap = PeAdapter().extract(_request(tmp_path / "foo.dll"))

    assert snap.library == "foo.dll"
    assert snap.version == "1.2"
    assert snap.platform == "pe"
    assert snap.pe is meta
    by_name = {f.name: f for f in snap.declarations.functions}
    assert set(by_name) == {"plain_c", "?cpp@@YAXXZ", "ordinal:7"}
    assert by_name["ordinal:7"].mangled == "ordinal:7"
    assert by_name["plain_c"].is_extern_c is True
    assert by_name["?cpp@@YAXXZ"].is_extern_c is False
    assert by_name["ordinal:7"].is_extern_c is True
    assert all(f.return_type == "?" for f in snap.declarations.functions)
    assert snap.declarations.types == [] and snap.declarations.enums == []


@pytest.mark.parametrize(
    ("raised", "expected", "fragment"),
    [
        (ImportError("pefile missing"), SnapshotError, "pefile missing"),
        (OSError("boom"), SnapshotError, "Failed to parse PE"),
        (ValueError("bad"), SnapshotError, "Failed to parse PE"),
        (RuntimeError("bad"), SnapshotError, "Failed to parse PE"),
    ],
)
def test_pe_parser_errors_become_snapshot_errors(
    monkeypatch, tmp_path, raised, expected, fragment
):
    _use_pe(monkeypatch, raised)
    with pytest.raises(expected, match=fragment):
        PeAdapter().extract(_request(tmp_path / "x.dll"))


def test_pe_without_machine_is_rejected(monkeypatch, tmp_path):
    _use_pe(monkeypatch, _pe_meta(PeExport(name="f"), machine=""))
    with pytest.raises(SnapshotError, match="Failed to extract PE metadata"):
        PeAdapter().extract(_request(tmp_path / "x.dll"))


def test_pe_without_exports_is_a_validation_error(monkeypatch, tmp_path):
    _use_pe(monkeypatch, _pe_meta())
    with pytest.raises(ValidationError, match="has no exports"):
        PeAdapter().extract(_request(tmp_path / "x.dll"))


def test_pe_header_scoped_result_wins_and_keeps_pdb_debug(monkeypatch, tmp_path):
    _use_pe(monkeypatch, _pe_meta(PeExport(name="f")))
    dwarf, adv = object(), object()
    monkeypatch.setattr(pe_mod, "extract_pdb_debug", lambda path, pdb: (dwarf, adv))
    seen = {}

    class _Decls:
        debug_layout = None
        debug_advanced = None

    class _Scoped:
        declarations = _Decls()

    scoped = _Scoped()

    def fake_scoped(fmt, path, headers, includes, version, lang, **kw):
        seen.update(fmt=fmt, headers=headers, includes=includes, lang=lang, **kw)
        return scoped, None

    monkeypatch.setattr(pe_mod, "try_header_scoped_dump", fake_scoped)
    hdr = tmp_path / "a.h"
    out = PeAdapter().extract(
        _request(
            tmp_path / "x.dll",
            headers=[hdr],
            lang="c",
            lang_explicit=True,
            header_backend="clang",
        )
    )
    assert out is scoped
    assert scoped.declarations.debug_layout is dwarf
    assert scoped.declarations.debug_advanced is adv
    assert seen["fmt"] == "pe"
    assert seen["headers"] == [hdr]
    assert seen["lang"] == "c"
    assert seen["lang_explicit"] is True
    assert seen["header_backend"] == "clang"


@pytest.mark.usefixtures("no_pdb")
def test_pe_header_scoping_fallback_keeps_export_table(monkeypatch, tmp_path):
    _use_pe(monkeypatch, _pe_meta(PeExport(name="f")))
    monkeypatch.setattr(
        pe_mod, "try_header_scoped_dump", lambda *a, **k: (None, "no-castxml")
    )
    snap = PeAdapter().extract(_request(tmp_path / "x.dll", headers=[tmp_path / "a.h"]))
    assert [f.name for f in snap.declarations.functions] == ["f"]
    assert snap.scope_fallback == "no-castxml"


def test_pe_scoping_fallback_recovers_pdb_types(monkeypatch, tmp_path):
    import abicheck.pdb_model as pdbm

    _use_pe(monkeypatch, _pe_meta(PeExport(name="f")))
    dwarf = object()
    monkeypatch.setattr(pe_mod, "extract_pdb_debug", lambda path, pdb: (dwarf, None))
    monkeypatch.setattr(
        pe_mod, "try_header_scoped_dump", lambda *a, **k: (None, "fallback")
    )
    calls = []
    monkeypatch.setattr(
        pdbm,
        "model_types_from_dwarf_metadata",
        lambda m: (calls.append(m), ([], []))[1],
    )
    snap = PeAdapter().extract(_request(tmp_path / "x.dll", headers=[tmp_path / "a.h"]))
    assert calls == [dwarf]
    assert snap.declarations.types == [] and snap.declarations.enums == []


def test_extract_pdb_debug_is_best_effort(monkeypatch, tmp_path):
    import abicheck.pdb_utils as pu

    def boom(*a, **k):
        raise RuntimeError("no pdb support")

    monkeypatch.setattr(pu, "locate_pdb", boom)
    assert pe_mod.extract_pdb_debug(tmp_path / "x.dll", None) == (None, None)


def test_extract_pdb_debug_without_pdb_file(monkeypatch, tmp_path):
    import abicheck.pdb_utils as pu

    seen = {}

    def locate(path, *, pdb_path_override, allow_network):
        seen.update(path=path, override=pdb_path_override, network=allow_network)
        return None

    monkeypatch.setattr(pu, "locate_pdb", locate)
    override = tmp_path / "o.pdb"
    assert pe_mod.extract_pdb_debug(tmp_path / "x.dll", override) == (None, None)
    assert seen == {"path": tmp_path / "x.dll", "override": override, "network": False}


def test_extract_pdb_debug_parses_located_pdb(monkeypatch, tmp_path):
    import abicheck.pdb_metadata as pmd
    import abicheck.pdb_utils as pu

    pdb = tmp_path / "x.pdb"
    monkeypatch.setattr(pu, "locate_pdb", lambda *a, **k: pdb)
    monkeypatch.setattr(
        pmd, "parse_pdb_debug_info", lambda p: ("meta", "adv") if p == pdb else None
    )
    assert pe_mod.extract_pdb_debug(tmp_path / "x.dll", None) == ("meta", "adv")


# ── Mach-O ──────────────────────────────────────────────────────────────


def test_macho_exports_become_public_functions(monkeypatch, tmp_path):
    meta = MachoMetadata(
        install_name="@rpath/libfoo.dylib",
        exports=[
            MachoExport(name="_c_func"),
            MachoExport(name="_ZN3foo3barEv"),
            MachoExport(name=""),
        ],
    )
    _use_macho(monkeypatch, meta)
    snap = MachoAdapter().extract(_request(tmp_path / "libfoo.dylib"))

    assert snap.library == "libfoo.dylib"
    assert snap.platform == "macho"
    assert snap.macho is meta
    by_name = {f.name: f for f in snap.declarations.functions}
    assert set(by_name) == {"_c_func", "_ZN3foo3barEv"}
    assert by_name["_c_func"].is_extern_c is True
    assert by_name["_ZN3foo3barEv"].is_extern_c is False
    assert by_name["_c_func"].mangled == "_c_func"
    assert snap.scope_fallback is None


@pytest.mark.parametrize("exc", [OSError("x"), ValueError("x"), RuntimeError("x")])
def test_macho_parser_errors_become_snapshot_errors(monkeypatch, tmp_path, exc):
    _use_macho(monkeypatch, exc)
    with pytest.raises(SnapshotError, match="Failed to parse Mach-O"):
        MachoAdapter().extract(_request(tmp_path / "x.dylib"))


def test_macho_without_any_metadata_is_rejected(monkeypatch, tmp_path):
    _use_macho(monkeypatch, MachoMetadata())
    with pytest.raises(SnapshotError, match="no exports or load-command metadata"):
        MachoAdapter().extract(_request(tmp_path / "x.dylib"))


@pytest.mark.parametrize(
    "meta",
    [
        MachoMetadata(install_name="libx.dylib"),
        MachoMetadata(dependent_libs=["/usr/lib/libSystem.B.dylib"]),
        MachoMetadata(exports=[MachoExport(name="_f")]),
    ],
)
def test_macho_any_one_metadata_source_suffices(monkeypatch, tmp_path, meta):
    _use_macho(monkeypatch, meta)
    snap = MachoAdapter().extract(_request(tmp_path / "x.dylib"))
    assert snap.macho is meta


def test_macho_header_scoped_result_wins(monkeypatch, tmp_path):
    _use_macho(monkeypatch, MachoMetadata(exports=[MachoExport(name="_f")]))
    sentinel = object()
    seen = {}

    def fake_scoped(fmt, path, headers, includes, version, lang, **kw):
        seen.update(fmt=fmt, includes=includes, version=version, **kw)
        return sentinel, None

    monkeypatch.setattr(macho_mod, "try_header_scoped_dump", fake_scoped)
    inc = tmp_path / "inc"
    out = MachoAdapter().extract(
        _request(
            tmp_path / "x.dylib",
            headers=[tmp_path / "a.h"],
            includes=[inc],
            header_backend="castxml",
        )
    )
    assert out is sentinel
    assert seen["fmt"] == "macho"
    assert seen["includes"] == [inc]
    assert seen["version"] == "1.2"
    assert seen["header_backend"] == "castxml"


def test_macho_header_scoping_fallback_keeps_exports(monkeypatch, tmp_path):
    _use_macho(monkeypatch, MachoMetadata(exports=[MachoExport(name="_f")]))
    monkeypatch.setattr(
        macho_mod, "try_header_scoped_dump", lambda *a, **k: (None, "why")
    )
    snap = MachoAdapter().extract(
        _request(tmp_path / "x.dylib", headers=[tmp_path / "a.h"])
    )
    assert [f.name for f in snap.declarations.functions] == ["_f"]
    assert snap.scope_fallback == "why"
