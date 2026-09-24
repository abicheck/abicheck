#!/usr/bin/env python3
# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""What the header graph and the public-surface graph cost, per dump/compare.

Measurement tooling for Phase 5 of
``docs/contribute/plans/evidence-entity-model.md`` (invariant I6: no graph
view becomes unconditional without a peak-RSS and latency measurement on a
real large-product dump). It changes no production behavior: each variant
runs the real CLI in a child interpreter whose *module constants* are set
before ``abicheck.cli.main`` is entered.

Variants:

* ``none``  -- ``service_dump_native._HEADER_GRAPH_ENABLED`` and
  ``_HEADER_GRAPH_INCLUDES_ENABLED`` off: the plain snapshot.
* ``graph`` -- today's default: the header graph attached
  (``service_header_graph_attach._attach_header_graph``).
* ``graph+facts`` -- ``graph`` plus ``compare.surface_graph.
  build_public_surface_facts`` run over the attached graph, i.e. the
  population pass ``_attach_header_graph`` deliberately skips. The populated
  graph is what gets serialized, so its persisted cost is measured too.

Per variant and repeat, three steps each run in their own child with a cold,
private ``ABICHECK_CACHE_DIR``: ``dump`` OLD, ``dump`` NEW, and ``compare``
of the two stored snapshots. The parent samples parent RSS, process-tree
RSS/PSS and cgroup peak with ``bench_release_memory._Sampler`` (four
separate figures, never folded), and reads node/edge counts and raw/zstd
sizes from the written snapshots, plus -- in a separate child per stored
snapshot -- the evidence-entity-model Phase 2 join timings and state counts
(``exports``/``debug_type_of``, per side). ``--tracemalloc`` adds one separate
allocation-attribution run per variant (never mixed with the timing runs).

``--old-lib``/``--new-lib`` may instead name two release *directories*
(e.g. each holding ``libonedal.so.3`` and ``libonedal_dpc.so.3``): each
variant/repeat then dumps every member of each side, runs a stored/stored
directory ``compare`` of those snapshots (the path that pays for a persisted
graph), and a live directory/package ``compare`` (the release fan-out dumps
every member itself and never serializes a graph), with ``--old-header``/
``--new-header`` and ``-I``/``-J`` passed as ``old=``/``new=`` values there.

The child imports the ``abicheck`` of the checkout this script lives in, so
comparing two revisions means running it from two worktrees.

Usage::

    python scripts/bench_graph_materialization.py \\
        --old-lib old/lib/libfoo.so --old-header old/include/foo.hpp \\
        --new-lib new/lib/libfoo.so --new-header new/include/foo.hpp \\
        -I old/include -J new/include --repeat 3 --tracemalloc \\
        --work /tmp/graphbench --out results.json
