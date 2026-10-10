"""``--dry-run`` predicts the header frontend the real run will use.

oneDNN validation: ``compare --dry-run`` reported clang found and estimated
a runtime; the real run failed with "castxml not found in PATH", because
``auto`` resolves to castxml and only falls back to clang on an explicit
opt-in. The preflight must agree with the real resolution for every
(request, env pin, fallback opt-in, tool availability) combination.
"""

from __future__ import annotations

import itertools
import shutil

import pytest
from click.testing import CliRunner

from abicheck.workflows.header_frontend_preflight import preflight_header_frontend


def _fake_path(monkeypatch, available: set[str]) -> None:
    def which(name, *a, **k):
        base = str(name).rsplit("/", 1)[-1]
        return f"/fake/{base}" if base in available else None

    monkeypatch.setattr(shutil, "which", which)


#: (gcc_path, gcc_prefix, castxml's emulated compiler, clang driver name)
_COMPILER_SELECTIONS = (
    (None, None, None, "clang++"),
    ("/opt/tc/bin/g++-99", None, "/opt/tc/bin/g++-99", "clang++"),
    (None, "x86-tc-", "x86-tc-g++", "x86-tc-clang++"),
)


def _expected(
    requested: str,
    env_pin: str,
    fallback: bool,
    castxml: bool,
    clang: bool,
    cc_name: str | None = None,
    cc_present: bool = True,
    clang_name: str = "clang++",
):
    """Independent restatement of the documented resolution rules."""
    req = requested.lower()
    if req == "auto":
        resolved = env_pin if env_pin in ("castxml", "clang", "hybrid") else "castxml"
        eligible = env_pin not in ("castxml", "clang", "hybrid")
    else:
        resolved, eligible = req, False
    missing = []
    if resolved in ("castxml", "hybrid"):
        castxml_gaps = [] if castxml else ["castxml"]
        if cc_name is not None and not cc_present:
            castxml_gaps.append(cc_name)
        if castxml_gaps and not (eligible and fallback and clang):
            missing.extend(castxml_gaps)
    if resolved in ("clang", "hybrid") and not clang:
        # The unresolvable driver is reported by its generic name.
        missing.append("clang++")
    return resolved, missing


@pytest.mark.parametrize(
    "requested,env_pin,fallback,castxml,clang,selection,cc_present",
    list(
        itertools.product(
            ("auto", "castxml", "clang", "hybrid"),
            ("", "castxml", "clang", "hybrid"),
            (False, True),
            (False, True),
            (False, True),
            _COMPILER_SELECTIONS,
            (False, True),
        )
    ),
)
def test_preflight_matches_resolution_rules(
    monkeypatch, requested, env_pin, fallback, castxml, clang, selection, cc_present
):
    gcc_path, gcc_prefix, cc_name, clang_name = selection
    monkeypatch.setenv("ABICHECK_AST_FRONTEND", env_pin)
    if fallback:
        monkeypatch.setenv("ABICHECK_ALLOW_AST_FALLBACK", "1")
    else:
        monkeypatch.delenv("ABICHECK_ALLOW_AST_FALLBACK", raising=False)
    tools = {
        t.rsplit("/", 1)[-1]
        for t, ok in (
            ("castxml", castxml),
            (clang_name, clang),
            ("clang", clang),
            (cc_name or "g++", cc_present),
        )
        if ok
    }
    _fake_path(monkeypatch, tools)
    pf = preflight_header_frontend(requested, gcc_path=gcc_path, gcc_prefix=gcc_prefix)
    resolved, missing = _expected(
        requested, env_pin, fallback, castxml, clang, cc_name, cc_present, clang_name
    )
    assert pf.resolved == resolved
    assert list(pf.missing_tools) == missing
    assert (pf.blocker() is None) == (not missing)


def test_real_dump_agrees_on_castxml_missing(monkeypatch):
    """Oracle from the real code path: the dump's own castxml resolution
    raises exactly when the preflight blocks."""
    from abicheck.errors import SnapshotError
    from abicheck.extract.headers.castxml.backend import _resolve_gated_castxml_bin

    monkeypatch.delenv("ABICHECK_AST_FRONTEND", raising=False)
    monkeypatch.delenv("ABICHECK_ALLOW_AST_FALLBACK", raising=False)
    _fake_path(monkeypatch, {"clang", "clang++"})
    assert preflight_header_frontend("auto").blocker() is not None
    with pytest.raises(SnapshotError, match="castxml not found"):
        _resolve_gated_castxml_bin(None)


def test_compare_dry_run_blocks_when_castxml_missing(monkeypatch, tmp_path):
    from abicheck.cli import main

    hdr = tmp_path / "a.h"
    hdr.write_text("int f(void);\n")
    old = tmp_path / "old.so"
    new = tmp_path / "new.so"
    for p in (old, new):
        p.write_bytes(b"\x7fELF" + b"\0" * 60)
    monkeypatch.delenv("ABICHECK_AST_FRONTEND", raising=False)
    monkeypatch.delenv("ABICHECK_ALLOW_AST_FALLBACK", raising=False)
    monkeypatch.chdir(tmp_path)
    _fake_path(monkeypatch, {"clang", "clang++", "gcc", "g++"})
    res = CliRunner().invoke(
        main, ["compare", str(old), str(new), "-H", str(hdr), "--dry-run"]
    )
    assert res.exit_code == 1, res.output
    assert "header frontend: castxml" in res.output
    assert "castxml" in res.output and "not" in res.output

    _fake_path(monkeypatch, {"castxml", "clang", "clang++", "gcc", "g++"})
    res = CliRunner().invoke(
        main, ["compare", str(old), str(new), "-H", str(hdr), "--dry-run"]
    )
    assert res.exit_code == 0, res.output


