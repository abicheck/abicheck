"""ADR-090's type-evidence fact reaches every compare route.

``policy.unconfirmed_compatibility.is_unconfirmed`` reads
``DiffResult.type_evidence``, which ``attach_type_evidence`` stamps on the
pairwise result. A route that built its result without that attach would read
a header-less, debug-less pair as clean. Two guards close that class:

* **Structural.** Every production ``DiffResult(...)`` constructor is listed
  here with the reason it does not need the attach. Only ``checker.compare``
  builds a comparison result, and it attaches the fact. A new constructor
  fails this test until someone decides.
* **Behavioural.** The same symbols-only pair (an export table, no DWARF, no
  headers, one addition) goes through every public compare route. Each must
  read ``UNCONFIRMED`` with exit 1. The control is the same pair with headers,
  which must keep its observed ``COMPATIBLE`` and exit 0 on every route, so
  the label comes from the evidence and not from the route.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from abicheck.elf_metadata import ElfMetadata
from abicheck.model import AbiSnapshot, Function
from abicheck.serialization import snapshot_to_json

_PKG = Path(__file__).resolve().parent.parent / "abicheck"

#: Production ``DiffResult(...)`` constructors and why each may skip the
#: type-evidence attach.
_CONSTRUCTORS = {
    "checker.py": "the pairwise compare itself; attaches the fact",
    "report/release_document.py": (
        "bundle findings rebuilt only to fold the disposition audit; "
        "never rendered as a verdict"
    ),
    "workflows/release_support_promise.py": (
        "one support-promise inventory change per entry; its verdict comes "
        "from the declared promise, not from a type comparison"
    ),
}


def _constructors() -> set[str]:
    found: set[str] = set()
    for path in _PKG.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
            if name in {"DiffResult", "_DiffResult"}:
                found.add(path.relative_to(_PKG).as_posix())
    return found


def test_every_diff_result_constructor_is_accounted_for() -> None:
    assert _constructors() == set(_CONSTRUCTORS)


def test_the_comparing_constructor_attaches_the_fact() -> None:
    """``checker.compare`` builds its result and calls the attach in the same
    function: removing the call fails here, not in a downstream report."""
    tree = ast.parse((_PKG / "checker.py").read_text(encoding="utf-8"))
    builders = [
        fn
        for fn in ast.walk(tree)
        if isinstance(fn, ast.FunctionDef)
        and any(
            isinstance(c, ast.Call) and getattr(c.func, "id", "") == "DiffResult"
            for c in ast.walk(fn)
        )
    ]
    assert builders, "checker.py no longer builds a DiffResult"
    for fn in builders:
        calls = {
            getattr(c.func, "id", getattr(c.func, "attr", ""))
            for c in ast.walk(fn)
            if isinstance(c, ast.Call)
        }
        assert "attach_analysis_assurance" in calls, fn.name


def _snap(version: str, funcs: tuple[str, ...], *, headers: bool) -> AbiSnapshot:
    return AbiSnapshot(
        library="libx.so.1",
        version=version,
        elf=ElfMetadata(),
        from_headers=headers,
        functions=[Function(name=n, mangled=n, return_type="int") for n in funcs],
    )


def _pair(*, headers: bool) -> tuple[AbiSnapshot, AbiSnapshot]:
    return (
        _snap("1", ("f",), headers=headers),
        _snap("2", ("f", "g"), headers=headers),
    )


def _write(path: Path, snap: AbiSnapshot) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(snapshot_to_json(snap), encoding="utf-8")
    return path


def _cli(*args: str) -> tuple[int, dict]:
    from abicheck.cli import main

    res = CliRunner().invoke(main, [*args, "-o", "json=-"])
    return res.exit_code, json.loads(res.stdout)


def _typed_api(tmp: Path, old: AbiSnapshot, new: AbiSnapshot) -> tuple[int, dict]:
    from abicheck.report.diff_report import to_json
    from abicheck.workflows.compare.execute import run_compare

    result = run_compare(_write(tmp / "o.json", old), _write(tmp / "n.json", new))
    diff = result.as_tuple()[0]
    assert diff.type_evidence is not None
    doc = json.loads(to_json(diff))
    return doc["exit"]["code"], doc


def _scalar_cli(tmp: Path, old: AbiSnapshot, new: AbiSnapshot) -> tuple[int, dict]:
    return _cli(
        "compare",
        str(_write(tmp / "o.json", old)),
        str(_write(tmp / "n.json", new)),
    )


def _release_dirs(tmp: Path, old: AbiSnapshot, new: AbiSnapshot) -> tuple[int, dict]:
    _write(tmp / "old" / "libx.so.1.json", old)
    _write(tmp / "new" / "libx.so.1.json", new)
    return _cli("compare", str(tmp / "old"), str(tmp / "new"))


def _stored_packages(tmp: Path, old: AbiSnapshot, new: AbiSnapshot) -> tuple[int, dict]:
    from test_release_scope_completeness import _write_stored_package

    _write_stored_package(tmp / "old_pkg", {"libx.so.1": old})
    _write_stored_package(tmp / "new_pkg", {"libx.so.1": new})
    return _cli("compare", str(tmp / "old_pkg"), str(tmp / "new_pkg"))


_ROUTES = {
    "typed_api": _typed_api,
    "scalar_cli": _scalar_cli,
    "release_directories": _release_dirs,
    "stored_packages": _stored_packages,
}


@pytest.mark.parametrize("headers", [False, True], ids=["symbols_only", "headers"])
@pytest.mark.parametrize("route", sorted(_ROUTES))
def test_every_route_reads_the_evidence(
    tmp_path: Path, route: str, headers: bool
) -> None:
    old, new = _pair(headers=headers)
    code, doc = _ROUTES[route](tmp_path, old, new)
    if headers:
        assert (code, doc["verdict"]) == (0, "COMPATIBLE"), route
        return
    assert (code, doc["verdict"]) == (1, "UNCONFIRMED"), route
    assert "unconfirmed_compatibility" in doc["exit"]["reasons"], route
