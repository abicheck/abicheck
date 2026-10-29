"""``param_restrict`` across evidence lanes: the declared interface decides.

A ``restrict`` qualifier on a parameter is not part of the function's type (C11
6.7.6.3p15), so it is not an ABI break. It is an API-level risk finding
(``param_restrict_added``, COMPATIBLE_WITH_RISK; ``param_restrict_changed``,
COMPATIBLE), which the catalog documents in case207. The finding is about the
*declared* interface, so only a header lane can establish it.

DWARF also records a definition's ``restrict``, but a ``restrict`` on the
definition alone leaves the declared interface unchanged, and case208 pins
that as silent. The DWARF lane therefore reports no restrict finding for any
case, and each header lane (castxml, and clang via ``ABICHECK_AST_FRONTEND``)
reports exactly the declared change. Every lane agrees with that rule.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path

import pytest

from tests._abicheck_subprocess import abicheck_command

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not sys.platform.startswith("linux"),
        reason="builds ELF shared objects with GNU ld options",
    ),
]

REPO_ROOT = Path(__file__).resolve().parents[1]

_RESTRICT_KINDS = frozenset({"param_restrict_added", "param_restrict_changed"})
_CLANG = {"ABICHECK_AST_FRONTEND": "clang"}
_NONE: frozenset[str] = frozenset()
_ADDED = frozenset({"param_restrict_added"})
_REMOVED = frozenset({"param_restrict_changed"})

#: (case id, old declaration, old definition, new declaration, new definition,
#: restrict kinds the header lanes must report). The definition is what DWARF
#: sees; the declaration is what a header lane sees.
CASES = [
    (
        "declared_added",
        "int *p",
        "int *p",
        "int *restrict p",
        "int *restrict p",
        _ADDED,
    ),
    (
        "declared_removed",
        "int *restrict p",
        "int *restrict p",
        "int *p",
        "int *p",
        _REMOVED,
    ),
    ("definition_only", "int *p", "int *p", "int *p", "int *restrict p", _NONE),
]

LANES = ["A", "B", "C", "B-clang", "C-clang"]


def _need(tool: str) -> str:
    path = shutil.which(tool)
    if path is None:
        pytest.skip(f"{tool} not available")
    return path


def _build(work: Path, case: str, side: str, decl: str, defn: str, debug: bool) -> Path:
    cc = _need("gcc")
    header = work / f"{case}_{side}.h"
    header.write_text(f"void f({decl});\n", encoding="utf-8")
    src = work / f"{case}_{side}.c"
    src.write_text(
        f'#include "{header.name}"\nvoid f({defn}) {{ (void)p; }}\n', encoding="utf-8"
    )
    lib = work / f"{case}_{side}_{'g' if debug else 'n'}.so"
    argv = [
        cc,
        *(["-g"] if debug else []),
        "-O0",
        "-shared",
        "-fPIC",
        "-Wl,-soname,libx.so.1",
        "-I",
        str(work),
        str(src),
        "-o",
        str(lib),
    ]
    proc = subprocess.run(argv, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        pytest.fail(f"compile failed: {' '.join(argv)}\n{proc.stderr}")
    return lib


def _run(
    *args: str, env_extra: Mapping[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    env = None if env_extra is None else {**os.environ, **env_extra}
    return subprocess.run(
        abicheck_command(*args),
        capture_output=True,
        text=True,
        check=False,
        cwd=REPO_ROOT,
        env=env,
    )


def _producer(snapshot: Path) -> str:
    doc = json.loads(snapshot.read_text(encoding="utf-8"))
    return doc["sections"]["debug"]["payload"]["ast_producer"]


def _lane(
    work: Path, case: str, lane: str, old: tuple[str, str], new: tuple[str, str]
) -> tuple[int, set[str]]:
    report = work / f"{case}_{lane}.json"
    base, _, frontend = lane.partition("-")
    env = _CLANG if frontend == "clang" else None

    def run(*args: str) -> subprocess.CompletedProcess[str]:
        return _run(*args, env_extra=env)

    if base == "A":
        lib_old = _build(work, case, "1", *old, True)
        lib_new = _build(work, case, "2", *new, True)
        proc = run("compare", str(lib_old), str(lib_new), "-o", f"json={report}")
    elif base == "B":
        lib_old = _build(work, case, "1", *old, False)
        lib_new = _build(work, case, "2", *new, False)
        proc = run(
            "compare",
            str(lib_old),
            str(lib_new),
            "-H",
            f"old={work / f'{case}_1.h'}",
            "-H",
            f"new={work / f'{case}_2.h'}",
            "-o",
            f"json={report}",
        )
    else:
        snaps = []
        for side, (decl, defn) in (("1", old), ("2", new)):
            lib = _build(work, case, side, decl, defn, True)
            snap = work / f"{case}_{side}.snap.json"
            dump = run(
                "dump", str(lib), "-H", str(work / f"{case}_{side}.h"), "-o", str(snap)
            )
            assert dump.returncode == 0, dump.stderr
            expected = "clang" if frontend == "clang" else "castxml"
            assert _producer(snap) == expected, (
                f"{case} lane {lane}: dump parsed headers with {_producer(snap)!r}"
            )
            snaps.append(snap)
        proc = run("compare", str(snaps[0]), str(snaps[1]), "-o", f"json={report}")
    if not report.exists():
        pytest.fail(
            f"lane {lane} wrote no report (exit {proc.returncode}):\n{proc.stderr}"
        )
    changes = json.loads(report.read_text(encoding="utf-8")).get("changes", [])
    return proc.returncode, {c.get("kind") for c in changes}


@pytest.mark.parametrize("lane", LANES)
@pytest.mark.parametrize(
    ("case", "old_decl", "old_defn", "new_decl", "new_defn", "header_kinds"),
    CASES,
    ids=[c[0] for c in CASES],
)
def test_restrict_finding_per_lane(
    tmp_path: Path,
    case: str,
    old_decl: str,
    old_defn: str,
    new_decl: str,
    new_defn: str,
    header_kinds: frozenset[str],
    lane: str,
) -> None:
    if lane.endswith("clang"):
        _need("clang")
    elif lane != "A":
        _need("castxml")
    expected = _NONE if lane == "A" else header_kinds
    code, kinds = _lane(
        tmp_path, case, lane, (old_decl, old_defn), (new_decl, new_defn)
    )
    restrict = kinds & _RESTRICT_KINDS
    assert restrict == expected, (
        f"{case} lane {lane}: restrict findings {sorted(restrict)}, "
        f"expected {sorted(expected)}"
    )
    assert code != 4, f"{case} lane {lane}: restrict read as a break (exit {code})"
