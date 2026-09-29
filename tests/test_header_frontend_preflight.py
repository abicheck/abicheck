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


def _expected(requested: str, env_pin: str, fallback: bool, castxml: bool, clang: bool):
    """Independent restatement of the documented resolution rules."""
    req = requested.lower()
    if req == "auto":
        resolved = env_pin if env_pin in ("castxml", "clang", "hybrid") else "castxml"
        eligible = env_pin not in ("castxml", "clang", "hybrid")
    else:
        resolved, eligible = req, False
    missing = []
    if resolved in ("castxml", "hybrid") and not castxml:
        if not (eligible and fallback and clang):
            missing.append("castxml")
    if resolved in ("clang", "hybrid") and not clang:
        missing.append("clang++")
    return resolved, missing


@pytest.mark.parametrize(
    "requested,env_pin,fallback,castxml,clang",
    list(
        itertools.product(
            ("auto", "castxml", "clang", "hybrid"),
            ("", "castxml", "clang", "hybrid"),
            (False, True),
            (False, True),
            (False, True),
        )
    ),
)
def test_preflight_matches_resolution_rules(
    monkeypatch, requested, env_pin, fallback, castxml, clang
):
    monkeypatch.setenv("ABICHECK_AST_FRONTEND", env_pin)
    if fallback:
        monkeypatch.setenv("ABICHECK_ALLOW_AST_FALLBACK", "1")
    else:
        monkeypatch.delenv("ABICHECK_ALLOW_AST_FALLBACK", raising=False)
    tools = {
        t
        for t, ok in (("castxml", castxml), ("clang++", clang), ("clang", clang))
        if ok
    }
    _fake_path(monkeypatch, tools)
    pf = preflight_header_frontend(requested)
    resolved, missing = _expected(requested, env_pin, fallback, castxml, clang)
    assert pf.resolved == resolved
    assert list(pf.missing_tools) == missing
    assert (pf.blocker() is None) == (not missing)


def test_real_dump_agrees_on_castxml_missing(monkeypatch):
    """Oracle from the real code path: the dump's own castxml resolution
    raises exactly when the preflight blocks."""
    from abicheck.dumper import _resolve_gated_castxml_bin
    from abicheck.errors import SnapshotError

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
