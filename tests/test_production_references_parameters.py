# SPDX-License-Identifier: Apache-2.0
"""``production_references.dead_parameters``: defaulted parameters no
production call passes (``usecase_paths.py dead``'s parameter pass).

Bug class: a parameter left behind when its last caller is deleted -- the
function stays live, so a function-level dead-code pass never sees it. That
is how ``scan``'s deletion left eight unused keywords on
``_resolve_side_snapshot_impl`` (``docs/contribute/plans/
dead-code-and-single-owner.md``). The invariant is stated over generated
packages: a parameter is reported exactly when no counted production call
passes it, a function with any non-call production use is not judged, and a
reported parameter is never one some production call really passes. The
oracle is the generator's own record of what each call it wrote binds,
never ``passed_parameters``.
"""

from __future__ import annotations

import random
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import production_references as pr  # noqa: E402

FUNC_DEFAULTED = ("b", "c", "d", "e")  # def fn(a, b=0, c=0, *, d=0, e=0)
METHOD_DEFAULTED = ("b", "c", "d")  # def fn(self, a, b=0, c=0, *, d=0)
POSITIONAL_DEFAULTED = ("b", "c")


def _write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class _Package:
    """A generated package: functions and methods ``fn_i``, call sites in
    production modules, scripts, dead functions and tests, and per function
    the parameters each *counted* call binds (the oracle)."""

    def __init__(self, rng: random.Random, n: int, root: Path) -> None:
        self.methods = {i for i in range(n) if rng.random() < 0.4}
        self.passed: dict[int, set[str]] = {i: set() for i in range(n)}
        self.counted_calls: dict[int, int] = dict.fromkeys(range(n), 0)
        self.non_call_use: set[int] = set()
        self.unbound_call: set[int] = set()
        self.dead_holders: set[str] = set()
        defs = []
        for i in range(n):
            if i in self.methods:
                defs.append(
                    f"class C{i}:\n    def fn_{i}(self, a, b=0, c=0, *, d=0):\n"
                    "        return a\n"
                )
            else:
                defs.append(f"def fn_{i}(a, b=0, c=0, *, d=0, e=0):\n    return a\n")
        _write(root, "abicheck/m.py", "\n".join(defs))
        live, dead, script, tests, imports = [], [], [], [], []
        for i in range(n):
            for k in range(rng.randint(0, 4)):
                where = rng.choice(["live", "live", "script", "dead", "test"])
                text, binds = self._call(rng, i, imports)
                counted = where in ("live", "script")
                if counted:
                    self.counted_calls[i] += 1
                    self.passed[i] |= binds
                body = f"    {text}\n"
                if where == "live":
                    live.append(f"def use_{i}_{k}(obj):\n{body}")
                elif where == "script":
                    script.append(f"def use_{i}_{k}(obj):\n{body}")
                elif where == "dead":
                    dead.append(f"def dead_{i}_{k}(obj):\n{body}")
                    self.dead_holders.add(f"abicheck/caller.py::dead_{i}_{k}")
                else:
                    tests.append(f"def test_{i}_{k}(obj):\n{body}")
            roll = rng.random()
            if roll < 0.12:
                live.append(f"REGISTRY_{i} = [{self._ref(i)}]\n")
                self.non_call_use.add(i)
            elif roll < 0.2:
                live.append(f"NAME_{i} = 'fn_{i}'\n")  # a getattr-able name
                self.non_call_use.add(i)
            elif roll < 0.3:
                live.append(f"MESSAGE_{i} = 'fn_{i} failed: try again'\n")
        header = "from abicheck import m\nfrom abicheck.m import *\n" + "".join(
            sorted(set(imports))
        )
        _write(root, "abicheck/caller.py", header + "\n".join(live + dead))
        _write(root, "scripts/tool.py", header + "\n".join(script))
        _write(root, "tests/test_gen.py", header + "\n".join(tests))

    def _ref(self, i: int) -> str:
        return f"C{i}.fn_{i}" if i in self.methods else f"fn_{i}"

    def _call(self, rng: random.Random, i: int, imports: list[str]):
        method = i in self.methods
        defaulted = METHOD_DEFAULTED if method else FUNC_DEFAULTED
        callee = f"obj.fn_{i}" if method else f"fn_{i}"
        form = rng.choice(["kw", "pos", "star", "dstar", "alias", "unbound"])
        if form == "alias" and not method:
            imports.append(f"from abicheck.m import fn_{i} as alias_{i}\n")
            chosen = set(rng.sample(defaulted, rng.randint(0, len(defaulted))))
            kws = "".join(f", {p}=1" for p in sorted(chosen))
            return f"alias_{i}(0{kws})", chosen
        if form == "unbound" and method:
            # ``C.fn(obj, a, b)``: binds b; the pass may also count c.
            self.unbound_call.add(i)
            return f"C{i}.fn_{i}(obj, 0, 1)", {"b"}
        if form == "pos":
            k = rng.randint(1, 3)  # a, then b, then c
            args = ", ".join(str(x) for x in range(k))
            return f"{callee}({args})", set(POSITIONAL_DEFAULTED[: k - 1])
        if form == "star":
            return f"{callee}(*range(3))", set(POSITIONAL_DEFAULTED)
        if form == "dstar":
            return f"{callee}(0, **{{}})", set(defaulted)
        chosen = set(rng.sample(defaulted, rng.randint(0, len(defaulted))))
        kws = "".join(f", {p}=1" for p in sorted(chosen))
        return f"{callee}(0{kws})", chosen

    def fid(self, i: int) -> str:
        qual = f"C{i}.fn_{i}" if i in self.methods else f"fn_{i}"
        return f"abicheck/m.py::{qual}"


