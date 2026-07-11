"""Coverage-focused tests for ``abicheck.compat.cli`` error/edge paths.

Targets branches in the ABICC compat CLI wrapper that the functional suites do
not reach: the ``compat dump`` descriptor/dump success + failure paths, the
post-compare transform helper, per-phase log-handler lifecycle in
``_take_snapshots_with_logging``, report-path mkdir failure, logging-setup
failure in ``compat check``, and the API_BREAK console-summary exit.

Everything is driven either through Click (``CliRunner``) or by calling the
internal helpers/command callbacks directly with crafted inputs. Only a tiny
ELF ``.so`` (built with gcc) is used for the ELF-only dump path; no castxml /
abidiff / abi-compliance-checker is required.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from abicheck.checker_types import DiffResult
from abicheck.cli import main
from abicheck.compat.cli import (
    _apply_result_transforms,
    _print_summary_and_exit,
    _resolve_report_path_and_mkdir,
    _take_snapshots_with_logging,
    compat_dump_cmd,
)
from abicheck.model import AbiSnapshot

# ── helpers / fixtures ──────────────────────────────────────────────────────────

_HAVE_GCC = shutil.which("gcc") is not None


@pytest.fixture
def tiny_so(tmp_path: Path) -> Path:
    """Build a minimal real ELF shared object with one exported function."""
    if not _HAVE_GCC:
        pytest.skip("gcc not available")
    src = tmp_path / "f.c"
    src.write_text("int add(int a, int b){return a+b;}\n", encoding="utf-8")
    so = tmp_path / "libfoo.so"
    subprocess.run(
        ["gcc", "-shared", "-fPIC", "-o", str(so), str(src)],
        check=True,
        capture_output=True,
    )
    return so


def _descriptor(
    path: Path, *, libs: list[Path], headers: list[Path] | None = None
) -> Path:
    """Write a minimal ABICC XML descriptor and return its path."""
    parts = ["<descriptor>", "<version>1.0</version>"]
    for lib in libs:
        parts.append(f"<libs>{lib}</libs>")
    for hdr in headers or []:
        parts.append(f"<headers>{hdr}</headers>")
    parts.append("</descriptor>")
    path.write_text("".join(parts), encoding="utf-8")
    return path


def _dump_callback(**overrides: object) -> None:
    """Invoke the raw ``compat dump`` callback with defaulted keyword args."""
    kw: dict[str, object] = dict(
        lib_name="libfoo",
        desc_path=None,
        dump_path=None,
        dump_format="json",
        vnum=None,
        gcc_path=None,
        gcc_prefix=None,
        gcc_options=None,
        sysroot=None,
        nostdinc=False,
        lang=None,
        arch=None,
        relpath=None,
        quiet=False,
        sort_dump=False,
        extra_dump=False,
        extra_info=None,
        check=False,
        xml_format=False,
    )
    kw.update(overrides)
    compat_dump_cmd.callback(**kw)  # type: ignore[misc]


def _diff_result() -> DiffResult:
    return DiffResult(
        old_version="1.0",
        new_version="2.0",
        library="libfoo.so",
        changes=[],
        old_symbol_count=10,
    )


# ════════════════════════════════════════════════════════════════════════════════
# compat dump — success / warning / failure paths (lines 302, 311-341, 323-324)
# ════════════════════════════════════════════════════════════════════════════════


def test_dump_success_writes_default_json(tiny_so: Path, tmp_path: Path, monkeypatch):
    """ELF-only dump (no headers) writes JSON to the default abi_dumps path."""
    desc = _descriptor(tmp_path / "desc.xml", libs=[tiny_so])
    monkeypatch.chdir(tmp_path)
    _dump_callback(desc_path=desc)
    out = tmp_path / "abi_dumps" / "libfoo" / "1.0" / "dump.json"
    assert out.exists()
    # Library name is overridden to the -lib flag value (line 329).
    from abicheck.serialization import load_snapshot

    snap = load_snapshot(out)
    assert snap.library == "libfoo"


def test_dump_multiple_libs_emits_warning(
    tiny_so: Path, tmp_path: Path, monkeypatch, capsys
):
    """A descriptor with >1 <libs> entries warns and uses the first (line 302)."""
    other = tmp_path / "missing_other.so"
    desc = _descriptor(tmp_path / "desc2.xml", libs=[tiny_so, other])
    monkeypatch.chdir(tmp_path)
    _dump_callback(desc_path=desc)
    # _do_echo writes to stderr by default.
    assert "2 <libs> entries" in capsys.readouterr().err
    assert (tmp_path / "abi_dumps" / "libfoo" / "1.0" / "dump.json").exists()


def test_dump_failure_during_dump_exits(tiny_so: Path, tmp_path: Path, monkeypatch):
    """Headers in the descriptor force a castxml dump that fails -> _compat_fail."""
    hdr = tmp_path / "foo.h"
    hdr.write_text("int add(int a, int b);\n", encoding="utf-8")
    desc = _descriptor(tmp_path / "desch.xml", libs=[tiny_so], headers=[hdr])
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit) as ei:
        _dump_callback(desc_path=desc)
    # castxml missing classifies as tool-missing exit code 3.
    assert ei.value.code == 3


# ════════════════════════════════════════════════════════════════════════════════
# _apply_result_transforms — optional-transform branches (lines 363, 365, 371)
# ════════════════════════════════════════════════════════════════════════════════


def test_apply_result_transforms_runs_all_optional_branches():
    """warn_newsym + limit_affected + source_only branches all execute."""
    r = _diff_result()
    transformed, full = _apply_result_transforms(
        r,
        warn_newsym=True,
        limit_affected=5,
        source_only=True,
        binary_only=False,
        strict=False,
        strict_mode="off",
    )
    assert transformed is not None
    assert full is not None
    # full_result is captured before source-only filtering.
    assert isinstance(full, DiffResult)


def test_apply_result_transforms_noop_when_flags_off():
    """With every optional flag off the result passes through unchanged."""
    r = _diff_result()
    transformed, full = _apply_result_transforms(
        r,
        warn_newsym=False,
        limit_affected=0,
        source_only=False,
        binary_only=True,
        strict=False,
        strict_mode="off",
    )
    assert transformed is r
    assert full is r


# ════════════════════════════════════════════════════════════════════════════════
# _take_snapshots_with_logging — handler lifecycle (lines 1290-1318)
# ════════════════════════════════════════════════════════════════════════════════


def _file_handler(path: Path) -> logging.FileHandler:
    h = logging.FileHandler(str(path), mode="w", encoding="utf-8")
    return h


def test_take_snapshots_with_logging_success_cycles_handlers(tmp_path: Path):
    """Both per-phase handlers are attached then removed+closed around dumps."""
    old = AbiSnapshot(library="libfoo.so", version="1.0")
    new = AbiSnapshot(library="libfoo.so", version="2.0")
    h1 = _file_handler(tmp_path / "l1.log")
    h2 = _file_handler(tmp_path / "l2.log")
    logger = logging.getLogger("abicheck")
    try:
        old_snap, old_v, new_snap, new_v = _take_snapshots_with_logging(
            old,
            new,
            tmp_path / "o.xml",
            tmp_path / "n.xml",
            None,
            None,
            h1,
            h2,
            headers_list_path=None,
            single_header=None,
            skip_headers_set=set(),
            quiet=True,
            gcc_path=None,
            gcc_prefix=None,
            gcc_options=None,
            sysroot=None,
            nostdinc=False,
            lang=None,
        )
    finally:
        for h in (h1, h2):
            if h in logger.handlers:
                logger.removeHandler(h)
            h.close()
    assert (old_v, new_v) == ("1.0", "2.0")
    assert old_snap is old and new_snap is new
    # Handlers were removed from the logger by the function itself.
    assert h1 not in logger.handlers
    assert h2 not in logger.handlers


def test_take_snapshots_with_logging_failure_closes_handlers(tmp_path: Path):
    """A snapshot-build exception closes both handlers and exits via _compat_fail."""
    new = AbiSnapshot(library="libfoo.so", version="2.0")
    h1 = _file_handler(tmp_path / "l3.log")
    h2 = _file_handler(tmp_path / "l4.log")
    logger = logging.getLogger("abicheck")
    try:
        with pytest.raises(SystemExit) as ei:
            _take_snapshots_with_logging(
                object(),  # neither AbiSnapshot nor descriptor -> AttributeError
                new,
                tmp_path / "o.xml",
                tmp_path / "n.xml",
                None,
                None,
                h1,
                h2,
                headers_list_path=None,
                single_header=None,
                skip_headers_set=set(),
                quiet=True,
                gcc_path=None,
                gcc_prefix=None,
                gcc_options=None,
                sysroot=None,
                nostdinc=False,
                lang=None,
            )
    finally:
        for h in (h1, h2):
            if h in logger.handlers:
                logger.removeHandler(h)
            h.close()
    # "during dump" context classifies as pipeline failure exit code 8.
    assert ei.value.code == 8


# ════════════════════════════════════════════════════════════════════════════════
# _resolve_report_path_and_mkdir — mkdir OSError (lines 1345-1346)
# ════════════════════════════════════════════════════════════════════════════════


def test_resolve_report_path_mkdir_failure_exits(tmp_path: Path):
    """A report path whose parent is a regular file fails mkdir -> exit 7."""
    blocker = tmp_path / "afile"
    blocker.write_text("x", encoding="utf-8")
    report_path = blocker / "sub" / "report.html"
    with pytest.raises(SystemExit) as ei:
        _resolve_report_path_and_mkdir(
            report_path, "libfoo", "1.0", "2.0", "html", True
        )
    assert ei.value.code == 7


def test_resolve_report_path_default_derivation(tmp_path: Path, monkeypatch):
    """When report_path is None a default compat_reports path is derived + created."""
    monkeypatch.chdir(tmp_path)
    resolved = _resolve_report_path_and_mkdir(
        None, "libfoo", "1.0", "2.0", "html", True
    )
    assert resolved.parent.is_dir()
    assert resolved.name == "compat_report.html"
    assert "compat_reports" in resolved.parts


# ════════════════════════════════════════════════════════════════════════════════
# _print_summary_and_exit — API_BREAK exit code (line 1471)
# ════════════════════════════════════════════════════════════════════════════════


def test_print_summary_api_break_exits_2(capsys):
    """An API_BREAK verdict prints the summary and exits with code 2."""
    with pytest.raises(SystemExit) as ei:
        _print_summary_and_exit(_diff_result(), "API_BREAK", False, Path("r.html"))
    assert ei.value.code == 2
    # _do_echo writes to stderr by default.
    assert "Verdict: API_BREAK" in capsys.readouterr().err


def test_print_summary_breaking_exits_1(capsys):
    """A BREAKING verdict exits with code 1 (companion to the API_BREAK case)."""
    with pytest.raises(SystemExit) as ei:
        _print_summary_and_exit(_diff_result(), "BREAKING", True, Path("r.html"))
    assert ei.value.code == 1


# ════════════════════════════════════════════════════════════════════════════════
# compat check — logging-setup failure (lines 881-882)
# ════════════════════════════════════════════════════════════════════════════════


def test_check_logging_setup_failure_exits(tmp_path: Path):
    """A -log-path under a regular file makes _setup_logging raise OSError -> exit 6."""
    blocker = tmp_path / "afile"
    blocker.write_text("x", encoding="utf-8")
    bad_log = blocker / "sub" / "log.txt"
    result = CliRunner().invoke(
        main,
        [
            "compat",
            "check",
            "-lib",
            "x",
            "-old",
            str(tmp_path / "o.xml"),
            "-new",
            str(tmp_path / "n.xml"),
            "-log-path",
            str(bad_log),
        ],
    )
    assert result.exit_code == 6
    assert "setting up logging" in result.output
