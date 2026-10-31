"""``dump --bundle-facts-out`` on real gcc-built libraries.

The JSON-snapshot parity cases live in ``tests/test_dump_bundle_facts.py``.
These add what those cannot reach: members dumped from ELF binaries (with
and without headers), and the round trip through the stored driver, which
pairs a stored OLD side with a live directory of binaries.
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
from abicheck.storage.evidence_digest import VOLATILE_CAPTURE_FIELDS

#: What a member snapshot records about its *capture* rather than the
#: binary: the evidence digest's volatile set, plus the file size the
#: comparison's member path stamps beside the mtime (a scalar `dump` stamps
#: neither).
_CAPTURE_FIELDS = VOLATILE_CAPTURE_FIELDS | {"source_size"}


def _evidence(doc: dict) -> dict:
    members = {
        key: {k: v for k, v in snap.items() if k not in _CAPTURE_FIELDS}
        for key, snap in doc["per_library_snapshots"].items()
    }
    return {**doc, "per_library_snapshots": members}


pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        sys.platform != "linux",
        reason="GNU ld -soname and ELF members; bundle facts are ELF/Linux-only "
        "(ADR-018/ADR-023).",
    ),
]

#: (library, OLD source, NEW source): a removal, an addition, a return-type
#: change, and an unchanged library.
_LIBS = (
    (
        "liba",
        "int a(void){return 1;}\nint b(void){return 2;}\n",
        "int a(void){return 1;}\n",
    ),
    ("libb", "int v = 1;\n", "int v = 1;\nint w = 2;\n"),
    ("libc", "int c(void){return 3;}\n", "int c(void){return 3;}\n"),
)


def _gcc() -> str:
    gcc = shutil.which("gcc")
    if gcc is None:
        pytest.skip("gcc not available")
    return gcc


def _build(root: Path, side: int) -> tuple[Path, Path]:
    libs, inc = root / "lib", root / "include"
    libs.mkdir(parents=True)
    inc.mkdir(parents=True)
    for name, *sources in _LIBS:
        src = root / f"{name}.c"
        src.write_text(sources[side], encoding="utf-8")
        decls = "".join(
            f"{line.split('{')[0].strip()};\n"
            if "(" in line
            else f"extern {line.split('=')[0].strip()};\n"
            for line in sources[side].splitlines()
        )
        (inc / f"{name}.h").write_text(decls, encoding="utf-8")
        res = subprocess.run(
            [
                _gcc(),
                "-shared",
                "-fPIC",
                "-g",
                "-O0",
                str(src),
                "-o",
                str(libs / f"{name}.so"),
                f"-Wl,-soname,{name}.so",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if res.returncode != 0:
            pytest.fail(f"gcc failed for {name}: {res.stderr}")
    return libs, inc


def _invoke(*args: str):
    return CliRunner().invoke(main, list(args))


@pytest.mark.parametrize("with_headers", [False, True], ids=["binary", "headers"])
def test_dump_matches_compare_and_round_trips(
    tmp_path: Path, with_headers: bool
) -> None:
    if with_headers and shutil.which("castxml") is None:
        pytest.skip("castxml not available")
    old_libs, old_inc = _build(tmp_path / "old", 0)
    new_libs, new_inc = _build(tmp_path / "new", 1)
    old_h = ["-H", str(old_inc)] if with_headers else []
    sided = ["-H", f"old={old_inc}", "-H", f"new={new_inc}"] if with_headers else []
    dumped, compared = tmp_path / "dumped.json", tmp_path / "compared.json"

    # `--label old`: compare's own OLD-side default label, the one field a
    # capture of the same release legitimately differs in.
    result = _invoke(
        "dump",
        str(old_libs),
        *old_h,
        "--label",
        "old",
        "--bundle-facts-out",
        str(dumped),
    )
    assert result.exit_code == 0, result.output
    live = _invoke(
        "compare",
        str(old_libs),
        str(new_libs),
        *sided,
        "--bundle-facts-out",
        str(compared),
        "-o",
        "json=-",
    )
    assert live.exit_code in (0, 1, 2, 4), live.output
    dumped_doc = json.loads(dumped.read_text(encoding="utf-8"))
    compared_doc = json.loads(compared.read_text(encoding="utf-8"))
    assert _evidence(dumped_doc) == _evidence(compared_doc)
    assert sorted(dumped_doc["per_library_snapshots"]) == [
        "liba.so",
        "libb.so",
        "libc.so",
    ]
    assert dumped_doc["degraded_members"] == {}

    new_h = ["-H", f"new={new_inc}"] if with_headers else []
    stored = _invoke("compare", str(dumped), str(new_libs), *new_h, "-o", "json=-")
    assert stored.exit_code == live.exit_code, stored.output
    live_doc, stored_doc = json.loads(live.stdout), json.loads(stored.stdout)
    assert stored_doc["verdict"] == live_doc["verdict"]
    assert {lib["library"]: lib["verdict"] for lib in stored_doc["libraries"]} == {
        lib["library"]: lib["verdict"] for lib in live_doc["libraries"]
    }


def test_the_label_names_every_member(tmp_path: Path) -> None:
    old_libs, _inc = _build(tmp_path / "old", 0)
    dumped = tmp_path / "dumped.json"
    result = _invoke(
        "dump", str(old_libs), "--label", "9.9", "--bundle-facts-out", str(dumped)
    )
    assert result.exit_code == 0, result.output
    doc = json.loads(dumped.read_text(encoding="utf-8"))
    assert {s["version"] for s in doc["per_library_snapshots"].values()} == {"9.9"}
