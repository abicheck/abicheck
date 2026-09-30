"""``compare`` and ``dump`` select the same source TUs for the same evidence.

Bug class: ``compare``'s inline source-tree dump routed a *pack-shaped*
``--build-info`` out-of-band instead of handing it to the dump, so its L4
replay discovered the tree's own compile DB (every TU in the workspace) while
``dump --sources S --build-info PACK`` replayed only the pack's compile units
(the integration lab saw 19 vs 1, two of the extra TUs failing, which then
failed a fail-closed coverage contract).

General invariant: for every combination of a side's ``--sources`` and
``--build-info`` shapes (absent / raw / pack), the inline dump receives exactly
the inputs a standalone ``dump`` would, and every supplied input is consumed
exactly once (by the dump or the out-of-band pack path, never both, never
neither). The end-to-end half is differential: the TU counts ``dump``
reports are the oracle for ``compare`` over several input shapes.
"""

from __future__ import annotations

import itertools
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from abicheck.frontends.cli.inline_evidence_routing import inline_dump_evidence_routing

_SHAPES = ("absent", "raw", "pack")


def _route(src_shape: str, bi_shape: str):
    sources = None if src_shape == "absent" else Path(f"src-{src_shape}")
    build_info = None if bi_shape == "absent" else Path(f"bi-{bi_shape}")
    return (
        sources,
        build_info,
        inline_dump_evidence_routing(
            sources, build_info, src_shape == "raw", bi_shape == "raw"
        ),
    )


@pytest.mark.parametrize(
    ("src_shape", "bi_shape"), list(itertools.product(_SHAPES, _SHAPES))
)
def test_routing_matches_standalone_dump_and_conserves_inputs(src_shape, bi_shape):
    sources, build_info, (d_src, d_bi, k_src, k_bi) = _route(src_shape, bi_shape)
    dump_runs = d_src is not None or d_bi is not None
    # A dump runs iff something raw needs collecting.
    assert dump_runs == ("raw" in (src_shape, bi_shape))
    if src_shape == "raw":
        # Same inputs as `abicheck dump --sources S --build-info B`.
        assert (d_src, d_bi) == (sources, build_info)
    # Conservation: each supplied input goes exactly one way.
    for given, dumped, kept in ((sources, d_src, k_src), (build_info, d_bi, k_bi)):
        if given is None:
            assert dumped is None and kept is None
        else:
            assert (dumped is None) != (kept is None)
            assert (dumped or kept) == given
    # A pack never reaches the dump on its own (it is not raw evidence).
    if src_shape != "raw" and bi_shape == "pack":
        assert d_bi is None and k_bi == build_info


def _tools_available() -> bool:
    return all(shutil.which(t) for t in ("g++", "clang", "git"))


_TU_RE = re.compile(r"scope=([\w-]+), (\d+)/(\d+) TUs parsed")


def _dump_tu_counts(snapshot: Path) -> tuple[str, int, int]:
    from abicheck.serialization import load_snapshot

    pack = load_snapshot(snapshot).build_source
    assert pack is not None and pack.source_abi is not None
    cov = pack.source_abi.coverage
    return (
        cov["replay_scope"],
        int(cov["compile_units_parsed"]),
        int(cov["compile_units_selected"]),
    )


def _compare_tu_counts(report: Path) -> tuple[str, int, int]:
    doc = json.loads(report.read_text())
    for row in doc.get("layer_coverage", []):
        m = _TU_RE.search(str(row.get("detail", "")))
        if m:
            return m.group(1), int(m.group(2)), int(m.group(3))
    raise AssertionError("compare report carries no L4 TU row")


@pytest.fixture
def lab_tree(tmp_path: Path) -> tuple[Path, Path, Path]:
    """A 3-TU source tree whose compile DB is wider than a 1-TU target pack."""
    from abicheck.buildsource import pack_io
    from abicheck.buildsource.build_evidence import BuildEvidence, CompileUnit
    from abicheck.buildsource.pack import BuildSourcePack

    root = tmp_path / "ws"
    (root / "include").mkdir(parents=True)
    (root / "src").mkdir()
    (root / "include" / "m.h").write_text("int add(int a, int b);\n")
    (root / "src" / "math.cc").write_text(
        '#include "m.h"\nint add(int a, int b) { return a + b; }\n'
    )
    for n in ("other1", "other2"):
        (root / "src" / f"{n}.cc").write_text(f"int {n}() {{ return 1; }}\n")
    (root / "compile_commands.json").write_text(
        json.dumps(
            [
                {
                    "directory": str(root),
                    "file": f"src/{n}.cc",
                    "arguments": ["g++", "-Iinclude", "-c", f"src/{n}.cc"],
                }
                for n in ("math", "other1", "other2")
            ]
        )
    )
    lib = root / "libmath.so"
    subprocess.run(
        [
            "g++",
            "-shared",
            "-fPIC",
            f"-I{root / 'include'}",
            str(root / "src" / "math.cc"),
            "-o",
            str(lib),
        ],
        check=True,
    )
    pack = tmp_path / "pack"
    ev = BuildEvidence(
        compile_units=[
            CompileUnit(
                id="cu://src/math.cc",
                source="src/math.cc",
                directory=str(root),
                target_id="//:math",
                argv=["g++", "-Iinclude", "-c", "src/math.cc"],
                language="CXX",
                include_paths=[str(root / "include")],
            )
        ]
    )
    pack_io.write(BuildSourcePack(root=pack, build_evidence=ev))
    git = [
        "git",
        "-C",
        str(root),
        "-c",
        "user.email=t@t",
        "-c",
        "user.name=t",
        "-c",
        "commit.gpgsign=false",
    ]
    subprocess.run([*git, "init", "-q"], check=True)
    subprocess.run([*git, "add", "-A"], check=True)
    subprocess.run([*git, "commit", "-qm", "base"], check=True)
    return root, lib, pack


