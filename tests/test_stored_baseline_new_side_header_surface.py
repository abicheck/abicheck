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
``integration``: it needs gcc and castxml, because the defect lived in the
CLI-to-extraction wiring a hand-built snapshot cannot reach.
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

pytestmark = [
    pytest.mark.integration,
    # The fixture libraries are built as ELF ``.so`` files.
    pytest.mark.skipif(
        not sys.platform.startswith("linux"), reason="needs an ELF-emitting gcc"
    ),
]

#: Exported by liba / libb respectively, on both sides.
_EXPORTED = {"a": ("a_one",), "b": ("b_one",)}

#: (old-only gaps, new-only gaps): declared in liba's header, exported nowhere.
_SCENARIOS = {
    "new_only": ((), ("gap_new",)),
    "old_only": (("gap_old",), ()),
    "both_persistent_and_new": (("gap_kept",), ("gap_kept", "gap_new")),
    "none": ((), ()),
}


def _require_toolchain() -> None:
    for tool in ("gcc", "castxml"):
        if shutil.which(tool) is None:
            pytest.skip(f"{tool} is required for this fixture")


def _build_side(root: Path, gaps: tuple[str, ...]) -> Path:
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
        subprocess.run(
            [
                "gcc",
                "-shared",
                "-fPIC",
                "-o",
                str(root / "lib" / f"lib{lib}.so"),
                str(src),
            ],
            check=True,
            capture_output=True,
        )
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


@pytest.mark.parametrize("form", ["dir", "files"])
@pytest.mark.parametrize("scenario", sorted(_SCENARIOS))
def test_both_drivers_reconcile_each_sides_own_headers(
    tmp_path: Path, scenario: str, form: str
) -> None:
    _require_toolchain()
    old_gaps, new_gaps = _SCENARIOS[scenario]
    old = _build_side(tmp_path / "old", old_gaps)
    new = _build_side(tmp_path / "new", new_gaps)
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
        assert sides["new"].get("resolvable", True), (name, sides["new"])
        assert _missing(report, "new") == set(new_gaps), name
        # OLD-side behaviour is unchanged by the fix.
        assert _missing(report, "old") == set(old_gaps), name