"""

from __future__ import annotations

import argparse
import gc
import json
import os
import shutil
import statistics
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

VARIANTS = ("none", "graph", "graph+facts")


# ---------------------------------------------------------------------------
# Child: run the real CLI under one variant
# ---------------------------------------------------------------------------


def _child(variant: str, argv: list[str]) -> int:
    # Measure *this checkout's* abicheck, not whichever one the interpreter's
    # editable install points at: comparing two revisions means running this
    # script from two worktrees, and each child must import its own tree.
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from abicheck import service_dump_native as native
    from abicheck.compare.surface_graph import build_public_surface_facts
    from abicheck.workflows import memory_trace

    if variant == "none":
        native._HEADER_GRAPH_ENABLED = False
        native._HEADER_GRAPH_INCLUDES_ENABLED = False

    original = native._attach_header_graph

    def attach(snap, *args, **kwargs):  # type: ignore[no-untyped-def]
        started = time.monotonic()
        snap = original(snap, *args, **kwargs)
        attach_s = time.monotonic() - started
        graph = snap.surface_graph
        before = (len(graph.nodes), len(graph.edges)) if graph is not None else (0, 0)
        facts_s = 0.0
        if variant == "graph+facts" and graph is not None:
            memory_trace.mark("bench.surface_facts:enter")
            started = time.monotonic()
            build_public_surface_facts(snap, graph)
            graph.finalize()
            facts_s = time.monotonic() - started
            memory_trace.mark("bench.surface_facts:done")
        after = (len(graph.nodes), len(graph.edges)) if graph is not None else (0, 0)
        record = {
            "attach_seconds": round(attach_s, 3),
            "surface_facts_seconds": round(facts_s, 3),
            "graph_nodes_before_facts": before[0],
            "graph_edges_before_facts": before[1],
            "graph_nodes": after[0],
            "graph_edges": after[1],
            "gc_objects": len(gc.get_objects()),
        }
        memory_trace.counts("bench.attach", **record)
        side = os.environ.get("BENCH_SIDECAR")
        if side:
            # One line per attach: a release fan-out attaches once per member.
            with open(side, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(record) + "\n")
        return snap

    native._attach_header_graph = attach  # type: ignore[assignment]

    from abicheck.cli import main

    try:
        main.main(args=argv, prog_name="abicheck", standalone_mode=False)
    except SystemExit as exc:  # compare exits with its verdict code
        return int(exc.code or 0)
    return 0


# ---------------------------------------------------------------------------
# Parent
# ---------------------------------------------------------------------------


def _run(variant: str, argv: list[str], env: dict[str, str]) -> dict[str, object]:
    import threading

    from bench_release_memory import _cgroup_memory, _Sampler

    # `_Sampler.cgroup_peak` folds `memory.peak`, which is the cgroup's
    # lifetime high-water mark: on a shared container it reports an earlier
    # job's peak, not this run's. `memory.current` sampled over the run, less
    # its reading at launch, is the attributable figure, so record it too.
    cg_base = _cgroup_memory()[0]
    cg_max: list[int | None] = [cg_base]
    stop = threading.Event()

    def _cg_poll() -> None:
        while not stop.is_set():
            cur = _cgroup_memory()[0]
            if cur is not None and (cg_max[0] is None or cur > cg_max[0]):
                cg_max[0] = cur
            stop.wait(0.05)

    poller = threading.Thread(target=_cg_poll, daemon=True)
    poller.start()
    cmd = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--_child",
        variant,
        "--",
        *argv,
    ]
    started = time.monotonic()
    proc = subprocess.Popen(
        cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    sampler = _Sampler(proc.pid)
    sampler.start()
    _, stderr = proc.communicate()
    sampler.stop.set()
    sampler.join(2)
    stop.set()
    poller.join(2)
    return {
        "cgroup_current_delta_peak_bytes": (
            None if cg_base is None or cg_max[0] is None else cg_max[0] - cg_base
        ),
        "exit_code": proc.returncode,
        "seconds": round(time.monotonic() - started, 3),
        "parent_peak_rss_bytes": sampler.parent_peak,
        "tree_peak_rss_bytes": sampler.tree_rss_peak,
        "tree_peak_pss_bytes": sampler.tree_pss_peak,
        "cgroup_peak_bytes": sampler.cgroup_peak,
        "stderr_tail": stderr.decode("utf-8", "replace")[-600:],
    }


def _graph_kinds(graph: dict[str, object]) -> tuple[list[str], list[str]]:
    """Per-node and per-edge kinds, from either stored graph encoding: the
    schema-v49 graph table (``storage/graph_table_codec.py``) or the older
    per-entity ``SourceGraphSummary.to_dict()`` form."""
    nodes = graph.get("nodes") or []
    edges = graph.get("edges") or []
    if isinstance(nodes, dict) and isinstance(edges, dict):
        strings = graph.get("strings") or []
        return (
            [strings[i] for i in nodes.get("kind", [])],  # type: ignore[index]
            [strings[i] for i in edges.get("kind", [])],  # type: ignore[index]
        )
    return (
        [n.get("kind", "?") for n in nodes],  # type: ignore[union-attr]
        [e.get("edge") or e.get("kind") or "?" for e in edges],  # type: ignore[union-attr]
    )


def _snapshot_stats(path: Path) -> dict[str, object]:
    import zstandard

    raw = path.read_bytes()
    doc = json.loads(raw)
    graph = (
        doc.get("sections", {}).get("graph", {}).get("payload", {}).get("surface_graph")
        or doc.get("surface_graph")
        or {}
    )
    graph_bytes = json.dumps(graph, separators=(",", ":")).encode() if graph else b""
    cctx = zstandard.ZstdCompressor(level=3)
    node_kind_list, edge_kind_list = _graph_kinds(graph)
    node_kinds: dict[str, int] = {}
    for kind in node_kind_list:
        node_kinds[kind] = node_kinds.get(kind, 0) + 1
    edge_kinds: dict[str, int] = {}
    for kind in edge_kind_list:
        edge_kinds[kind] = edge_kinds.get(kind, 0) + 1
    return {
        "raw_bytes": len(raw),
        "zstd3_bytes": len(cctx.compress(raw)),
        "graph_compact_bytes": len(graph_bytes),
        "graph_zstd3_bytes": len(cctx.compress(graph_bytes)) if graph_bytes else 0,
        "nodes": len(node_kind_list),
        "edges": len(edge_kind_list),
        "node_kinds": node_kinds,
        "edge_kinds": edge_kinds,
    }


def _join_child(path: str) -> int:
    """Child entry: time the evidence-entity-model Phase 2 joins over one
    stored snapshot and print their per-side state counts as JSON. A checkout
    that predates the joins prints ``{}``."""
    try:
        from abicheck.compare.debug_type_join import join_debug_types
        from abicheck.compare.export_join import join_exports
        from abicheck.model.graph_entity_identity import snapshot_identities
    except ImportError:
        print("{}")
        return 0
    from abicheck.serialization import load_snapshot

    snap = load_snapshot(Path(path))
    t0 = time.perf_counter()
    ids = snapshot_identities(snap)
    t1 = time.perf_counter()
    exports = join_exports(snap, ids)
    t2 = time.perf_counter()
    debug = join_debug_types(snap, ids)
    t3 = time.perf_counter()
    print(
        json.dumps(
            {
                "identities_seconds": round(t1 - t0, 3),
                "exports": {
                    "seconds": round(t2 - t1, 3),
                    "complete": exports.complete,
                    "states": exports.join.state_counts(),
                },
                "debug_type_of": {
                    "seconds": round(t3 - t2, 3),
                    "complete": debug.complete,
                    "odr_observed": debug.odr_observed,
                    "states": debug.join.state_counts(),
                },
            }
        )
    )
    return 0


def _join_stats(path: Path) -> dict[str, object]:
    """:func:`_join_child`'s answer for *path*, from a fresh interpreter so
    loading the snapshot never inflates this parent's own memory."""
    proc = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), "--_joins", str(path)],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return {"error": proc.stderr[-2000:]}
    return json.loads(proc.stdout.strip().splitlines()[-1] or "{}")


