# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""Issue #1715: a stored-OLD/live-NEW comparison keeps NEW's public headers.

``compare <stored bundle facts> <live dir> --header new=...`` dumped each
live NEW member with its headers but without them as the *public-header
set*, so no NEW declaration got a public origin, NEW's release surface read
"public surface unresolved", and the release-level header-versus-exports
check (declared in a public header, exported by no member) judged only the
stored OLD release. A PR that introduced such a gap passed unflagged.

The invariant, stated across both drivers (live/live and stored/live), both
``-H`` spellings (a directory and a file list) and a small exhaustive domain
of which side declares an unexported symbol: each side's
``missing_from_bundle`` set equals that side's declared-minus-exported set,
computed here from the fixture definition, not from abicheck. Marked
``integration``: it needs castxml plus a toolchain, because the defect lived
in the CLI-to-extraction wiring a hand-built snapshot cannot reach.

Run for each binary format the host can emit: ELF via gcc on Linux, and PE
via ``clang --target=x86_64-pc-windows-msvc`` + ``lld-link`` on any host.
The PE leg is the Windows CI failure reproduced on Linux: the stored/live
driver discovered NEW's members with an ELF-only walk, so a directory of
PE libraries supplied no member, NEW's surface came back unresolved and
``missing_from_bundle`` read empty while live/live was correct.
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

pytestmark = pytest.mark.integration

#: Exported by liba / libb respectively, on both sides.
_EXPORTED = {"a": ("a_one",), "b": ("b_one",)}

#: (old-only gaps, new-only gaps): declared in liba's header, exported nowhere.
_SCENARIOS = {
    "new_only": ((), ("gap_new",)),
    "old_only": (("gap_old",), ()),
    "both_persistent_and_new": (("gap_kept",), ("gap_kept", "gap_new")),
    "none": ((), ()),
}


#: Tools each binary format's fixture build needs.
_TOOLCHAINS = {"elf": ("gcc", "castxml"), "pe": ("clang", "lld-link", "castxml")}


def _require_toolchain(fmt: str) -> None:
    if fmt == "elf" and not sys.platform.startswith("linux"):
        pytest.skip("gcc emits ELF only on Linux")
    for tool in _TOOLCHAINS[fmt]:
        if shutil.which(tool) is None:
            pytest.skip(f"{tool} is required for the {fmt} fixture")


def _link(fmt: str, src: Path, out: Path, exported: tuple[str, ...]) -> None:
    if fmt == "elf":
        cmds = [["gcc", "-shared", "-fPIC", "-o", str(out), str(src)]]
    else:
        obj = src.with_suffix(".obj")
        cmds = [
            [
                "clang",
                "--target=x86_64-pc-windows-msvc",
                "-c",
                str(src),
                "-o",
                str(obj),
            ],
            [
                "lld-link",
                "/dll",
                "/noentry",
                "/nodefaultlib",
                *(f"/export:{n}" for n in exported),
                f"/out:{out}",
                str(obj),
            ],
        ]
    for cmd in cmds:
        subprocess.run(cmd, check=True, capture_output=True)


def _build_side(root: Path, gaps: tuple[str, ...], fmt: str) -> Path:
    root = root.resolve()
    (root / "include").mkdir(parents=True)
    (root / "lib").mkdir()
    for lib, exported in _EXPORTED.items():
        declared = (*exported, *(gaps if lib == "a" else ()))
        (root / "include" / f"{lib}.h").write_text(
            "".join(f"int {n}(void);\n" for n in declared), encoding="utf-8"
        )
        src = root / f"{lib}.c"
        src.write_text(
            "".join(f"int {n}(void) {{ return 1; }}\n" for n in exported),
            encoding="utf-8",
        )
        # Named `.so` on both formats, as mingw gcc on Windows CI named them.
        _link(fmt, src, root / "lib" / f"lib{lib}.so", exported)
    return root


def _header_args(side: str, root: Path, form: str) -> list[str]:
    if form == "dir":
        return ["--header", f"{side}={root / 'include'}"]
    args: list[str] = []
    for header in sorted((root / "include").glob("*.h")):
        args += ["--header", f"{side}={header}"]
    return args


def _run(args: list[str], out: Path) -> dict:
    result = CliRunner().invoke(main, ["compare", *args, "-o", f"json={out}"])
    assert out.exists(), result.output
    return json.loads(out.read_text(encoding="utf-8"))


def _missing(report: dict, side: str) -> set[str]:
    sides = report["public_surface_reconciliation"]["sides"]
    return {entry["symbol"] for entry in sides[side]["missing_from_bundle"]}


@pytest.mark.parametrize("fmt", sorted(_TOOLCHAINS))
@pytest.mark.parametrize("form", ["dir", "files"])
@pytest.mark.parametrize("scenario", sorted(_SCENARIOS))
def test_both_drivers_reconcile_each_sides_own_headers(
    tmp_path: Path, scenario: str, form: str, fmt: str
) -> None:
    _require_toolchain(fmt)
    old_gaps, new_gaps = _SCENARIOS[scenario]
    old = _build_side(tmp_path / "old", old_gaps, fmt)
    new = _build_side(tmp_path / "new", new_gaps, fmt)
    facts = tmp_path / "old.abicheck-bundle.json"

    live = _run(
        [
            str(old / "lib"),
            str(new / "lib"),
            *_header_args("old", old, form),
            *_header_args("new", new, form),
            "--bundle-facts-out",
            str(facts),
        ],
        tmp_path / "live.json",
    )
    assert facts.exists()
    stored = _run(
        [str(facts), str(new / "lib"), *_header_args("new", new, form)],
        tmp_path / "stored.json",
    )

    for name, report in (("live/live", live), ("stored/live", stored)):
        sides = report["public_surface_reconciliation"]["sides"]
        # Every member was compared, so NEW's surface is never unresolved.
        assert report["comparison_scope"]["counts"]["available"] == 2, name
        assert sides["new"]["surface_resolvable"], (name, sides["new"])
        assert _missing(report, "new") == set(new_gaps), name
        # OLD-side behaviour is unchanged by the fix.
        assert _missing(report, "old") == set(old_gaps), name
