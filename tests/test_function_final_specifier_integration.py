"""``final`` on a virtual member function, read by every header backend.

Built with g++ and dumped through castxml, the direct-clang backend and the
native gcc plugin: a virtual gaining ``final`` is ``func_became_final``
(``API_BREAK``: an overrider stops compiling, nothing binary changes), losing
it ``func_lost_final``. A stored copy of each snapshot answers the same.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from abicheck.checker import ChangeKind, Verdict, compare
from abicheck.dumper import dump
from abicheck.serialization import load_snapshot
from abicheck.storage.snapshot_codec import save_snapshot

pytestmark = pytest.mark.integration

_BACKENDS = ("castxml", "clang", "gcc")
_PLAIN = "struct V { virtual ~V(); virtual int f(); };\nV* v_make();\n"
_FINAL = "struct V { virtual ~V(); virtual int f() final; };\nV* v_make();\n"
_SRC = '#include "api.h"\nV::~V() {}\nint V::f() { return 1; }\nV* v_make() { return new V(); }\n'


def _build(side: Path, header: str) -> Path:
    side.mkdir(parents=True)
    (side / "api.h").write_text(header, encoding="utf-8")
    (side / "api.cpp").write_text(_SRC, encoding="utf-8")
    lib = side / "libv.so"
    subprocess.run(
        [
            "g++",
            "-shared",
            "-fPIC",
            "-g",
            "-std=c++17",
            f"-I{side}",
            str(side / "api.cpp"),
            "-o",
            str(lib),
        ],
        check=True,
        capture_output=True,
    )
    return lib


def _snap(side: Path, backend: str):  # type: ignore[no-untyped-def]
    return dump(
        side / "libv.so",
        [side / "api.h"],
        version=side.name,
        compiler="c++",
        lang="c++",
        header_backend=backend,
        public_headers=[side / "api.h"],
    )


@pytest.mark.skipif(shutil.which("g++") is None, reason="g++ not installed")
@pytest.mark.parametrize("backend", _BACKENDS)
@pytest.mark.parametrize(
    "old_h,new_h,kind,verdict",
    [
        (_PLAIN, _FINAL, ChangeKind.FUNC_BECAME_FINAL, Verdict.API_BREAK),
        (_FINAL, _PLAIN, ChangeKind.FUNC_LOST_FINAL, Verdict.COMPATIBLE_WITH_RISK),
    ],
    ids=["became_final", "lost_final"],
)
def test_every_backend_reads_the_final_specifier(
    tmp_path: Path,
    backend: str,
    old_h: str,
    new_h: str,
    kind: ChangeKind,
    verdict: Verdict,
) -> None:
    from abiforge.checkers.abicheck import header_backend_problem

    problem = header_backend_problem(backend, "g++", "c++")
    if problem:
        pytest.skip(problem)
    _build(tmp_path / "old", old_h)
    _build(tmp_path / "new", new_h)
    old, new = _snap(tmp_path / "old", backend), _snap(tmp_path / "new", backend)
    r = compare(old, new)
    finals = [
        c
        for c in r.changes
        if c.kind in (ChangeKind.FUNC_BECAME_FINAL, ChangeKind.FUNC_LOST_FINAL)
    ]
    assert [(c.kind, c.symbol) for c in finals] == [(kind, "_ZN1V1fEv")]
    assert r.verdict == verdict
    # A stored copy carries the specifier (the extension fact).
    save_snapshot(old, tmp_path / "old.json")
    save_snapshot(new, tmp_path / "new.json")
    stored = compare(
        load_snapshot(tmp_path / "old.json"), load_snapshot(tmp_path / "new.json")
    )
    assert kind in {c.kind for c in stored.changes}
    assert kind in (ChangeKind.FUNC_BECAME_FINAL, ChangeKind.FUNC_LOST_FINAL)
