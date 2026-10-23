"""The Action's ``suppress`` input: one ``--suppress`` per line, never per word.

``compare --suppress`` is repeatable, so a build-variant matrix passes the
exceptions every variant shares and the variant's own file. The input used to
forward its whole value as one argument (a multi-line value became one path
with a newline in it). Splitting it the way the repeatable inputs do would
word-split a single-line path containing a space -- an existing user's value
changing meaning. The rule: split on newlines only.

Oracle: the expected argv is written out per case, independently of
``run.sh``'s helper, and compared against the argv the stub ``abicheck``
actually received.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from test_action_coverage_verdict import (  # noqa: F401
    _lib,
    _run_action,
    _stub_abicheck,
    pytestmark,
)

_REPORT = {"verdict": "COMPATIBLE", "run_outcome": {"compatibility": "COMPATIBLE"}}


def _suppress_args(tmp_path: Path, value: str | None) -> list[str]:
    bindir = _stub_abicheck(tmp_path, exit_code=0, report=_REPORT)
    real = bindir / "abicheck.real"
    (bindir / "abicheck").rename(real)
    log = tmp_path / "argv.bin"
    wrapper = bindir / "abicheck"
    wrapper.write_text(
        f'#!/usr/bin/env bash\nprintf "%s\\0" "$@" > "{log}"\nexec "{real}" "$@"\n',
        encoding="utf-8",
    )
    wrapper.chmod(0o755)
    env = {
        "INPUT_MODE": "compare",
        "INPUT_OLD_LIBRARY": _lib(tmp_path, "libold.so"),
        "INPUT_NEW_LIBRARY": _lib(tmp_path, "libnew.so"),
        "INPUT_FORMAT": "json",
        "INPUT_OUTPUT_FILE": str(tmp_path / "report.json"),
    }
    if value is not None:
        env["INPUT_SUPPRESS"] = value
    _run_action(tmp_path, env, bindir)
    argv = [a for a in log.read_bytes().decode().split("\0") if a]
    return [argv[i + 1] for i, a in enumerate(argv) if a == "--suppress"]


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, []),
        ("", []),
        ("sup.yml", ["sup.yml"]),
        ("my exceptions/sup file.yml", ["my exceptions/sup file.yml"]),
        ("common.yml\nilp64.yml", ["common.yml", "ilp64.yml"]),
        ("common.yml\n\n  \nilp64.yml\n", ["common.yml", "ilp64.yml"]),
        ("a b.yml\nc d.yml", ["a b.yml", "c d.yml"]),
        ("one.yml\ntwo.yml\nthree.yml", ["one.yml", "two.yml", "three.yml"]),
    ],
)
def test_each_line_is_one_suppress_argument(
    tmp_path: Path, value: str | None, expected: list[str]
) -> None:
    assert _suppress_args(tmp_path, value) == expected
