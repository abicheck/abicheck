"""TEMPORARY Windows diagnostic for the intermittent depth-projection finding.

Two parts, per case:

1. Stress: the projection comparison run many times in one process, and in fresh
   processes each under an explicit PYTHONHASHSEED. For the field
   ``_TP_CALLBACK_ENVIRON_V3::u`` it records the spelling each stage gives it (raw header
   dump, projected, binary-only), and the order of the same-named records.
2. Rebuild: the case is compiled afresh N times, each into its own build directory. For
   each side it counts the two spellings in the raw PDB bytes and records the projected
   spelling, so the projected spelling can be checked against what the PDB contains.

It fails on purpose with the summary. Removed once the question is settled.
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
REBUILDS = 8
ROOT = Path(__file__).resolve().parent.parent
RECORD = "_TP_CALLBACK_ENVIRON_V3"
FIELD = "u"
SPELLINGS = (b"<unnamed-tag>", b"<unnamed-type-u>")


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


def _pdb_data(lib: Path) -> bytes:
    """The raw bytes of the side's PDB, or b'' when there is none."""
    from abicheck.pdb_utils import locate_pdb

    pdb = locate_pdb(lib, allow_network=False)
    return Path(pdb).read_bytes() if pdb is not None else b""


def _byte_counts(lib: Path) -> str:
    """Occurrences of each spelling in the side's raw PDB bytes."""
    data = _pdb_data(lib)
    if not data:
        return "no-pdb"
    return " ".join(f"{s.decode()}={data.count(s)}" for s in SPELLINGS)


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
    # Rebuild: fresh compile per run, raw PDB bytes against the projected spelling.
    rebuild_rows: list[str] = []
    mismatches: list[str] = []
    for n in range(REBUILDS):
        rb = tmp_path / f"rebuild_{n}"
        rb.mkdir()
        rv1, rv2, _rh1, _rh2 = _built_case(case, rb, None)
        bytes_old, bytes_new = _byte_counts(rv1), _byte_counts(rv2)
        proj_old, proj_new = project_pair_to_depth(
            dump(rv1, headers=_header_list(h1), version="v1"),
            dump(rv2, headers=_header_list(h2), version="v2"),
            "binary",
        )
        spell_old, spell_new = _field_spelling(proj_old), _field_spelling(proj_new)
        rebuild_rows.append(
            f"rebuild {n}: bytes-old[{bytes_old}] bytes-new[{bytes_new}] "
            f"projected-old={spell_old} projected-new={spell_new}"
        )
        # The projected spelling must be one the side's PDB actually contains.
        for side, lib, spelled in (("old", rv1, spell_old), ("new", rv2, spell_new)):
            data = _pdb_data(lib)
            for token in SPELLINGS:
                if token.decode() in spelled and token not in data:
                    mismatches.append(
                        f"rebuild {n} {side}: projected {token.decode()} not in PDB bytes"
                    )
    raise AssertionError(
        f"STRESS {case}\n"
        f"pdb-per-side={pdbs}\n"
        f"in-process failing {len(in_fail)}/{IN_PROCESS} iters={in_fail}\n"
        f"first in-process: {first_in}\n"
        f"fresh-process by hash seed: {' '.join(fresh_rows)}\n"
        f"fresh-process failing {len(fresh_fail)}/{FRESH}\n"
        f"first fresh: {first_fresh}\n"
        f"field {RECORD}::{FIELD} spelling per stage (distinct values seen):\n{stages}\n"
        f"rebuilds ({REBUILDS}, fresh compile each):\n  "
        + "\n  ".join(rebuild_rows)
        + f"\nprojected-vs-bytes mismatches: {mismatches or 'none'}"
    )
