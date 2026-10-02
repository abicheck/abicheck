"""A `dump` baseline records the `--exclude-header` patterns it was built under.

Known gap "The native `dump` CLI does not stamp `excluded_header_patterns` on
the snapshot it writes" (2026-09-16) stopped reproducing once `dump` began
executing through `workflows.input_resolution.resolve_input`, the one place
that stamps the achieved patterns. Nothing pinned that through the `dump`
CLI, and the stamp is the *entire* input to the ADR-050 comparability gate
that refuses a baseline/candidate pair narrowed by different rules. These
tests pin the stamp and the gate it feeds, through the public commands.
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
from abicheck.serialization import load_snapshot

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(sys.platform != "linux", reason="ELF/DWARF tests require Linux"),
    pytest.mark.skipif(
        shutil.which("gcc") is None or shutil.which("castxml") is None,
        reason="needs gcc + castxml",
    ),
]


def _library(tmp_path: Path) -> tuple[Path, Path]:
    inc = tmp_path / "inc"
    inc.mkdir()
    (inc / "a.h").write_text("int a(void);\n")
    (inc / "c2.h").write_text("int c2(void);\n")
    src = tmp_path / "x.c"
    src.write_text("int a(void) { return 1; }\nint c2(void) { return 2; }\n")
    lib = tmp_path / "libx.so"
    subprocess.run(["gcc", "-shared", "-fPIC", str(src), "-o", str(lib)], check=True)
    return lib, inc


def _dump(tmp_path: Path, lib: Path, inc: Path, *exclude: str) -> Path:
    out = tmp_path / f"base{'-'.join(('',) + exclude)}.json"
    args = ["dump", str(lib), "-H", str(inc), "-o", str(out)]
    for pattern in exclude:
        args += ["--exclude-header", pattern]
    result = CliRunner().invoke(main, args)
    assert result.exit_code == 0, result.output
    return out


def test_dump_stamps_the_achieved_patterns(tmp_path: Path) -> None:
    lib, inc = _library(tmp_path)
    snap = load_snapshot(_dump(tmp_path, lib, inc, "c2.h"))
    assert snap.excluded_header_patterns == ("c2.h",)
    assert snap.excluded_header_matching == "glob"
    assert {f.name for f in snap.declarations.functions} == {"a"}


def test_a_pattern_that_matched_nothing_is_not_stamped(tmp_path: Path) -> None:
    """Only *achieved* narrowing is recorded -- a typo excludes nothing."""
    lib, inc = _library(tmp_path)
    snap = load_snapshot(_dump(tmp_path, lib, inc, "nope.h"))
    assert snap.excluded_header_patterns == ()


def test_no_flag_stamps_nothing(tmp_path: Path) -> None:
    lib, inc = _library(tmp_path)
    assert load_snapshot(_dump(tmp_path, lib, inc)).excluded_header_patterns == ()


@pytest.mark.parametrize(
    ("baseline", "candidate", "comparable"),
    [
        (("c2.h",), ("c2.h",), True),
        (("c2.h",), ("a.h",), False),
        (("c2.h",), (), False),
        ((), ("c2.h",), False),
        ((), (), True),
    ],
)
def test_dump_baseline_feeds_the_comparability_gate(
    tmp_path: Path,
    baseline: tuple[str, ...],
    candidate: tuple[str, ...],
    comparable: bool,
) -> None:
    """Oracle: a pair is comparable iff both sides excluded the same set."""
    lib, inc = _library(tmp_path)
    base = _dump(tmp_path, lib, inc, *baseline)
    args = ["compare", str(base), str(lib), "-H", str(inc), "-o", "json=-"]
    for pattern in candidate:
        args += ["--exclude-header", pattern]
    result = CliRunner().invoke(main, args)
    if comparable:
        doc = json.loads(result.output[result.output.index("{") :])
        assert doc["verdict"] == "NO_CHANGE", result.output
    else:
        assert "--exclude-header patterns differ" in result.output, result.output
        assert result.exit_code != 0
