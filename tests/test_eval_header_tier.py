"""Pure-logic tests for the field-eval header tier (H4) and condafetch helpers.

The live fetch/compare runs in the network-gated lane; here the conda fetch and
the abicheck subprocess are replaced by fakes so the manifest contract, the
argv built for ``abicheck compare``, the result parsing and the CDN-repodata
fallback are pinned without network.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_EVAL_DIR = (
    Path(__file__).resolve().parent.parent / "skills-src" / "evaluation" / "field"
)
if str(_EVAL_DIR) not in sys.path:
    sys.path.insert(0, str(_EVAL_DIR))

ht = pytest.importorskip("header_tier")
cf = pytest.importorskip("condafetch")
yaml = pytest.importorskip("yaml")


@pytest.mark.parametrize(
    ("pkg", "expected_prefix"),
    [
        (
            "tbb",
            ["tbb", "tbb-devel", "tbb-dev", "tbb-headers", "tbb-include", "tbb-static"],
        ),
        ("libzlib", ["libzlib", "libzlib-devel"]),
        ("lib", ["lib", "lib-devel"]),
    ],
)
def test_devel_candidates_order_and_lib_strip(pkg, expected_prefix):
    got = cf.devel_candidates(pkg)
    assert got[: len(expected_prefix)] == expected_prefix
    assert len(got) == len(set(got))
    if pkg == "libzlib":
        assert "zlib" in got and got.index("zlib") > got.index("libzlib-static")
    if pkg == "lib":  # a bare "lib" is not stripped to an empty name
        assert "" not in got


def test_repodata_files_matches_api_shape():
    repo = {
        "packages": {
            "a-1.0-h_0.tar.bz2": {"name": "a", "version": "1.0", "build_number": 0}
        },
        "packages.conda": {
            "a-1.0-h_2.conda": {"name": "a", "version": "1.0", "build_number": 2},
            "b-2.0-h_0.conda": {"name": "b", "version": "2.0"},
        },
    }
    idx = cf.repodata_files(repo, "linux-64")
    assert sorted(idx) == ["a", "b"]
    assert {f["basename"] for f in idx["a"]} == {
        "linux-64/a-1.0-h_0.tar.bz2",
        "linux-64/a-1.0-h_2.conda",
    }
    assert idx["b"][0]["attrs"] == {"subdir": "linux-64", "build_number": 0}


def test_list_files_falls_back_to_repodata_when_api_blocked(monkeypatch, tmp_path):
    monkeypatch.setattr(cf, "CACHE", str(tmp_path))

    def blocked(url, dest):
        raise OSError("proxy 403")

    monkeypatch.setattr(cf, "_retrieve", blocked)
    monkeypatch.setattr(cf, "_repodata_index", lambda subdir: {"x": [{"version": "1"}]})
    assert cf.list_files("x") == [{"version": "1"}]
    assert cf.list_files("missing") == []


def _fake_fs(tmp_path: Path, layout: dict[str, list[str]]):
    """Packages → header files under <pkg>/include; returns a fake cf."""
    for pkg, files in layout.items():
        for rel in files:
            p = tmp_path / pkg / "include" / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("", encoding="utf-8")

    def resolve(pkg, ver, candidates=None):
        for cand in candidates or cf.devel_candidates(pkg):
            if (tmp_path / cand / "include").is_dir():
                return cand, str(tmp_path / cand / "include")
        raise LookupError(f"none for {pkg}")

    return resolve


def test_resolve_side_umbrella_includes_and_deps(monkeypatch, tmp_path):
    monkeypatch.setattr(
        cf,
        "resolve_headers_pkg",
        _fake_fs(tmp_path, {"tbb-devel": ["oneapi/tbb.h"], "absl": ["absl/x.h"]}),
    )
    entry = {
        "lib": "t",
        "conda_pkg": "tbb",
        "old": "1",
        "new": "2",
        "headers": {
            "umbrella": ["oneapi/tbb.h"],
            "include": ["oneapi"],
            "deps": [{"pkg": "absl", "old": "a", "new": "b"}],
        },
    }
    res = ht.resolve_side(entry, "new")
    inc = str(tmp_path / "tbb-devel" / "include")
    assert res["package"] == "tbb-devel"
    assert res["headers"] == [os.path.join(inc, "oneapi", "tbb.h")]
    assert res["includes"] == [
        inc,
        os.path.join(inc, "oneapi"),
        str(tmp_path / "absl" / "include"),
    ]


def test_resolve_side_dep_include_is_relative_to_package_root(monkeypatch, tmp_path):
    # glib's glibconfig.h lives in the `glib` package's lib/glib-2.0/include,
    # which has no include/ tree at all.
    monkeypatch.setattr(
        cf,
        "resolve_headers_pkg",
        _fake_fs(tmp_path, {"libglib": ["glib-2.0/gio/gio.h"]}),
    )
    seen = []

    def fetch(pkg, ver):
        seen.append((pkg, ver))
        return str(tmp_path / f"root-{pkg}-{ver}")

    monkeypatch.setattr(cf, "fetch_extract", fetch)
    entry = {
        "lib": "glib",
        "conda_pkg": "libglib",
        "old": "1",
        "new": "2",
        "headers": {
            "umbrella": ["glib-2.0/gio/gio.h"],
            "deps": [
                {"pkg": "glib", "old": "1", "new": "2", "include": ["lib/x", "lib/y"]}
            ],
        },
    }
    res = ht.resolve_side(entry, "old")
    root = str(tmp_path / "root-glib-1")
    assert res["includes"][1:] == [
        os.path.join(root, "lib", "x"),
        os.path.join(root, "lib", "y"),
    ]
    assert seen == [("glib", "1")]


def test_repodata_files_indexes_removed_archives_below_live_builds():
    repo = {
        "packages.conda": {
            "p-1.0-h_1.conda": {"name": "p", "version": "1.0", "build_number": 1},
        },
        "removed": [
            "p-1.0-hx_3.conda",
            "p-2.0-h612f066_0.conda",
            "q-x-1.tar.bz2",
            "junk",
        ],
    }
    idx = cf.repodata_files(repo, "linux-64")
    by_base = {f["basename"]: f for f in idx["p"]}
    assert by_base["linux-64/p-2.0-h612f066_0.conda"]["version"] == "2.0"
    assert by_base["linux-64/p-1.0-hx_3.conda"]["attrs"]["build_number"] == 3
    assert idx["q"][0]["version"] == "x"
    assert "junk" not in idx
    cf_files = idx["p"]
    old = cf.list_files
    try:
        cf.list_files = lambda pkg, subdir="linux-64": cf_files
        # live build 1 wins over removed build 3 of the same version
        assert cf.pick("p", "1.0")["basename"] == "linux-64/p-1.0-h_1.conda"
        assert cf.pick("p", "2.0")["basename"] == "linux-64/p-2.0-h612f066_0.conda"
    finally:
        cf.list_files = old


@pytest.mark.parametrize(
    "files, chosen",
    [
        # live .tar.bz2 beats a removed .conda of the same version
        (
            [("p-1-h_1.tar.bz2", 1, False), ("p-1-h_3.conda", 3, True)],
            "p-1-h_1.tar.bz2",
        ),
        # among live builds .conda wins at equal build number
        ([("p-1-h_1.tar.bz2", 1, False), ("p-1-h_1.conda", 1, False)], "p-1-h_1.conda"),
        # among live builds .conda wins even over a higher-build .tar.bz2
        ([("p-1-h_2.tar.bz2", 2, False), ("p-1-h_1.conda", 1, False)], "p-1-h_1.conda"),
        # only removed builds: still fetchable, highest wins
        ([("p-1-h_1.conda", 1, True), ("p-1-h_2.conda", 2, True)], "p-1-h_2.conda"),
    ],
)
def test_pick_prefers_live_over_removed_regardless_of_format(
    monkeypatch, files, chosen
):
    rows = [
        {"basename": b, "version": "1", "attrs": {"build_number": n, "removed": r}}
        for b, n, r in files
    ]
    monkeypatch.setattr(cf, "list_files", lambda pkg, subdir="linux-64": list(rows))
    assert cf.pick("p", "1")["basename"] == chosen


def test_resolve_side_missing_umbrella_raises(monkeypatch, tmp_path):
    monkeypatch.setattr(cf, "resolve_headers_pkg", _fake_fs(tmp_path, {"z": ["z.h"]}))
    entry = {
        "lib": "z",
        "conda_pkg": "z",
        "old": "1",
        "new": "2",
        "headers": {"umbrella": ["nope.h"]},
    }
    with pytest.raises(FileNotFoundError, match="nope.h"):
        ht.resolve_side(entry, "old")


def test_compare_cmd_is_per_side():
    old = {"headers": ["/o/a.h"], "includes": ["/o"]}
    new = {"headers": ["/n/a.h"], "includes": ["/n", "/dep"]}
    cmd = ht.compare_cmd("o.so", "n.so", old, new, ["W=8"])
    assert cmd[:4] == ["abicheck", "compare", "o.so", "n.so"]
    pairs = list(zip(cmd[4:-2:2], cmd[5:-2:2], strict=True))
    assert pairs == [
        ("--header", "old=/o/a.h"),
        ("-I", "old=/o"),
        ("--header", "new=/n/a.h"),
        ("-I", "new=/n"),
        ("-I", "new=/dep"),
        ("-D", "W=8"),
    ]
    assert cmd[-2:] == ["-o", "json=-"]


@pytest.mark.parametrize(
    ("rc", "stdout", "want"),
    [
        (
            4,
            json.dumps(
                {
                    "verdict": "BREAKING",
                    "evidence_tier": "header_aware",
                    "analysis_assurance": {
                        "status": "partial",
                        "effective_depth": "headers",
                    },
                    "summary": {"breaking": 3},
                }
            ),
            {
                "exit": 4,
                "verdict": "BREAKING",
                "assurance": "partial",
                "effective_depth": "headers",
                "breaking": 3,
            },
        ),
        (
            0,
            json.dumps(
                {"verdict": "NO_CHANGE", "analysis_assurance": {"status": "complete"}}
            ),
            {
                "exit": 0,
                "verdict": "NO_CHANGE",
                "assurance": "complete",
                "effective_depth": None,
            },
        ),
        (1, "", {"exit": 1}),
        (1, "not json", {"exit": 1}),
    ],
)
def test_parse_compare(rc, stdout, want):
    rec = ht.parse_compare(rc, stdout, "boom")
    for k, v in want.items():
        assert rec[k] == v
    assert ("error" in rec) == ("verdict" not in want)


def test_scan_header_one_errors_become_rows(monkeypatch):
    def pick(*a):
        raise SystemExit("no files for x 1")

    entry = {
        "lib": "x",
        "conda_pkg": "x",
        "old": "1",
        "new": "2",
        "headers": {"umbrella": ["x.h"]},
    }
    rec = ht.scan_header_one(entry, pick, None)
    assert rec["tier"] == "header" and "no files" in rec["error"]


def test_scan_header_one_happy_path(monkeypatch):
    monkeypatch.setattr(
        ht,
        "resolve_side",
        lambda e, w: {
            "package": "p",
            "include_root": "/i",
            "headers": [f"/{w}.h"],
            "includes": ["/i"],
        },
    )
    seen = {}

    def run(cmd):
        seen["cmd"] = cmd
        return 0.5, SimpleNamespace(
            returncode=0, stdout='{"verdict": "COMPATIBLE"}', stderr=""
        )

    entry = {
        "lib": "x",
        "conda_pkg": "x",
        "old": "1",
        "new": "2",
        "headers": {"umbrella": ["x.h"], "expect": "BREAKING", "defines": ["A"]},
    }
    rec = ht.scan_header_one(entry, lambda *a: f"{a[1]}.so", run)
    assert rec["verdict"] == "COMPATIBLE" and rec["verdict_matches_expected"] is False
    assert "-D" in seen["cmd"] and "1.so" in seen["cmd"]
    section = "\n".join(
        ht.render_header_section(
            [rec, {"lib": "y", "old": "1", "new": "2", "error": "E"}]
        )
    )
    assert (
        "| x | 1→2 | `p`" in section
        and "✗(BREAKING)" in section
        and "| y | 1→2 |" in section
    )


def test_merge_into_keeps_other_tiers():
    prior = {"results": [1], "source_results": [2], "generated_utc": "t0"}
    merged = ht.merge_into(
        prior,
        {
            "header_results": [{"lib": "a"}],
            "generated_utc": "t1",
            "abicheck_version": "v",
        },
    )
    assert merged["results"] == [1] and merged["source_results"] == [2]
    assert (
        merged["header_results"] == [{"lib": "a"}]
        and merged["header_run"]["generated_utc"] == "t1"
    )
    assert "header_results" not in prior


@pytest.mark.parametrize(
    "prior_libs, run_libs",
    [
        (["a", "b", "c"], ["b"]),
        (["a", "b", "c"], ["d"]),
        (["a", "b"], ["b", "e"]),
        ([], ["a"]),
        (["a", "b"], []),
    ],
)
def test_merge_into_subset_run_replaces_only_its_own_rows(prior_libs, run_libs):
    """An ``--only`` header run keeps every other library's row (it used to
    replace the whole header table with just the subset)."""
    prior = {"header_results": [{"lib": x, "v": "old"} for x in prior_libs]}
    run = {"header_results": [{"lib": x, "v": "new"} for x in run_libs]}
    rows = ht.merge_into(prior, run)["header_results"]
    libs = [r["lib"] for r in rows]
    assert libs == prior_libs + [x for x in run_libs if x not in prior_libs]
    for r in rows:
        assert r["v"] == ("new" if r["lib"] in run_libs else "old")


def test_manifest_header_blocks_are_well_formed():
    manifest = yaml.safe_load((_EVAL_DIR / "manifest.yaml").read_text(encoding="utf-8"))
    entries = ht.header_entries(manifest, None)
    assert {"onetbb", "onednn", "onedal"} <= {e["lib"] for e in entries}
    allowed = {"umbrella", "packages", "defines", "include", "deps", "expect"}
    for e in entries:
        hb = e["headers"]
        assert set(hb) <= allowed, e["lib"]
        assert hb["umbrella"] and all(isinstance(u, str) for u in hb["umbrella"])
        for dep in hb.get("deps", []):
            assert {"pkg", "old", "new"} <= set(dep)
    assert [e["lib"] for e in ht.header_entries(manifest, {"onednn"})] == ["onednn"]