def _dump_argv(lib: str, header: str, includes: list[str], out: Path) -> list[str]:
    argv = ["dump", lib, "-H", header]
    for inc in includes:
        argv += ["-I", inc]
    return [*argv, "-o", str(out)]


_LIBRARY_SUFFIXES = (".so", ".dll", ".dylib")


def _release_members(lib_dir: Path) -> list[Path]:
    """The shared libraries in *lib_dir* (versioned ``libfoo.so.3`` too);
    headers, metadata and subdirectories are not members."""
    members = sorted(
        p
        for p in lib_dir.iterdir()
        if p.is_file()
        and (
            p.suffix in _LIBRARY_SUFFIXES
            or any(f"{sfx}." in p.name for sfx in _LIBRARY_SUFFIXES)
        )
    )
    if not members:
        raise SystemExit(f"{lib_dir}: no shared library to measure")
    return members


def _release_steps(args: argparse.Namespace, wd: Path) -> dict[str, list[str]]:
    """Release mode: one ``dump`` per member and side into ``wd/old`` and
    ``wd/new``, a stored/stored directory ``compare`` of those (the path a
    persisted graph is paid for), then the live directory ``compare``."""
    steps: dict[str, list[str]] = {}
    for side, lib_dir, header, incs in (
        ("old", args.old_lib, args.old_header, args.old_inc),
        ("new", args.new_lib, args.new_header, args.new_inc),
    ):
        # A reused --work may hold a member that no longer exists; the stored
        # compare and the size scan must see only this run's snapshots.
        shutil.rmtree(wd / side, ignore_errors=True)
        (wd / side).mkdir(parents=True)
        for member in _release_members(Path(lib_dir)):
            steps[f"dump_{side}_{member.name}"] = _dump_argv(
                str(member), header, incs, wd / side / f"{member.name}.json"
            )
    steps["compare_stored"] = [
        "compare",
        str(wd / "old"),
        str(wd / "new"),
        "-o",
        f"json={wd / 'report_stored.json'}",
    ]
    steps["compare_release"] = _release_argv(args, wd)
    return steps


