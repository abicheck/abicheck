# SPDX-License-Identifier: Apache-2.0
"""``compat check`` labels a source-only HTML report as a source report.

Bug class: a renderer parameter the front end has the value for and never
passes. ``write_html_report`` took a ``report_kind`` that no caller passed, so
every ABICC-layout report (``-old-style``) was titled "Binary compatibility
report" and carried ``kind:binary`` in the metadata comment ABICC tooling
parses -- including the source-only reports ``-source`` and
``-src-report-path`` write.

The oracle is ABICC's own flag table, stated here rather than taken from the
code: the primary report is source-only when ``-source`` is given without
``-binary``; ``-bin-report-path`` is always a binary report and
``-src-report-path`` always a source report.
"""

from __future__ import annotations

import itertools
import re
from pathlib import Path

import pytest
from click.testing import CliRunner

from abicheck.cli import main
from abicheck.model import AbiSnapshot, Function, Visibility
from abicheck.serialization import save_snapshot

_KIND = re.compile(r"kind:(\w+);")
_TITLE = re.compile(r"<title>(\w+) compatibility report")


def _expected_kinds(source: bool, binary: bool, bin_split: bool, src_split: bool):
    kinds = {"report.html": "source" if source and not binary else "binary"}
    if bin_split:
        kinds["bin.html"] = "binary"
    if src_split:
        kinds["src.html"] = "source"
    return kinds


@pytest.fixture(scope="module")
def snapshots(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    root = tmp_path_factory.mktemp("snaps")
    keep = Function(
        name="keep", mangled="keep", return_type="int", visibility=Visibility.PUBLIC
    )
    gone = Function(
        name="gone", mangled="gone", return_type="int", visibility=Visibility.PUBLIC
    )
    old, new = root / "old.json", root / "new.json"
    save_snapshot(
        AbiSnapshot(library="libfoo.so", version="1.0", functions=[keep, gone]), old
    )
    save_snapshot(
        AbiSnapshot(library="libfoo.so", version="2.0", functions=[keep]), new
    )
    return old, new


@pytest.mark.parametrize(
    ("source", "binary", "bin_split", "src_split"),
    list(itertools.product((False, True), repeat=4)),
)
def test_every_written_report_carries_the_kind_its_flags_select(
    snapshots, tmp_path: Path, source, binary, bin_split, src_split
) -> None:
    old, new = snapshots
    args = ["compat", "check", "-lib", "libfoo", "-old", str(old), "-new", str(new)]
    args += ["-old-style", "-report-path", str(tmp_path / "report.html")]
    args += ["-source"] if source else []
    args += ["-binary"] if binary else []
    args += ["-bin-report-path", str(tmp_path / "bin.html")] if bin_split else []
    args += ["-src-report-path", str(tmp_path / "src.html")] if src_split else []

    result = CliRunner().invoke(main, args)

    assert result.exit_code in (0, 1, 2), result.output
    expected = _expected_kinds(source, binary, bin_split, src_split)
    written = {p.name for p in tmp_path.glob("*.html")}
    assert written == set(expected)
    for name, kind in expected.items():
        page = (tmp_path / name).read_text(encoding="utf-8")
        assert _KIND.findall(page) == [kind], name
        assert _TITLE.findall(page) == [kind.capitalize()], name


def test_the_oracle_is_not_constant() -> None:
    """Both kinds occur, for the primary report and across the split reports."""
    combos = list(itertools.product((False, True), repeat=4))
    primary = {_expected_kinds(*c)["report.html"] for c in combos}
    every = {k for c in combos for k in _expected_kinds(*c).values()}
    assert primary == every == {"binary", "source"}
