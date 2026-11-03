"""The GNU_UNIQUE binding of a public inline function's local static is in
the default public contract (case180's mechanism, non-template form); the
same static behind a private header stays out. Live and stored compare must
agree."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests._abicheck_subprocess import abicheck_command

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(sys.platform != "linux", reason="STB_GNU_UNIQUE requires ELF"),
]

HEADER = (
    "#pragma once\n"
    "struct Widget { int id = 42; };\n"
    "inline Widget& registry() { static Widget w; return w; }\n"
    'extern "C" int get_widget_id();\n'
)
SOURCE = '#include "api.h"\nextern "C" int get_widget_id() { return registry().id; }\n'
PRIVATE_HEADER = '#pragma once\nextern "C" int get_widget_id();\n'


def _gxx_with_gnu_unique() -> str:
    gxx = shutil.which("g++")
    if gxx is None:
        pytest.skip("g++ not available")
    out = subprocess.run([gxx, "--version"], capture_output=True, text=True).stdout
    if "clang" in out.lower():
        pytest.skip("g++ is clang: no STB_GNU_UNIQUE to lose")
    return gxx


@pytest.fixture(scope="module")
def libs(tmp_path_factory):
    gxx = _gxx_with_gnu_unique()
    d = tmp_path_factory.mktemp("unique")
    (d / "api.h").write_text(HEADER)
    (d / "api.cpp").write_text(SOURCE)
    (d / "priv").mkdir()
    (d / "priv" / "api.h").write_text(PRIVATE_HEADER)
    for name, extra in (("libv1.so", []), ("libv2.so", ["-fno-gnu-unique"])):
        subprocess.run(
            [
                gxx,
                "-g",
                "-shared",
                "-fPIC",
                *extra,
                str(d / "api.cpp"),
                "-o",
                str(d / name),
            ],
            check=True,
            cwd=d,
        )
    return d


def _kinds(report: Path) -> tuple[str, set[str]]:
    doc = json.loads(report.read_text())
    return doc["verdict"], {c["kind"] for c in doc["changes"]}


def _compare(d: Path, header: Path, *, stored: bool, tag: str) -> tuple[str, set[str]]:
    out = d / f"{tag}.json"
    if stored:
        snaps = []
        for v in ("v1", "v2"):
            snap = d / f"{tag}-{v}.abi.json"
            subprocess.run(
                abicheck_command("dump", d / f"lib{v}.so", "-H", header, "-o", snap),
                check=True,
                capture_output=True,
            )
            snaps.append(snap)
        cmd = abicheck_command("compare", *snaps, "-o", f"json={out}")
    else:
        cmd = abicheck_command(
            "compare",
            d / "libv1.so",
            d / "libv2.so",
            "-H",
            f"old={header}",
            "-H",
            f"new={header}",
            "-o",
            f"json={out}",
        )
    subprocess.run(cmd, capture_output=True)
    return _kinds(out)


@pytest.mark.parametrize("stored", [False, True])
def test_public_local_static_binding_loss_is_reported(libs, stored):
    verdict, kinds = _compare(libs, libs / "api.h", stored=stored, tag=f"pub{stored}")
    assert "symbol_binding_lost_unique" in kinds
    assert verdict == "COMPATIBLE_WITH_RISK"


@pytest.mark.parametrize("stored", [False, True])
def test_private_local_static_stays_out_of_contract(libs, stored):
    verdict, kinds = _compare(
        libs, libs / "priv" / "api.h", stored=stored, tag=f"priv{stored}"
    )
    assert "symbol_binding_lost_unique" not in kinds
    assert verdict == "NO_CHANGE"