def test_real_resolver_names_the_compiler_preflight_checks(monkeypatch):
    """Oracle from the real castxml path: under an explicit selection the
    compiler the dump hands castxml is the one the preflight reports."""
    from abicheck.extract.headers.ast_config import _resolve_compiler_binary

    monkeypatch.delenv("ABICHECK_AST_FRONTEND", raising=False)
    monkeypatch.delenv("ABICHECK_ALLOW_AST_FALLBACK", raising=False)
    _fake_path(monkeypatch, {"castxml"})
    for gcc_path, gcc_prefix, _cc, _cl in _COMPILER_SELECTIONS[1:]:
        cc_bin, _ = _resolve_compiler_binary("c++", gcc_path, gcc_prefix)
        pf = preflight_header_frontend(
            "castxml", gcc_path=gcc_path, gcc_prefix=gcc_prefix
        )
        assert pf.missing_tools == (cc_bin,)
        assert pf.blocker() is not None


def _stored_snapshot(path):
    from abicheck.model.snapshot import AbiSnapshot
    from abicheck.serialization import save_snapshot

    save_snapshot(AbiSnapshot(library="libx.so", version="1"), path)
    return path


def _live_binary(path):
    path.write_bytes(b"\x7fELF" + b"\0" * 60)
    return path


@pytest.mark.parametrize(
    "stored,has_headers,has_manifest",
    list(itertools.product((False, True), repeat=3)),
)
def test_operand_parses_headers_matrix(tmp_path, stored, has_headers, has_manifest):
    from abicheck.workflows.header_frontend_preflight import operand_parses_headers

    path = (
        _stored_snapshot(tmp_path / "side.abi.json")
        if stored
        else _live_binary(tmp_path / "side.so")
    )
    headers = [tmp_path / "a.h"] if has_headers else []
    manifest = object() if has_manifest else None
    expected = (not stored) and (has_headers or has_manifest)
    assert operand_parses_headers(path, headers, manifest) is expected


@pytest.mark.parametrize(
    "old_stored,new_stored,with_h,old_manifest,new_manifest",
    list(itertools.product((False, True), repeat=5)),
)
def test_dry_run_preflights_only_header_parsing_operands(
    monkeypatch, tmp_path, old_stored, new_stored, with_h, old_manifest, new_manifest
):
    """castxml missing: the receipt blocks exactly when an operand the real
    run extracts live has headers to parse (from -H or a dump manifest)."""
    from abicheck.frontends.cli.compare_dry_run import build_compare_dry_run_result

    monkeypatch.delenv("ABICHECK_AST_FRONTEND", raising=False)
    monkeypatch.delenv("ABICHECK_ALLOW_AST_FALLBACK", raising=False)
    monkeypatch.chdir(tmp_path)
    hdr = tmp_path / "a.h"
    hdr.write_text("int f(void);\n")

    def side(name, stored):
        if stored:
            return _stored_snapshot(tmp_path / f"{name}.abi.json")
        return _live_binary(tmp_path / f"{name}.so")

    old, new = side("old", old_stored), side("new", new_stored)
    _fake_path(monkeypatch, {"clang", "clang++", "gcc", "g++"})
    result = build_compare_dry_run_result(
        old_input=old,
        new_input=new,
        old_kind="file",
        new_kind="file",
        depth=None,
        collect_mode="none",
        effective_depth_label="headers",
        headers=(hdr,) if with_h else (),
        includes=(),
        old_headers_only=(),
        new_headers_only=(),
        old_sources=None,
        new_sources=None,
        old_build_info=None,
        new_build_info=None,
        cfg_path=None,
        fmt="text",
        exit_code_scheme=None,
        header_backend="auto",
        old_dump_manifest=object() if old_manifest else None,
        new_dump_manifest=object() if new_manifest else None,
    )
    parses = bool(
        (not old_stored and (with_h or old_manifest))
        or (not new_stored and (with_h or new_manifest))
    )
    rendered = str(result.render())
    assert (result.exit_code == 1) is parses, rendered
    assert ("header frontend: castxml" in rendered) is parses


def test_compare_dry_run_ignores_h_for_two_stored_snapshots(monkeypatch, tmp_path):
    """End to end: -H with two stored snapshots cannot block --dry-run."""
    from abicheck.cli import main

    hdr = tmp_path / "a.h"
    hdr.write_text("int f(void);\n")
    old = _stored_snapshot(tmp_path / "old.abi.json")
    new = _stored_snapshot(tmp_path / "new.abi.json")
    monkeypatch.delenv("ABICHECK_AST_FRONTEND", raising=False)
    monkeypatch.delenv("ABICHECK_ALLOW_AST_FALLBACK", raising=False)
    monkeypatch.chdir(tmp_path)
    _fake_path(monkeypatch, {"gcc", "g++"})
    res = CliRunner().invoke(
        main, ["compare", str(old), str(new), "-H", str(hdr), "--dry-run"]
    )
    assert res.exit_code == 0, res.output
    assert "header frontend:" not in res.output