@pytest.mark.parametrize("seed", range(30))
def test_reported_exactly_when_no_counted_call_passes_it(tmp_path, seed) -> None:
    rng = random.Random(seed)
    pkg = _Package(rng, rng.randint(3, 12), tmp_path)
    report = pr.dead_parameters(tmp_path, dead_functions=pkg.dead_holders)
    for i in range(len(pkg.passed)):
        fid = pkg.fid(i)
        defaulted = METHOD_DEFAULTED if i in pkg.methods else FUNC_DEFAULTED
        unpassed = [p for p in defaulted if p not in pkg.passed[i]]
        if i in pkg.non_call_use:
            assert fid in report.unverifiable, fid
            assert fid not in report.dead
        elif not pkg.counted_calls[i]:
            assert fid not in report.dead and fid not in report.unverifiable
        elif i in pkg.unbound_call:
            # Conservative counting: never reports a parameter a call binds.
            assert not set(report.dead.get(fid, ())) & pkg.passed[i]
            assert set(report.dead.get(fid, ())) <= set(unpassed)
        else:
            assert report.dead.get(fid, []) == unpassed, fid
    assert not report.documented  # no docs tree in the generated package


def test_a_documented_function_is_listed_apart(tmp_path) -> None:
    _write(tmp_path, "abicheck/a.py", "def api(x, *, verbose=False):\n    return x\n")
    _write(tmp_path, "abicheck/b.py", "from abicheck.a import api\nAPI = 1\napi(1)\n")
    _write(tmp_path, "docs/use/api.md", "Call `api(x, verbose=True)`.\n")
    report = pr.dead_parameters(tmp_path)
    assert report.documented == {"abicheck/a.py::api": ["verbose"]}
    assert not report.dead


def test_a_reexport_tuple_is_not_a_use(tmp_path) -> None:
    """``_ = (f,)`` keeps a linter from dropping an import tests reach for;
    like ``__all__``, it is not a use, so it neither makes ``f``
    uncheckable nor keeps it live."""
    _write(tmp_path, "abicheck/a.py", "def f(x, y=0):\n    return x\n")
    _write(
        tmp_path,
        "abicheck/b.py",
        "from abicheck.a import f\n_ = (f,)\n\ndef g():\n    return f(1)\n",
    )
    report = pr.dead_parameters(tmp_path)
    assert report.dead == {"abicheck/a.py::f": ["y"]}
    assert ("f", 1) not in pr.python_names("_ = (f,)\n")
    assert ("f", 1) in pr.python_names("_ = [f(1)]\n")


def test_dead_subcommand_reports_parameters(tmp_path, capsys) -> None:
    import json

    import usecase_paths as up

    _write(
        tmp_path,
        "abicheck/a.py",
        "def ran(x, *, unused=None, used=None):\n    return x\n"
        "def caller():\n    return ran(1, used=2)\n",
    )
    recording = tmp_path / "rec.json"
    recording.write_text(
        json.dumps(
            {
                "schema_version": up.SCHEMA_VERSION,
                "runs": {
                    "r": {
                        "use_case": "UC-X",
                        "functions": ["abicheck/a.py::ran", "abicheck/a.py::caller"],
                    }
                },
                "inventory": up.inventory(tmp_path),
                "failures": [],
            }
        ),
        encoding="utf-8",
    )
    out = tmp_path / "dead.json"
    assert (
        up.main(["dead", str(recording), "--root", str(tmp_path), "--json", str(out)])
        == 0
    )
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert doc["dead_parameters"] == {"abicheck/a.py::ran": ["unused"]}
    assert "- `abicheck/a.py::ran`: `unused`" in capsys.readouterr().out
