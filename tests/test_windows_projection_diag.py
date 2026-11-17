"""TEMPORARY Windows stress diagnostic for the intermittent depth-projection finding.

Forces the failure instead of waiting for it. For each case it runs the projection
comparison many times in one process and also in fresh processes, each with an explicit
PYTHONHASHSEED. It records each side's resolved PDB path, mtime and hash, and, for the
field ``_TP_CALLBACK_ENVIRON_V3::u``, the spelling each stage gives it: the raw header
dump, the projected pass, and the binary-only dump. It also records which records of that
name appear in each snapshot, and in what order. Then it fails on purpose with the
summary. Removed once the root cause is fixed.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path

import pytest

CASES = [
    "case224_header_only_template_default_added",
    "case226_header_only_template_default_unchanged",
]
IN_PROCESS = 20
FRESH = 6
ROOT = Path(__file__).resolve().parent.parent
RECORD = "_TP_CALLBACK_ENVIRON_V3"
FIELD = "u"


def _pdb_fact(lib: Path) -> str:
    from abicheck.pdb_utils import locate_pdb

    try:
        pdb = locate_pdb(lib, allow_network=False)
    except Exception as exc:  # diagnostic: record, never crash
        return f"<lookup {type(exc).__name__}: {exc}>"
    if pdb is None:
        return "no-pdb"
    data = Path(pdb).read_bytes()
    return f"{pdb} mtime={Path(pdb).stat().st_mtime:.0f} sha={hashlib.sha1(data).hexdigest()[:12]}"


def _record_names(snap: object) -> list[str]:
    """Names of every record whose name ends with the target, in list order."""
    return [
        r.name
        for r in getattr(getattr(snap, "declarations", None), "types", []) or []
        if str(getattr(r, "name", "")).endswith(RECORD)
    ]


def _field_spelling(snap: object) -> str:
    """The target field's type spelling on every same-named record, in list order."""
    out: list[str] = []
    for r in getattr(getattr(snap, "declarations", None), "types", []) or []:
        if not str(getattr(r, "name", "")).endswith(RECORD):
            continue
        for f in getattr(r, "fields", []) or []:
            if getattr(f, "name", None) == FIELD:
                out.append(f"{r.name}->{f.type}")
    return " | ".join(out) if out else "<absent>"


@pytest.mark.integration
@pytest.mark.skipif(sys.platform != "win32", reason="Windows stress diagnostic only")
@pytest.mark.parametrize("case", CASES)
def test_stress_depth_case(case: str, tmp_path: Path, shared_cmake_build_dir) -> None:
    from abicheck.dumper import dump
    from abicheck.policy.depth_projection import project_pair_to_depth
    from tests.test_depth_projection_matches_shallow_dump import (
        Run,
        _built_case,
        _header_list,
        _unexplained,
    )

    v1, v2, h1, h2 = _built_case(case, tmp_path, shared_cmake_build_dir)
    pdbs = (_pdb_fact(v1), _pdb_fact(v2))
    in_fail: list[str] = []
    first_in: str | None = None
    stage_seen: dict[str, set[str]] = {}
    for i in range(IN_PROCESS):
        rich_old = dump(v1, headers=_header_list(h1), version="v1")
        rich_new = dump(v2, headers=_header_list(h2), version="v2")
        p_old, p_new = project_pair_to_depth(rich_old, rich_new, "binary")
        bin_old = dump(v1, headers=[], version="v1")
        bin_new = dump(v2, headers=[], version="v2")
        for stage, snap in (
            ("raw-old", rich_old),
            ("raw-new", rich_new),
            ("proj-old", p_old),
            ("proj-new", p_new),
            ("bin-old", bin_old),
            ("bin-new", bin_new),
        ):
            stage_seen.setdefault(stage, set()).add(
                f"{_field_spelling(snap)} order={_record_names(snap)}"
            )
        pr, orr = Run.of(p_old, p_new), Run.of(bin_old, bin_new)
        un = _unexplained(pr, orr)
        if un != {"only_binary_dump": [], "only_projected": []}:
            in_fail.append(str(i))
            if first_in is None:
                detail = {
                    k: pr.details.get(k) or orr.details.get(k)
                    for k in un["only_projected"] + un["only_binary_dump"]
                }
                first_in = f"iter {i}: {un} full={detail}"
    fresh_fail: list[str] = []
    fresh_rows: list[str] = []
    first_fresh: str | None = None
    node = (
        f"tests/test_depth_projection_matches_shallow_dump.py::"
        f"test_binary_projection_matches_binary_only_dump[{case}]"
    )
    for seed in range(FRESH):
        env = {**os.environ, "PYTHONHASHSEED": str(seed)}
        res = subprocess.run(
            [sys.executable, "-m", "pytest", node, "-q", "-p", "no:cacheprovider"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=900,
            env=env,
        )
        outcome = "pass" if res.returncode == 0 else "FAIL"
        fresh_rows.append(f"seed={seed}:{outcome}")
        if res.returncode != 0:
            fresh_fail.append(str(seed))
            if first_fresh is None:
                tail = res.stdout[-1500:]
                first_fresh = f"seed {seed}: {tail}"
    stages = "\n".join(
        f"  {stage}: " + " ;; ".join(sorted(spellings))
        for stage, spellings in sorted(stage_seen.items())
    )
    raise AssertionError(
        f"STRESS {case}\n"
        f"pdb-per-side={pdbs}\n"
        f"in-process failing {len(in_fail)}/{IN_PROCESS} iters={in_fail}\n"
        f"first in-process: {first_in}\n"
        f"fresh-process by hash seed: {' '.join(fresh_rows)}\n"
        f"fresh-process failing {len(fresh_fail)}/{FRESH}\n"
        f"first fresh: {first_fresh}\n"
        f"field {RECORD}::{FIELD} spelling per stage (distinct values seen):\n{stages}"
    )