def _release_argv(args: argparse.Namespace, wd: Path) -> list[str]:
    """A live directory/package ``compare`` of two release directories: the
    release fan-out dumps every member itself, so there is no separate dump
    step (and no stored snapshot to size)."""
    argv = [
        "compare",
        args.old_lib,
        args.new_lib,
        "--header",
        f"old={args.old_header}",
        "--header",
        f"new={args.new_header}",
    ]
    for inc in args.old_inc:
        argv += ["-I", f"old={inc}"]
    for inc in args.new_inc:
        argv += ["-I", f"new={inc}"]
    return [*argv, "-o", f"json={wd / 'report.json'}"]


def _mib(v: object) -> float | None:
    return None if v is None else round(int(v) / 2**20, 1)  # type: ignore[call-overload]


def _summary(rows: list[dict[str, object]]) -> dict[str, object]:
    out: dict[str, object] = {}
    for key in (
        "seconds",
        "parent_peak_rss_bytes",
        "tree_peak_rss_bytes",
        "tree_peak_pss_bytes",
        "cgroup_peak_bytes",
        "cgroup_current_delta_peak_bytes",
    ):
        vals = [float(r[key]) for r in rows if r.get(key) is not None]  # type: ignore[arg-type]
        if not vals:
            out[key] = None
            continue
        scale = 1.0 if key == "seconds" else 2**20
        vals = [v / scale for v in vals]
        out[key] = {
            "mean": round(statistics.mean(vals), 2),
            "stdev": round(statistics.stdev(vals), 2) if len(vals) > 1 else 0.0,
            "min": round(min(vals), 2),
            "max": round(max(vals), 2),
            "n": len(vals),
        }
    return out


def _run_tracemalloc(
    args: argparse.Namespace,
    variants: list[str],
    base_env: dict[str, str],
    results: dict[str, object],
) -> None:
    """One allocation-attribution dump per variant, never mixed with the
    timing runs (tracemalloc perturbs the time and RSS it sits beside)."""
    for variant in variants:
        wd = args.work / variant.replace("+", "_") / "tracemalloc"
        wd.mkdir(parents=True, exist_ok=True)
        env = dict(base_env)
        env["ABICHECK_CACHE_DIR"] = str(wd / "cache")
        env["XDG_CACHE_HOME"] = str(wd / "xdg")
        env["ABICHECK_MEMORY_TRACE"] = str(wd / "dump_old.trace.jsonl")
        env["ABICHECK_MEMORY_TRACE_TRACEMALLOC"] = "1"
        traced_lib = (
            str(_release_members(Path(args.old_lib))[0])
            if Path(args.old_lib).is_dir()
            else args.old_lib
        )
        row = _run(
            variant,
            _dump_argv(traced_lib, args.old_header, args.old_inc, wd / "old.json"),
            env,
        )
        samples = [
            json.loads(line)
            for line in (wd / "dump_old.trace.jsonl").read_text().splitlines()
            if line.strip()
        ]
        results["tracemalloc"][variant] = {  # type: ignore[index]
            "seconds": row["seconds"],
            "marks": [
                {
                    "event": s["event"],
                    "t": s.get("t"),
                    "tracemalloc_current_mib": _mib(s.get("tracemalloc_current_bytes")),
                    "tracemalloc_phase_peak_mib": _mib(
                        s.get("tracemalloc_phase_peak_bytes")
                    ),
                    "counts": s.get("counts"),
                }
                for s in samples
                if "tracemalloc_current_bytes" in s or s.get("kind") == "counts"
            ],
        }
        print(f"tracemalloc {variant} {row['seconds']}s", flush=True)
        args.out.write_text(json.dumps(results, indent=2), encoding="utf-8")


