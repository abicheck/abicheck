"""End to end: a removal's disposition reaches the surface roll-up it feeds.

Removing a public function also lowers the public declaration count, so the
run carries a ``public_surface_shrank`` roll-up for the same removal. The
roll-up is disposed of together with the removals it summarises:

* a suppressed removal hides its roll-up (the run is clean, NO_CHANGE, and
  both are recorded as suppressed);
* an acknowledged, overridden or reclassified removal stays visible, and so
  does its roll-up, which is never a break on its own.

Negative controls: a rule on another symbol leaves the roll-up in place, and
so does a sibling removal that stays visible. ``-H`` runs take the same path.

Linux only: the libraries are built with gcc.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests._abicheck_subprocess import abicheck_command, abicheck_env

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not sys.platform.startswith("linux") or shutil.which("gcc") is None,
        reason="builds ELF libraries with gcc; Linux only",
    ),
]

ROLLUP = "public_surface_shrank"
_REMOVAL_KINDS = frozenset({"func_removed", "func_removed_elf_only"})
_KEEP = "int keep_fn(void) { return 1; }\n"
_NEW_SRC = _KEEP
_OLD_ONE = _KEEP + "int gone_fn(void) { return 2; }\n"
_OLD_TWO = _OLD_ONE + "int gone_two(void) { return 3; }\n"
_NEW_HDR = "int keep_fn(void);\n"
_OLD_ONE_HDR = "int keep_fn(void);\nint gone_fn(void);\n"
_OLD_TWO_HDR = _OLD_ONE_HDR + "int gone_two(void);\n"


def _build(tmp: Path, name: str, src: str) -> Path:
    source = tmp / f"{name}.c"
    source.write_text(src)
    lib = tmp / f"lib{name}.so"
    subprocess.run(
        ["gcc", "-shared", "-fPIC", "-O0", str(source), "-o", str(lib)], check=True
    )
    return lib


@pytest.fixture
def libs(tmp_path: Path) -> dict[str, Path]:
    """One new library, and an old library with one or two removed functions."""
    files: dict[str, Path] = {
        "new_lib": _build(tmp_path, "new", _NEW_SRC),
        "old_one_lib": _build(tmp_path, "old_one", _OLD_ONE),
        "old_two_lib": _build(tmp_path, "old_two", _OLD_TWO),
    }
    for stem, text in (
        ("new.h", _NEW_HDR),
        ("old_one.h", _OLD_ONE_HDR),
        ("old_two.h", _OLD_TWO_HDR),
    ):
        (tmp_path / stem).write_text(text)
        files[stem] = tmp_path / stem
    return files


def _write(tmp: Path, name: str, body: str) -> Path:
    path = tmp / f"{name}.yml"
    path.write_text(body)
    return path


def _run(
    tmp: Path,
    libs: dict[str, Path],
    *,
    old: str,
    tag: str,
    with_headers: bool,
    extra: list[str],
) -> tuple[int, dict]:
    report = tmp / f"report-{tag}.json"
    args: list[str] = [
        "compare",
        str(libs[f"{old}_lib"]),
        str(libs["new_lib"]),
        *extra,
        "-o",
        f"json={report}",
    ]
    if with_headers:
        args += [
            "-H",
            f"old={libs[f'{old}.h']}",
            "-H",
            f"new={libs['new.h']}",
        ]
    proc = subprocess.run(
        abicheck_command(*args),
        capture_output=True,
        text=True,
        env=abicheck_env(),
        check=False,
    )
    return proc.returncode, json.loads(report.read_text())


def _visible(report: dict) -> set[str]:
    return {c["kind"] for c in report["changes"]}


def _visible_symbols(report: dict, kinds: frozenset[str]) -> set[str]:
    return {c["symbol"] for c in report["changes"] if c["kind"] in kinds}


def _suppressed(report: dict) -> list[dict]:
    return report["suppression"]["suppressed_changes"]


def _suppress_rule(tmp: Path, symbol: str, change_kind: str = "func_removed") -> Path:
    return _write(
        tmp,
        f"supp-{symbol}",
        "version: 1\n"
        "suppressions:\n"
        f"  - change_kind: {change_kind}\n"
        f"    symbol: {symbol}\n"
        "    reason: removed on purpose; consumers migrated\n",
    )


@pytest.mark.parametrize("with_headers", [False, True], ids=["headerless", "headers"])
def test_suppressed_removal_hides_its_roll_up(
    tmp_path: Path, libs: dict[str, Path], with_headers: bool
) -> None:
    code, report = _run(
        tmp_path,
        libs,
        old="old_one",
        tag=f"supp-{with_headers}",
        with_headers=with_headers,
        extra=["--suppress", str(_suppress_rule(tmp_path, "gone_fn"))],
    )
    assert code == 0
    assert report["verdict"] == "NO_CHANGE"
    assert not _visible(report) & {ROLLUP, *_REMOVAL_KINDS}
    suppressed = _suppressed(report)
    assert {c["kind"] for c in suppressed} >= {ROLLUP}
    roll_up = next(c for c in suppressed if c["kind"] == ROLLUP)
    # The roll-up names the rule that hid its removal, not an empty rule.
    assert roll_up["rule"]["reason"] == "removed on purpose; consumers migrated"
    assert roll_up["rule"]["rule_id"] is not None
    assert report["disposition_audit"]["counts"]["suppressed"] == 2


@pytest.mark.parametrize("with_headers", [False, True], ids=["headerless", "headers"])
def test_rule_on_another_symbol_keeps_the_roll_up(
    tmp_path: Path, libs: dict[str, Path], with_headers: bool
) -> None:
    code, report = _run(
        tmp_path,
        libs,
        old="old_one",
        tag=f"other-{with_headers}",
        with_headers=with_headers,
        extra=["--suppress", str(_suppress_rule(tmp_path, "keep_fn"))],
    )
    assert code == 4
    assert report["verdict"] == "BREAKING"
    assert _visible_symbols(report, _REMOVAL_KINDS) == {"gone_fn"}
    assert ROLLUP in _visible(report)
    assert ROLLUP not in {c["kind"] for c in _suppressed(report)}


@pytest.mark.parametrize("with_headers", [False, True], ids=["headerless", "headers"])
def test_sibling_removal_still_visible_keeps_the_roll_up(
    tmp_path: Path, libs: dict[str, Path], with_headers: bool
) -> None:
    code, report = _run(
        tmp_path,
        libs,
        old="old_two",
        tag=f"sibling-{with_headers}",
        with_headers=with_headers,
        extra=["--suppress", str(_suppress_rule(tmp_path, "gone_fn"))],
    )
    assert code == 4
    assert report["verdict"] == "BREAKING"
    assert _visible_symbols(report, _REMOVAL_KINDS) == {"gone_two"}
    assert ROLLUP in _visible(report)
    assert ROLLUP not in {c["kind"] for c in _suppressed(report)}


@pytest.mark.parametrize("with_headers", [False, True], ids=["headerless", "headers"])
def test_every_removal_suppressed_hides_the_roll_up(
    tmp_path: Path, libs: dict[str, Path], with_headers: bool
) -> None:
    supp = _write(
        tmp_path,
        "supp-both",
        "version: 1\n"
        "suppressions:\n"
        "  - change_kind: func_removed\n"
        "    symbol: gone_fn\n"
        "    reason: removed on purpose\n"
        "  - change_kind: func_removed\n"
        "    symbol: gone_two\n"
        "    reason: removed on purpose\n",
    )
    code, report = _run(
        tmp_path,
        libs,
        old="old_two",
        tag=f"both-{with_headers}",
        with_headers=with_headers,
        extra=["--suppress", str(supp)],
    )
    assert code == 0
    assert report["verdict"] == "NO_CHANGE"
    assert not _visible(report) & {ROLLUP, *_REMOVAL_KINDS}
    assert report["disposition_audit"]["counts"]["suppressed"] == 3


def test_acknowledged_removal_acknowledges_the_roll_up(
    tmp_path: Path, libs: dict[str, Path]
) -> None:
    # Headerless only: with -H a `symbol:` record matches the header removal and
    # its deduplicated DWARF twin, and raises AmbiguousAcknowledgmentError. That
    # is a separate, pre-existing acknowledgment issue, not this roll-up's.
    with_headers = False
    ack = _write(
        tmp_path,
        "ack",
        "version: 1\n"
        "acknowledgments:\n"
        "  - symbol: gone_fn\n"
        "    change_kind: func_removed\n"
        "    reason: deliberate removal\n",
    )
    code, report = _run(
        tmp_path,
        libs,
        old="old_one",
        tag=f"ack-{with_headers}",
        with_headers=with_headers,
        extra=["--acknowledgments", str(ack)],
    )
    # Acknowledgment keeps the break and its gate (breaks: gate is the default).
    assert code == 4
    assert report["verdict"] == "BREAKING"
    assert ROLLUP in _visible(report)
    # The removal and the roll-up are both acknowledged, by one record.
    assert report["disposition_audit"]["acknowledged_total"] == 2


@pytest.mark.parametrize("with_headers", [False, True], ids=["headerless", "headers"])
def test_base_override_leaves_the_roll_up_non_breaking(
    tmp_path: Path, libs: dict[str, Path], with_headers: bool
) -> None:
    policy = _write(tmp_path, "override", "overrides:\n  func_removed: ignore\n")
    code, report = _run(
        tmp_path,
        libs,
        old="old_one",
        tag=f"override-{with_headers}",
        with_headers=with_headers,
        extra=["--policy", str(policy)],
    )
    assert code == 0
    assert report["verdict"] == "COMPATIBLE"
    # The removal stays visible at its overridden verdict; so does its roll-up.
    assert _visible_symbols(report, _REMOVAL_KINDS) == {"gone_fn"}
    roll_up = [c for c in report["changes"] if c["kind"] == ROLLUP]
    assert [c["severity"] for c in roll_up] == ["compatible"]


@pytest.mark.parametrize("with_headers", [False, True], ids=["headerless", "headers"])
def test_reclassified_removal_leaves_the_roll_up_non_breaking(
    tmp_path: Path, libs: dict[str, Path], with_headers: bool
) -> None:
    policy = _write(
        tmp_path,
        "reclassify",
        "reclassify:\n"
        "  - kind: func_removed\n"
        "    symbol: gone_fn\n"
        "    to: risk\n"
        "    reason: consumers tolerate the removal\n",
    )
    code, report = _run(
        tmp_path,
        libs,
        old="old_one",
        tag=f"reclass-{with_headers}",
        with_headers=with_headers,
        extra=["--policy", str(policy)],
    )
    assert code == 0
    assert report["verdict"] == "COMPATIBLE_WITH_RISK"
    assert _visible_symbols(report, _REMOVAL_KINDS) == {"gone_fn"}
    roll_up = [c for c in report["changes"] if c["kind"] == ROLLUP]
    assert [c["severity"] for c in roll_up] == ["compatible"]
