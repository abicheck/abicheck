"""Frozen Itanium C++ consumer of a changed public base path."""

import json
import os
import subprocess
import sys

import pytest

from tests._abicheck_subprocess import abicheck_command


@pytest.mark.integration
@pytest.mark.skipif(sys.platform != "linux", reason="compiles shared ELF libraries")
@pytest.mark.parametrize(
    "link_flags",
    [(), ("-Wl,-Bsymbolic",), ("-Wl,-Bsymbolic", "-Wl,-z,pack-relative-relocs")],
    ids=["preemptible", "local", "packed"],
)
def test_public_base_path_controls_crosscast_and_catch(tmp_path, link_flags):
    from abiforge.run.base_access_consumers import build_cases
    from abiforge.run.itanium_base_edges import direct_base_flags

    exe, libraries = build_cases(tmp_path, link_flags)
    export_sets = []
    for access, library in libraries.items():
        root = library.parent
        if "-Wl,-z,pack-relative-relocs" in link_flags:
            dynamic = subprocess.check_output(["readelf", "-dW", library], text=True)
            if "(RELR)" not in dynamic:
                pytest.skip("linker did not emit packed relative relocations")
        if os.uname().machine == "x86_64" and not link_flags:
            edges = direct_base_flags(library, "_ZTI4Item")
            assert edges is not None
            assert edges["_ZTI6Anchor"] == 2
            assert edges["_ZTI8Category"] == (8 << 8) | (2 if access == "public" else 0)
            assert direct_base_flags(library, "_ZTI6Anchor") is None
        exports = subprocess.check_output(
            ["nm", "-D", "--defined-only", "-P", library], text=True
        )
        export_sets.append({line.split()[0] for line in exports.splitlines()})
        output = subprocess.check_output(
            [exe], env={**os.environ, "LD_LIBRARY_PATH": str(root)}, text=True
        )
        expected = (
            ["public edge", "category", "base handler"]
            if access == "public"
            else ["nonpublic edge", "no category", "fallback handler"]
        )
        assert output.splitlines() == expected
    assert export_sets[0] == export_sets[1] == export_sets[2]
    if os.uname().machine == "x86_64":
        for access in ("public", "protected", "private"):
            root = tmp_path / access
            subprocess.run(
                abicheck_command(
                    "dump",
                    root / "libitem.so",
                    "-o",
                    root / "snap.json",
                ),
                check=True,
            )
        for access in ("protected", "private"):
            report = tmp_path / f"{access}.json"
            compared = subprocess.run(
                abicheck_command(
                    "compare",
                    tmp_path / "public/snap.json",
                    tmp_path / access / "snap.json",
                    "-o",
                    f"json={report}",
                )
            )
            assert compared.returncode == 4
            rows = json.loads(report.read_text())["changes"]
            assert [(row["kind"], row["symbol"]) for row in rows] == [
                ("rtti_inheritance_changed", "_ZTI4Item")
            ]
            assert "Category" in rows[0]["description"]