@pytest.mark.integration
@pytest.mark.skipif(not _tools_available(), reason="needs g++, clang and git")
@pytest.mark.parametrize("with_pack", [True, False], ids=["pack", "tree-db"])
@pytest.mark.parametrize("with_since", [False, True], ids=["full", "since"])
def test_compare_selects_the_same_tus_as_dump(
    lab_tree, tmp_path, monkeypatch, with_pack, with_since
):
    from click.testing import CliRunner

    from abicheck.cli import main

    root, lib, pack = lab_tree
    monkeypatch.setenv("ABICHECK_AST_FRONTEND", "clang")
    monkeypatch.chdir(root)
    runner = CliRunner()

    snap = tmp_path / "dump.json"
    dump_args = [
        "dump",
        str(lib),
        "-H",
        "include",
        "--sources",
        ".",
        "--depth",
        "source",
        "-o",
        str(snap),
    ]
    if with_pack:
        dump_args += ["--build-info", str(pack)]
    res = runner.invoke(main, dump_args)
    assert res.exit_code == 0, res.output
    dump_scope, dump_parsed, dump_selected = _dump_tu_counts(snap)
    # Vacuity guard: the pack and the tree DB really select different sets.
    assert dump_selected == (1 if with_pack else 3)

    report = tmp_path / "cmp.json"
    cmp_args = [
        "compare",
        str(snap),
        str(lib),
        "--header",
        "new=include",
        "--sources",
        "new=.",
        "--depth",
        "source",
        "-o",
        f"json={report}",
    ]
    if with_pack:
        cmp_args += ["--build-info", f"new={pack}"]
    if with_since:
        head = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        cmp_args += ["--since", head]
    res = runner.invoke(main, cmp_args)
    assert res.exit_code in (0, 1, 2, 4), res.output
    _scope, cmp_parsed, cmp_selected = _compare_tu_counts(report)
    assert (cmp_parsed, cmp_selected) == (dump_parsed, dump_selected)
    if not with_since:
        assert _scope == dump_scope


def test_failed_tus_are_named_in_the_l4_detail():
    from abicheck.buildsource.inline import _l4_coverage_detail
    from abicheck.buildsource.source_abi import SourceAbiSurface

    surface = SourceAbiSurface()
    surface.coverage.update(
        compile_units_selected=19,
        compile_units_parsed=17,
        extractor_failures=2,
        failed_compile_units=["src/a.cc", "src/b.cc"],
    )
    detail = _l4_coverage_detail(surface)
    assert "17/19 TUs parsed" in detail
    assert "2 extractor failures (src/a.cc, src/b.cc)" in detail
    # More failures than names: the remainder is counted, never hidden.
    surface.coverage["extractor_failures"] = 5
    assert "5 extractor failures (src/a.cc, src/b.cc, +3 more)" in _l4_coverage_detail(
        surface
    )


def test_failed_unit_names_follow_the_extractor_diagnostic_contract():
    from types import SimpleNamespace

    from abicheck.buildsource.inline import FAILED_TU_NAME_LIMIT, failed_unit_names

    units = [SimpleNamespace(source=f"src/f{i}.cc", id=f"cu://{i}") for i in range(30)]
    units.append(SimpleNamespace(source="", id="cu://nosource"))
    # "src/f1.cc" must not match a diagnostic for "src/f1.cc.in" or "src/f10.cc".
    diags = ["src/f10.cc: boom", "src/f1.cc.in: other", "cu://nosource: x"]
    assert failed_unit_names(units, diags) == ["src/f10.cc", "cu://nosource"]
    everything = [f"{u.source or u.id}: e" for u in units]
    named = failed_unit_names(units, everything)
    assert len(named) == FAILED_TU_NAME_LIMIT
    assert named == [u.source for u in units[:FAILED_TU_NAME_LIMIT]]
    assert failed_unit_names(units, []) == []