def _summaries(runs: list[dict[str, object]], variants: list[str]) -> dict[str, object]:
    """Per variant and step, in the order the steps ran (release mode names
    one step per member plus the two compares)."""
    summary: dict[str, object] = {}
    for variant in variants:
        steps_seen = dict.fromkeys(r["step"] for r in runs if r["variant"] == variant)
        for step in steps_seen:
            rows = [r for r in runs if r["variant"] == variant and r["step"] == step]
            summary[f"{variant}/{step}"] = _summary(rows)
    return summary


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["--_child"]:
        variant, rest = argv[1], argv[3:]
        return _child(variant, rest)
    if argv[:1] == ["--_joins"]:
        return _join_child(argv[1])

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--old-lib", required=True)
    ap.add_argument("--old-header", required=True)
    ap.add_argument("--new-lib", required=True)
    ap.add_argument("--new-header", required=True)
    ap.add_argument("-I", dest="old_inc", action="append", default=[])
    ap.add_argument("-J", dest="new_inc", action="append", default=[])
    ap.add_argument("--variants", default=",".join(VARIANTS))
    ap.add_argument("--repeat", type=int, default=3)
    ap.add_argument("--tracemalloc", action="store_true")
    ap.add_argument("--work", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args(argv)

    base_env = {
        k: v
        for k, v in os.environ.items()
        if k not in ("ABICHECK_MEMORY_TRACE", "ABICHECK_MEMORY_TRACE_TRACEMALLOC")
    }
    results: dict[str, object] = {"runs": [], "snapshots": {}, "tracemalloc": {}}
    runs: list[dict[str, object]] = results["runs"]  # type: ignore[assignment]
    variants = [v for v in args.variants.split(",") if v]
    for rep in range(args.repeat):
        for variant in variants:
            wd = args.work / variant.replace("+", "_") / f"r{rep}"
            wd.mkdir(parents=True, exist_ok=True)
            release = Path(args.old_lib).is_dir()
            steps = (
                _release_steps(args, wd)
                if release
                else {
                    "dump_old": _dump_argv(
                        args.old_lib, args.old_header, args.old_inc, wd / "old.json"
                    ),
                    "dump_new": _dump_argv(
                        args.new_lib, args.new_header, args.new_inc, wd / "new.json"
                    ),
                    "compare": [
                        "compare",
                        str(wd / "old.json"),
                        str(wd / "new.json"),
                        "-o",
                        f"json={wd / 'report.json'}",
                    ],
                }
            )
            for step, step_argv in steps.items():
                env = dict(base_env)
                # Both cache roots: `ABICHECK_CACHE_DIR` and the XDG root the
                # snapshot/AST caches fall back to. A warm entry from an earlier
                # variant would otherwise make a later one look cheaper.
                env["ABICHECK_CACHE_DIR"] = str(wd / f"cache_{step}")
                env["XDG_CACHE_HOME"] = str(wd / f"xdg_{step}")
                env["ABICHECK_MEMORY_TRACE"] = str(wd / f"{step}.trace.jsonl")
                env["BENCH_SIDECAR"] = str(wd / f"{step}.attach.json")
                # The child appends one line per attach; start from empty so a
                # reused --work directory never mixes in an earlier run.
                (wd / f"{step}.attach.json").unlink(missing_ok=True)
                row = _run(variant, step_argv, env)
                side = wd / f"{step}.attach.json"
                if side.exists():
                    attaches = [
                        json.loads(line)
                        for line in side.read_text(encoding="utf-8").splitlines()
                        if line.strip()
                    ]
                    row["attach"] = attaches[0] if len(attaches) == 1 else attaches
                row.update(variant=variant, step=step, repeat=rep)
                runs.append(row)
                print(
                    f"{variant:12} r{rep} {step:9} rc={row['exit_code']} "
                    f"{row['seconds']:8.1f}s parent={_mib(row['parent_peak_rss_bytes'])} "
                    f"treeRSS={_mib(row['tree_peak_rss_bytes'])} "
                    f"treePSS={_mib(row['tree_peak_pss_bytes'])} "
                    f"cgroupΔ={_mib(row['cgroup_current_delta_peak_bytes'])} MiB",
                    flush=True,
                )
                if row["exit_code"] not in (0, 2, 4):
                    print(row["stderr_tail"], file=sys.stderr)
            if rep == 0:
                results["snapshots"][variant] = {  # type: ignore[index]
                    str(path.relative_to(wd)): {
                        **_snapshot_stats(path),
                        "joins": _join_stats(path),
                    }
                    for path in (
                        sorted((wd / "old").glob("*.json"))
                        + sorted((wd / "new").glob("*.json"))
                        if release
                        else [wd / "old.json", wd / "new.json"]
                    )
                    if path.is_file()
                }
            args.out.write_text(json.dumps(results, indent=2), encoding="utf-8")

    if args.tracemalloc:
        _run_tracemalloc(args, variants, base_env, results)

    summary = _summaries(runs, variants)
    results["summary"] = summary
    args.out.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
