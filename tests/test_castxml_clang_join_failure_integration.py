# SPDX-License-Identifier: Apache-2.0
"""castxml with a failed clang join: the public compare reads ``partial``.

castxml writes a vector, ``_Complex`` or ``_BitInt`` type as one literal
``Unimplemented`` node; the clang join supplies its spelling. When that join
failed (no clang, a clang parse error) both sides spelled every such type
``Unimplemented``, and a real ABI break -- ``float _Complex`` ->
``double _Complex`` -- compared ``NO_CHANGE`` with assurance ``complete`` and
exit 0. Each case builds two gcc ELF libraries without debug info, so the
header AST is the only evidence, and runs ``dump``/``compare`` through the
CLI with the join forced to fail, then with it working (the positive
control: the break is reported and assurance stays ``complete``).
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from abicheck.cli import main
from abicheck.errors import SnapshotError

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(sys.platform != "linux", reason="gcc emits ELF on Linux only"),
    pytest.mark.skipif(
        not (shutil.which("castxml") and shutil.which("gcc") and shutil.which("clang")),
        reason="needs castxml, gcc and clang",
    ),
]

# (case id, old parameter type, new parameter type)
_CASES = [
    ("complex", "float _Complex", "double _Complex"),
    (
        "vector",
        "float __attribute__((vector_size(16)))",
        "float __attribute__((vector_size(32)))",
    ),
    ("bitint", "_BitInt(8)", "_BitInt(16)"),
]


def _build(root: Path, side: str, ptype: str) -> tuple[Path, Path]:
    d = root / side
    d.mkdir()
    header = d / "api.h"
    header.write_text(f"typedef {ptype} arg_t;\nvoid take(arg_t x);\n")
    src = d / "api.c"
    src.write_text('#include "api.h"\nvoid take(arg_t x) { (void)x; }\n')
    lib = d / "libapi.so"
    subprocess.run(
        [
            "clang" if "_BitInt" in ptype else "gcc",
            "-std=c2x",
            "-shared",
            "-fPIC",
            "-o",
            str(lib),
            str(src),
        ],
        check=True,
        capture_output=True,
    )
    return lib, header


def _compare(tmp_path: Path, old_t: str, new_t: str) -> tuple[int, dict]:
    runner = CliRunner()
    snaps = []
    for side, ptype in (("old", old_t), ("new", new_t)):
        lib, header = _build(tmp_path, side, ptype)
        out = tmp_path / f"{side}.json"
        res = runner.invoke(main, ["dump", str(lib), "-H", str(header), "-o", str(out)])
        assert res.exit_code == 0, res.output
        snaps.append(out)
    report = tmp_path / "report.json"
    res = runner.invoke(
        main, ["compare", str(snaps[0]), str(snaps[1]), "-o", f"json={report}"]
    )
    return res.exit_code, json.loads(report.read_text())


@pytest.mark.parametrize(("case", "old_t", "new_t"), _CASES, ids=[c[0] for c in _CASES])
def test_failed_clang_join_never_reads_clean(
    case: str,
    old_t: str,
    new_t: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import abicheck.extract.headers.castxml.vector_join as vj

    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))

    def clang_fails(*args: object, **kwargs: object) -> None:
        raise SnapshotError("clang unavailable (simulated)")

    monkeypatch.setattr(vj, "clang_header_dump", clang_fails)
    exit_code, report = _compare(tmp_path, old_t, new_t)
    assurance = report["analysis_assurance"]
    # The break itself is unobservable here (another detector may still see a
    # side effect); what must not happen is a ``complete`` claim.
    assert report["verdict"] != "BREAKING"
    assert assurance["status"] == "partial", assurance
    assert any("clang join failed" in n for n in assurance["notes"])
    assert any("unresolved type spellings" in n for n in assurance["notes"])
    assert exit_code == 0  # partial assurance alone does not raise the exit code


@pytest.mark.parametrize(("case", "old_t", "new_t"), _CASES, ids=[c[0] for c in _CASES])
def test_working_clang_join_reports_the_break_with_complete_assurance(
    case: str,
    old_t: str,
    new_t: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    exit_code, report = _compare(tmp_path, old_t, new_t)
    assert report["verdict"] == "BREAKING", report.get("changes")
    assert report["analysis_assurance"]["status"] == "complete"
    assert exit_code == 4


def test_failed_join_snapshot_is_not_served_to_a_later_working_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The gapped snapshot is not cached: once clang works, the same cache
    directory yields the break, not the earlier unobserved reading."""
    import abicheck.extract.headers.castxml.vector_join as vj

    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    real = vj.clang_header_dump

    def clang_fails(*args: object, **kwargs: object) -> None:
        raise SnapshotError("clang unavailable (simulated)")

    monkeypatch.setattr(vj, "clang_header_dump", clang_fails)
    first_dir = tmp_path / "first"
    first_dir.mkdir()
    _, first = _compare(first_dir, *_CASES[0][1:])
    assert first["analysis_assurance"]["status"] == "partial"
    monkeypatch.setattr(vj, "clang_header_dump", real)
    # Re-dump the very same files: the cache keys are identical.
    runner = CliRunner()
    snaps = []
    for side in ("old", "new"):
        d = first_dir / side
        out = tmp_path / f"second_{side}.json"
        res = runner.invoke(
            main, ["dump", str(d / "libapi.so"), "-H", str(d / "api.h"), "-o", str(out)]
        )
        assert res.exit_code == 0, res.output
        snaps.append(out)
    report = tmp_path / "second.json"
    runner.invoke(
        main, ["compare", str(snaps[0]), str(snaps[1]), "-o", f"json={report}"]
    )
    second = json.loads(report.read_text())
    assert second["verdict"] == "BREAKING"
    assert second["analysis_assurance"]["status"] == "complete"
