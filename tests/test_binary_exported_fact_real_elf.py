"""``binary_exported_fact`` tiers on a real gcc-built ELF (integration).

The property tests in ``test_binary_exported_fact_export_join.py`` drive the
primitive and producers over generated tables; this module goes through the
real path -- gcc links a PIE whose ``static_only_fn`` is a GLOBAL ``.symtab``
symbol absent from ``.dynsym`` (no ``--export-dynamic``), while ``dyn_fn`` is
exported with ``--export-dynamic-symbol`` -- then ``abicheck dump -H`` (castxml)
and ``abicheck compare`` through the CLI.

* False positive removed: the ``.symtab``-only symbol is no longer recorded
  as a confirmed (``PRESENT``) export; it is ``PARTIAL`` ``static_only``, the
  export join leaves it unmatched, and the two agree.
* Real break preserved: dropping ``dyn_fn`` from v2 is still reported as a
  breaking removal by ``compare``.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not sys.platform.startswith("linux"), reason="ELF .symtab/.dynsym fixture"
    ),
]

_REPO_ROOT = Path(__file__).resolve().parents[1]

HEADER = "int dyn_fn(int);\nint static_only_fn(int);\n"
SOURCE = {
    "v1": "int dyn_fn(int x){return x;}\n"
    "int static_only_fn(int x){return x+1;}\n"
    "int main(void){return dyn_fn(0)+static_only_fn(0);}\n",
    "v2": "int static_only_fn(int x){return x+1;}\n"
    "int main(void){return static_only_fn(0);}\n",
}


def _abicheck(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "abicheck", *args],
        cwd=cwd,
        # A private cache: a whole-snapshot cache hit from another build of
        # this code would hand back facts this run's producer never computed.
        env={
            **os.environ,
            "ABICHECK_AST_FRONTEND": "castxml",
            "ABICHECK_CACHE_DIR": str(cwd / ".abicheck-cache"),
            # The checkout under test, not whichever abicheck is installed.
            "PYTHONPATH": os.pathsep.join(
                p for p in (str(_REPO_ROOT), os.environ.get("PYTHONPATH", "")) if p
            ),
        },
        capture_output=True,
        text=True,
    )


@pytest.fixture(scope="module")
def dumps(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    for tool in ("gcc", "castxml", "readelf"):
        if shutil.which(tool) is None:
            pytest.skip(f"{tool} not available")
    root = tmp_path_factory.mktemp("symtab_only")
    out: dict[str, Path] = {}
    for version, src in SOURCE.items():
        d = root / version
        d.mkdir()
        (d / "api.h").write_text(HEADER)
        (d / "app.c").write_text('#include "api.h"\n' + src)
        link = ["-Wl,--export-dynamic-symbol=dyn_fn"] if version == "v1" else []
        r = subprocess.run(
            ["gcc", "-g", "-fPIE", "-pie", "app.c", *link, "-o", "app"],
            cwd=d,
            capture_output=True,
            text=True,
        )
        if r.returncode != 0 and "export-dynamic-symbol" in r.stderr:
            pytest.skip("linker lacks --export-dynamic-symbol")
        assert r.returncode == 0, r.stderr
        res = _abicheck(["dump", "app", "-H", "api.h", "-o", "app.json"], d)
        assert res.returncode == 0, res.stderr
        out[version] = d
    # The fixture really has the shape the test claims.
    syms = subprocess.run(
        ["readelf", "-W", "--dyn-syms", str(out["v1"] / "app")],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert "dyn_fn" in syms and "static_only_fn" not in syms
    return out


def test_symtab_only_symbol_is_not_a_confirmed_export(dumps):
    from abicheck.compare.export_join import join_exports
    from abicheck.model.export_index import ExportMatch
    from abicheck.model.fact import FactStatus
    from abicheck.model.graph_join import JoinState
    from abicheck.model.surface_facts import binary_export_match, is_binary_exported
    from abicheck.serialization import load_snapshot

    snap = load_snapshot(dumps["v1"] / "app.json")
    j = join_exports(snap)
    by_name = {
        fn.name: (fn, ident)
        for fn, ident in zip(snap.functions, j.identities.functions)
    }
    fn, ident = by_name["static_only_fn"]
    assert binary_export_match(fn) is ExportMatch.STATIC_ONLY
    assert fn.binary_exported_fact.status is FactStatus.PARTIAL
    assert is_binary_exported(fn)  # truthiness preserved, so findings do not move
    assert j.declaration(ident.node_id).state is JoinState.UNMATCHED

    fn, ident = by_name["dyn_fn"]
    assert binary_export_match(fn) is ExportMatch.DYNAMIC
    assert fn.binary_exported_fact.status is FactStatus.PRESENT
    assert j.declaration(ident.node_id).state is JoinState.MATCHED


def test_real_exported_symbol_removal_is_still_reported(dumps, tmp_path):
    out = tmp_path / "cmp.json"
    res = _abicheck(
        [
            "compare",
            str(dumps["v1"] / "app.json"),
            str(dumps["v2"] / "app.json"),
            "-o",
            f"json={out}",
        ],
        tmp_path,
    )
    assert res.returncode == 4, res.stderr
    report = json.loads(out.read_text())
    assert report["verdict"] == "BREAKING"
    assert any(c.get("symbol") == "dyn_fn" for c in report["changes"])
