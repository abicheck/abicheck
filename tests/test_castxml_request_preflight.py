"""Issue #1710: a missing castxml fails before any extraction work.

The header pass needs castxml whenever the resolved frontend is castxml or
hybrid. Before the preflight, ``compare``/``dump`` did the binary and debug
extraction first and only then failed with "castxml not found in PATH".
The run must now fail with the same ``SnapshotError`` message *before* the
extraction entry point is reached, and runs that never parse headers (or do
not need castxml) must be unaffected.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from abicheck.errors import SnapshotError
from abicheck.extract.headers.castxml.backend import CASTXML_NOT_FOUND_MESSAGE
from abicheck.workflows.dump import native

_real_which = shutil.which


def _hide_castxml(monkeypatch: pytest.MonkeyPatch) -> None:
    def which(name, *a, **k):
        if str(name).rsplit("/", 1)[-1] == "castxml":
            return None
        return _real_which(name, *a, **k)

    monkeypatch.setattr(shutil, "which", which)
    monkeypatch.delenv("ABICHECK_AST_FRONTEND", raising=False)
    monkeypatch.delenv("ABICHECK_ALLOW_AST_FALLBACK", raising=False)


class _Spy:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, *a, **k):
        self.calls += 1
        raise AssertionError("extraction reached")


@pytest.fixture
def spy(monkeypatch: pytest.MonkeyPatch) -> _Spy:
    s = _Spy()
    monkeypatch.setattr(native, "_extract_and_finish", s)
    return s


def _header(tmp_path: Path) -> Path:
    h = tmp_path / "a.h"
    h.write_text("int f(void);\n")
    return h


# -- the choke point: every native dump (compare sides, release members, dump)


@pytest.mark.parametrize("backend", ["auto", "castxml", "hybrid", "CastXML"])
@pytest.mark.parametrize("via", ["headers", "manifest"])
def test_missing_castxml_fails_before_extraction(
    monkeypatch, spy, tmp_path, backend, via
):
    _hide_castxml(monkeypatch)
    kwargs: dict = {"header_backend": backend}
    if via == "headers":
        kwargs["headers"] = [_header(tmp_path)]
    else:
        kwargs["dump_manifest"] = object()
    with pytest.raises(SnapshotError) as exc:
        native._run_dump_uncached(tmp_path / "lib.so", "elf", **kwargs)
    assert str(exc.value) == CASTXML_NOT_FOUND_MESSAGE
    assert "Install" in str(exc.value)
    assert spy.calls == 0


@pytest.mark.parametrize(
    "kwargs",
    [
        {},
        {"headers": "H", "dwarf_only": True},
        {"headers": "H", "symbols_only": True},
        {"headers": "H", "header_backend": "clang"},
    ],
    ids=["no-headers", "dwarf-only", "symbols-only", "clang-backend"],
)
def test_runs_not_needing_castxml_are_untouched(monkeypatch, spy, tmp_path, kwargs):
    _hide_castxml(monkeypatch)
    monkeypatch.setattr(
        "abicheck.extract.compiler_driver.resolve_clang_bin", lambda *a, **k: "clang"
    )
    if kwargs.get("headers") == "H":
        kwargs = {**kwargs, "headers": [_header(tmp_path)]}
    with pytest.raises(AssertionError, match="extraction reached"):
        native._run_dump_uncached(tmp_path / "lib.so", "elf", **kwargs)
    assert spy.calls == 1


@pytest.mark.parametrize("fmt", ["pe", "macho"])
def test_pe_macho_keep_their_export_table_fallback(monkeypatch, spy, tmp_path, fmt):
    """PE/Mach-O header scoping degrades instead of failing: no preflight."""
    _hide_castxml(monkeypatch)
    with pytest.raises(AssertionError, match="extraction reached"):
        native._run_dump_uncached(tmp_path / "lib", fmt, headers=[_header(tmp_path)])
    assert spy.calls == 1


def test_castxml_present_is_untouched(monkeypatch, spy, tmp_path):
    monkeypatch.setattr(
        shutil, "which", lambda n, *a, **k: f"/fake/{str(n).rsplit('/', 1)[-1]}"
    )
    with pytest.raises(AssertionError, match="extraction reached"):
        native._run_dump_uncached(
            tmp_path / "lib.so", "elf", headers=[_header(tmp_path)]
        )
    assert spy.calls == 1


# -- the public workflows: compare (file pair and directory) and dump


def _build_so(tmp: Path, name: str) -> Path:
    src = tmp / f"{name}.c"
    src.write_text("int f(void) { return 1; }\n")
    out = tmp / f"lib{name}.so"
    subprocess.run(["gcc", "-shared", "-fPIC", "-o", str(out), str(src)], check=True)
    return out


@pytest.fixture
def libs(tmp_path):
    if _real_which("gcc") is None:
        pytest.skip("gcc not available")
    old_dir, new_dir = tmp_path / "old", tmp_path / "new"
    old_dir.mkdir()
    new_dir.mkdir()
    return _build_so(old_dir, "x"), _build_so(new_dir, "x"), _header(tmp_path)


def _cli():
    from abicheck.cli import main

    return main


@pytest.mark.integration
@pytest.mark.parametrize("shape", ["pair", "directory", "dump"])
def test_cli_fails_fast_with_install_hint(monkeypatch, spy, libs, tmp_path, shape):
    old, new, h = libs
    monkeypatch.setenv("ABICHECK_CACHE_DIR", str(tmp_path / "cache"))
    _hide_castxml(monkeypatch)
    if shape == "pair":
        args = ["compare", str(old), str(new), "-H", str(h)]
    elif shape == "directory":
        args = ["compare", str(old.parent), str(new.parent), "-H", str(h)]
    else:
        args = ["dump", str(old), "-H", str(h), "-o", str(tmp_path / "s.json")]
    result = CliRunner().invoke(_cli(), args)
    assert spy.calls == 0, result.output
    assert result.exit_code != 0
    assert "castxml not found in PATH" in result.output + str(result.exception)


@pytest.mark.integration
@pytest.mark.parametrize("shape", ["pair", "dump"])
def test_cli_without_headers_still_extracts(monkeypatch, spy, libs, tmp_path, shape):
    old, new, _h = libs
    monkeypatch.setenv("ABICHECK_CACHE_DIR", str(tmp_path / "cache"))
    _hide_castxml(monkeypatch)
    if shape == "pair":
        args = ["compare", str(old), str(new)]
    else:
        args = ["dump", str(old), "-o", str(tmp_path / "s.json")]
    CliRunner().invoke(_cli(), args)
    assert spy.calls >= 1
