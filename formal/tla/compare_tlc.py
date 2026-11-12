#!/usr/bin/env python3
"""Run TLC on every formal/tla model and compare it with the Python BFS.

The two Python checkers (check_disposition.py, check_scope.py) are separate
transcriptions of the .tla transition relations. This script runs TLC and
fails on any difference between the two:

1. **Verdict run.** TLC on the checked-in ``MC_*.cfg`` (invariants on). The
   outcome must be the one the experiment report states (``EXPECTED``), and
   for a model that holds, TLC's distinct-state count must equal the Python
   checker's. For a violated model, TLC's counterexample must be a path of
   the Python graph that starts in an initial state and ends in a state the
   Python predicate also rejects.
2. **Graph run.** TLC on the same constants with the invariants removed and
   ``-dump dot,actionlabels``, so the whole reachable graph is explored even
   where the invariant fails. The initial states, the reachable states and
   the labelled transitions must be identical to the Python ``graph()`` for
   the same constants. Self-loops are TLC's stuttering steps (the
   ``steps = MaxSteps`` disjunct, ``Finished``); the Python checkers leave
   them out, so they are compared separately: TLC must stutter in exactly
   the states where the Python relation has no successor.

The constants are read from the cfg file, so both checkers always see the
same ones.

    python3 -I formal/tla/compare_tlc.py --jar /path/to/tla2tools.jar
"""

from __future__ import annotations

import argparse
import importlib.util
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _load(name: str):  # type: ignore[no-untyped-def]
    spec = importlib.util.spec_from_file_location(name, HERE / f"{name}.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# --------------------------------------------------------------------------
# A parser for the TLA+ values TLC prints for these specs: integers,
# booleans, strings, model values, sets, sequences and functions written
# ``(k1 :> v1 @@ k2 :> v2)``.
# --------------------------------------------------------------------------

_TOKEN = re.compile(
    r'\s*(?:(?P<num>-?\d+)|(?P<str>"(?:[^"\\]|\\.)*")|(?P<id>[A-Za-z_]\w*)'
    r"|(?P<op>:>|@@|<<|>>|[{}(),]))"
)


class _Parser:
    def __init__(self, text: str) -> None:
        self.toks: list[tuple[str, str]] = []
        pos = 0
        text = text.strip()
        while pos < len(text):
            m = _TOKEN.match(text, pos)
            if m is None or m.end() == pos:
                raise ValueError(f"cannot tokenize TLA+ value at {text[pos:]!r}")
            kind = m.lastgroup
            assert kind is not None
            self.toks.append((kind, m.group(kind)))
            pos = m.end()
        self.i = 0

    def peek(self) -> str | None:
        return self.toks[self.i][1] if self.i < len(self.toks) else None

    def take(self, expected: str | None = None) -> tuple[str, str]:
        if self.i >= len(self.toks):
            raise ValueError("unexpected end of TLA+ value")
        tok = self.toks[self.i]
        if expected is not None and tok[1] != expected:
            raise ValueError(f"expected {expected!r}, got {tok[1]!r}")
        self.i += 1
        return tok

    def value(self):  # type: ignore[no-untyped-def]
        kind, tok = self.take()
        if kind == "num":
            return int(tok)
        if kind == "str":
            return tok[1:-1]
        if kind == "id":
            return {"TRUE": True, "FALSE": False}.get(tok, ("mv", tok))
        if tok == "{":
            items = self._items("}")
            return frozenset(items)
        if tok == "<<":
            return tuple(self._items(">>"))
        if tok == "(":
            fn = {}
            while True:
                k = self.value()
                self.take(":>")
                fn[k] = self.value()
                if self.peek() == "@@":
                    self.take("@@")
                    continue
                self.take(")")
                return ("fn", frozenset(fn.items()))
        raise ValueError(f"unexpected token {tok!r}")

    def _items(self, close: str) -> list:
        items: list = []
        if self.peek() == close:
            self.take(close)
            return items
        while True:
            items.append(self.value())
            if self.peek() == ",":
                self.take(",")
                continue
            self.take(close)
            return items

    def done(self) -> bool:
        return self.i == len(self.toks)


def parse_value(text: str):  # type: ignore[no-untyped-def]
    p = _Parser(text)
    v = p.value()
    if not p.done():
        raise ValueError(f"trailing tokens in {text!r}")
    return v


def parse_conjuncts(lines: list[str]) -> dict[str, object]:
    """``/\\ var = value`` lines (one state as TLC prints it) -> {var: value}."""
    out: dict[str, object] = {}
    cur: list[str] = []
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        if line.startswith("/\\ "):
            if cur:
                name, _, val = " ".join(cur)[3:].partition(" = ")
                out[name.strip()] = parse_value(val)
            cur = [line]
        else:  # a long value TLC wrapped onto the next line
            cur.append(line)
    if cur:
        name, _, val = " ".join(cur)[3:].partition(" = ")
        out[name.strip()] = parse_value(val)
    return out


def _unescape_dot(label: str) -> str:
    out: list[str] = []
    i = 0
    while i < len(label):
        c = label[i]
        if c == "\\" and i + 1 < len(label):
            nxt = label[i + 1]
            out.append("\n" if nxt == "n" else nxt)
            i += 2
            continue
        out.append(c)
        i += 1
    return "".join(out)


_NODE = re.compile(r'^(-?\d+) \[label="((?:[^"\\]|\\.)*)"(,style = filled)?\];?$')
_EDGE = re.compile(r'^(-?\d+) -> (-?\d+) \[label="((?:[^"\\]|\\.)*)"')


@dataclass
class DotGraph:
    init: set
    states: set
    edges: set  # (src, label, dst), self-loops excluded
    stutter: set  # states with a TLC self-loop


def parse_dot(text: str, to_state) -> DotGraph:  # type: ignore[no-untyped-def]
    """TLC's ``-dump dot,actionlabels`` output -> a graph over normalized
    states. ``to_state`` maps TLC's {var: value} to the Python checker's
    state tuple."""
    nodes: dict[str, object] = {}
    init: set = set()
    raw_edges: list[tuple[str, str, str]] = []
    for line in text.splitlines():
        m = _NODE.match(line)
        if m:
            fp, label, filled = m.groups()
            st = to_state(parse_conjuncts(_unescape_dot(label).split("\n")))
            if fp in nodes and nodes[fp] != st:
                raise ValueError(f"fingerprint {fp} names two different states")
            nodes[fp] = st
            if filled:
                init.add(st)
            continue
        m = _EDGE.match(line)
        if m:
            raw_edges.append((m.group(1), _unescape_dot(m.group(3)), m.group(2)))
    edges: set = set()
    stutter: set = set()
    for src, label, dst in raw_edges:
        s, d = nodes[src], nodes[dst]
        if s == d:
            stutter.add(s)
        else:
            edges.add((s, label, d))
    return DotGraph(init, set(nodes.values()), edges, stutter)


# --------------------------------------------------------------------------
# Models: constants from the cfg, TLC state -> Python state tuple.
# --------------------------------------------------------------------------


def _model_index(mv: object, prefix: str) -> int:
    assert isinstance(mv, tuple) and mv[0] == "mv", mv
    name = mv[1]
    assert isinstance(name, str) and name.startswith(prefix), name
    return int(name[len(prefix) :])


def read_constants(cfg: Path) -> dict[str, object]:
    text = cfg.read_text(encoding="utf-8")
    body = text.split("CONSTANTS", 1)[1]
    body = re.split(r"^\s*(?:INIT|NEXT|SPECIFICATION|INVARIANTS?)\b", body, flags=re.M)[
        0
    ]
    out: dict[str, object] = {}
    for m in re.finditer(r"(\w+)\s*=\s*(\{[^}]*\}|\w+)", body):
        out[m.group(1)] = parse_value(m.group(2))
    return out


@dataclass
class Model:
    cfg: str
    module: str
    expected: str  # "holds" or the name of the invariant TLC must report

    def python(self, consts: dict[str, object], mutant: str | None = None):  # type: ignore[no-untyped-def]
        """(graph, check() state count, check() found a violation,
        invariant predicate, TLC-state converter) for this model. ``mutant``
        seeds a known-wrong check_scope.py variant, to show the comparison
        can fail."""
        if self.module == "DispositionPipeline":
            chk = _load("check_disposition")
            ids = consts["Ids"]
            assert isinstance(ids, frozenset)
            n = len(ids)
            assert {_model_index(v, "f") for v in ids} == set(range(1, n + 1))
            steps, adds = consts["MaxSteps"], consts["AddsAllowed"]

            def to_state(v: dict) -> tuple:
                def ints(s: object) -> frozenset[int]:
                    assert isinstance(s, frozenset)
                    return frozenset(_model_index(x, "f") for x in s)

                return (
                    ints(v["changes"]),
                    ints(v["buckets"]),
                    ints(v["ledger"]),
                    ints(v["seen"]),
                    ints(v["fresh"]),
                    v["steps"],
                )

            g = chk.graph(n, steps, adds)
            n_check, trace = chk.check(n, steps, adds)
            return g, n_check, trace is not None, chk.nothing_vanishes, to_state
        if self.module == "ScopeCompleteness":
            chk = _load("check_scope")
            members = consts["Members"]
            assert isinstance(members, frozenset)
            n = len(members)
            assert {_model_index(v, "m") for v in members} == set(range(1, n + 1))

            def to_state(v: dict) -> tuple:
                fn = v["state"]
                assert isinstance(fn, tuple) and fn[0] == "fn"
                by_index = {_model_index(k, "m"): s for k, s in fn[1]}
                return (
                    tuple(by_index[i] for i in range(1, n + 1)),
                    v["newProven"],
                    v["policy"],
                )

            spec = chk.Spec(mutant)
            g = chk.graph(n, spec)
            n_check, found = chk.check(n, spec)

            def ok(st: tuple) -> bool:
                return not chk.invariant_violations(spec, st)

            return g, n_check, found is not None, ok, to_state
        raise ValueError(self.module)


MODELS = (
    Model("MC_noadds.cfg", "DispositionPipeline", "holds"),
    Model("MC_adds.cfg", "DispositionPipeline", "NothingVanishes"),
    Model("MC_scope.cfg", "ScopeCompleteness", "holds"),
)


# --------------------------------------------------------------------------
# Running TLC
# --------------------------------------------------------------------------


def run_tlc(
    jar: Path, java: str, work: Path, module: str, cfg: Path, extra: list[str]
) -> str:
    """Run TLC in ``work`` on a copy of the module. TLC resolves -config
    against the spec's directory, so the spec and cfg sit side by side."""
    run_dir = Path(tempfile.mkdtemp(dir=work, prefix=f"{module}-"))
    (run_dir / f"{module}.tla").write_text(
        (HERE / f"{module}.tla").read_text(encoding="utf-8"), encoding="utf-8"
    )
    (run_dir / cfg.name).write_text(cfg.read_text(encoding="utf-8"), encoding="utf-8")
    meta = run_dir / "meta"
    cmd = [
        java,
        "-XX:+UseParallelGC",
        "-cp",
        str(jar),
        "tlc2.TLC",
        "-workers",
        "1",
        "-nowarning",
        "-metadir",
        str(meta),
        "-config",
        cfg.name,
        *extra,
        f"{module}.tla",
    ]
    proc = subprocess.run(cmd, cwd=run_dir, capture_output=True, text=True, check=False)
    out = proc.stdout + proc.stderr
    banner = re.search(r"^TLC2 Version .*$", out, flags=re.M)
    if banner and banner.group(0) not in _BANNERS:
        _BANNERS.append(banner.group(0))
        print(f"TLC: {banner.group(0)}")
    return out


_BANNERS: list[str] = []


def _distinct(out: str) -> int:
    m = re.search(r"(\d+) distinct states found", out)
    if m is None:
        raise RuntimeError("TLC printed no state count:\n" + out)
    return int(m.group(1))


def tlc_trace(out: str, to_state) -> list:  # type: ignore[no-untyped-def]
    """The counterexample TLC printed after 'The behavior up to this point is:'."""
    body = out.split("The behavior up to this point is:", 1)[1]
    blocks = re.split(r"^State \d+: .*$", body, flags=re.M)[1:]
    trace = []
    for block in blocks:
        lines = []
        for line in block.splitlines():
            if not line.strip():
                if lines:
                    break
                continue
            lines.append(line)
        trace.append(to_state(parse_conjuncts(lines)))
    return trace


def _sample(items: set, k: int = 3) -> str:
    return "\n      ".join(repr(x) for x in sorted(items, key=repr)[:k])


def compare_model(
    model: Model, jar: Path, java: str, work: Path, mutant: str | None = None
) -> list[str]:
    cfg = HERE / model.cfg
    consts = read_constants(cfg)
    (p_init, p_states, p_edges), n_check, p_violated, inv_ok, to_state = model.python(
        consts, mutant
    )
    errors: list[str] = []
    name = model.cfg.removesuffix(".cfg")

    # 1. verdict run on the checked-in cfg
    out = run_tlc(jar, java, work, model.module, cfg, [])
    if "Parsing or semantic analysis failed" in out or "Deadlock reached" in out:
        return [f"{name}: TLC failed:\n{out}"]
    if p_violated != (model.expected != "holds"):
        errors.append(
            f"{name}: Python check() {'finds a violation' if p_violated else 'holds'}, "
            f"expected {model.expected}"
        )
    if model.expected == "holds":
        if "Model checking completed. No error has been found." not in out:
            errors.append(f"{name}: TLC did not report success:\n{out}")
        elif _distinct(out) != n_check:
            errors.append(
                f"{name}: TLC {_distinct(out)} distinct states, Python check() {n_check}"
            )
        verdict = "holds"
    else:
        if f"Invariant {model.expected} is violated" not in out:
            errors.append(
                f"{name}: TLC did not report {model.expected} violated:\n{out}"
            )
            verdict = "?"
        else:
            trace = tlc_trace(out, to_state)
            if not trace or trace[0] not in p_init:
                errors.append(
                    f"{name}: TLC counterexample does not start in a Python initial state"
                )
            for a, b in zip(trace, trace[1:]):
                if not any(e[0] == a and e[2] == b for e in p_edges):
                    errors.append(
                        f"{name}: TLC counterexample step {a} -> {b} is not a Python transition"
                    )
            if trace and inv_ok(trace[-1]):
                errors.append(
                    f"{name}: Python predicate accepts TLC's violating state {trace[-1]}"
                )
            if trace and not all(inv_ok(s) for s in trace[:-1]):
                errors.append(
                    f"{name}: TLC counterexample is not minimal under the Python predicate"
                )
            verdict = f"{model.expected} violated, TLC trace length {len(trace)}"

    # 2. graph run: same constants, no invariants, full dump
    graph_cfg = work / f"{name}_graph.cfg"
    graph_cfg.write_text(
        "\n".join(
            line
            for line in cfg.read_text(encoding="utf-8").splitlines()
            if not re.match(r"^\s*INVARIANTS?\b", line)
        )
        + "\n",
        encoding="utf-8",
    )
    dot = work / f"{name}.dot"
    out = run_tlc(
        jar,
        java,
        work,
        model.module,
        graph_cfg,
        ["-dump", "dot,actionlabels", str(dot)],
    )
    if "Model checking completed. No error has been found." not in out:
        return [*errors, f"{name}: TLC graph run failed:\n{out}"]
    g = parse_dot(dot.read_text(encoding="utf-8"), to_state)
    if _distinct(out) != len(g.states):
        errors.append(
            f"{name}: dot has {len(g.states)} states, TLC reported {_distinct(out)}"
        )
    for what, tlc_set, py_set in (
        ("initial states", g.init, p_init),
        ("reachable states", g.states, p_states),
        ("transitions", g.edges, p_edges),
    ):
        if tlc_set != py_set:
            errors.append(
                f"{name}: {what} differ: TLC {len(tlc_set)}, Python {len(py_set)}\n"
                f"    only in TLC:\n      {_sample(tlc_set - py_set)}\n"
                f"    only in Python:\n      {_sample(py_set - tlc_set)}"
            )
    p_dead = p_states - {e[0] for e in p_edges}
    if g.stutter != p_dead:
        errors.append(
            f"{name}: TLC stutters in {len(g.stutter)} states, Python has "
            f"{len(p_dead)} states without a successor\n"
            f"    stutter only:\n      {_sample(g.stutter - p_dead)}\n"
            f"    dead end only:\n      {_sample(p_dead - g.stutter)}"
        )
    violating = sum(1 for s in g.states if not inv_ok(s))
    print(
        f"{name}: verdict {verdict}; states TLC={len(g.states)} Python={len(p_states)}; "
        f"transitions TLC={len(g.edges)} Python={len(p_edges)}; "
        f"terminal TLC={len(g.stutter)} Python={len(p_dead)}; "
        f"states violating the invariants={violating}; "
        + ("AGREE" if not errors else "DIFFER")
    )
    return errors


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--jar", type=Path, required=True, help="path to tla2tools.jar")
    ap.add_argument("--java", default="java")
    ap.add_argument("--workdir", type=Path, default=None)
    ap.add_argument(
        "--python-mutant",
        choices=("classify-unguarded", "warn-clean-zero", "failed-ops-dropped"),
        default=None,
        help="compare MC_scope against a seeded-wrong check_scope.py; must FAIL",
    )
    a = ap.parse_args()
    with tempfile.TemporaryDirectory(prefix="tlc-") as tmp:
        work = a.workdir or Path(tmp)
        work.mkdir(parents=True, exist_ok=True)
        models = [
            m
            for m in MODELS
            if a.python_mutant is None or m.module == "ScopeCompleteness"
        ]
        errors = [
            e
            for m in models
            for e in compare_model(m, a.jar.resolve(), a.java, work, a.python_mutant)
        ]
    for e in errors:
        print("ERROR:", e, file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
