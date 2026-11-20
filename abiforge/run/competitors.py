"""Compare abicheck, libabigail ``abidiff`` and ABICC on the same generated pairs.

Usage::

    python -m abiforge.run.competitors OUT [--select pilot|all|OP[,OP...]]
        [--modes abicheck,abidiff,abicc-dump,abicc-xml]
        [--configs dwarf+headers,stripped+headers] [--jobs N] [--timeout S]

Each (operator, seed) pair is built **once per configuration** with the
harness's own compile path (``harness.build_library``); every selected mode
then reads those exact files. The configurations:

* ``dwarf+headers`` -- ``-g`` library and public header (``{B,D,H}``);
* ``stripped+headers`` -- the primary configuration, a stripped library and
  its public header (``{B,H}``); ``strip --strip-debug`` is verified.

The modes, each selectable on its own:

* ``abicheck`` -- ``abicheck compare OLD NEW --header old=.. --header new=..``;
* ``abidiff`` -- ``abidiff --headers-dir1 OLD_DIR --headers-dir2 NEW_DIR OLD NEW``;
* ``abicc-dump`` -- ``abi-dumper`` (with ``-public-headers``) on each library,
  then ``abi-compliance-checker`` on the two dumps;
* ``abicc-xml`` -- ``abi-compliance-checker`` on two input XML descriptors
  (library, public header, the header's compiler options).

Outputs in OUT: ``pairs.jsonl`` (one line per pair: hashes, inputs, every
mode's commands, exit status, time, raw report paths, decision and detail
outcomes), ``pairs.csv`` (one row per pair and mode), ``summary.json`` and
``table.md`` (``competitor_report``), raw reports under ``pairs/<id>/``.
The expected answers come from ``competitor_truth``, never from a tool.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, replace
from pathlib import Path

from abiforge.checkers import abicc, abicheck_cli, abidiff
from abiforge.checkers.tools import ToolRun, tool_version
from abiforge.model.config import Config, default_config
from abiforge.model.expect import Expectation
from abiforge.model.seeds import Seed
from abiforge.operators import OP_BY_NAME, EditOp, matrix
from abiforge.run import competitor_score as score
from abiforge.run.competitor_truth import (
    detail_truth,
    expected_class,
    language_expectation,
)
from abiforge.run.harness import (
    CompileError,
    Library,
    build_library,
    debug_sections,
    label_toolchain,
)
from abiforge.run.toolchains import capability_gap

MODES = ("abicheck", "abidiff", "abicc-dump", "abicc-xml")
CONFIGS = ("dwarf+headers", "stripped+headers")
CONTROL = "control_identity"
#: The pilot's selection rule: per expected class, in this order of seeds and
#: in operator-registry order, the first ``PILOT_PER_CLASS`` operators whose
#: ground truth is config-independent (no ``rules``), that rebuild nothing,
#: carry no build-flag-only edit and dump both sides at the same evidence.
PILOT_SEEDS = ("c_core", "c_elf")
PILOT_CLASSES = ("abi_break", "source_break", "compatible", "risk", "unchanged")
PILOT_PER_CLASS = 3
#: The deployment-target case: a raised glibc symbol-version floor.
PILOT_EXTRA = ("glibc_floor_raised",)


@dataclass(frozen=True)
class Case:
    op: str  # operator name, or CONTROL
    seed: str
    cls: str  # expected class (``competitor_truth.CLASSES``)

    @property
    def id(self) -> str:
        return f"{self.seed}.{self.op}"


def _seeds() -> dict[str, Seed]:
    return {s.name: s for _op, s in matrix()}


def _plain(op: EditOp) -> bool:
    return not (
        op.rules
        or op.rebuild
        or op.carrier
        or op.new_evidence
        or op.needs_libstdcxx
        or op.host_arch
        or op.licences
    )


def pilot_cases() -> list[Case]:
    """The deterministic pilot (see ``PILOT_*``), then one identity control per seed used."""
    chosen: list[Case] = []
    pairs = matrix()
    for cls in PILOT_CLASSES:
        picked: list[Case] = []
        for seed in PILOT_SEEDS:
            for op, s in pairs:
                if len(picked) == PILOT_PER_CLASS:
                    break
                if s.name == seed and _plain(op) and expected_class(op.base()) == cls:
                    picked.append(Case(op.name, seed, cls))
        chosen += picked
    for name in PILOT_EXTRA:
        seed = next(s.name for op, s in pairs if op.name == name)
        chosen.append(Case(name, seed, expected_class(OP_BY_NAME[name].base())))
    for seed in dict.fromkeys(c.seed for c in chosen):
        chosen.append(Case(CONTROL, seed, "unchanged"))
    return chosen


def all_cases() -> list[Case]:
    cases = [Case(op.name, s.name, expected_class(op.base())) for op, s in matrix()]
    return cases + [Case(CONTROL, name, "unchanged") for name in _seeds()]


def make_config(seed: Seed, kind: str, compiler: str) -> Config:
    cfg = default_config(seed.lang, label_toolchain(compiler))
    return replace(cfg, evidence="headers", stripped=(kind == "stripped+headers"))


def control_expectation() -> Expectation:
    return Expectation(
        verdict_min="NO_CHANGE",
        verdict_max="NO_CHANGE",
        note="identical sources rebuilt",
    )


def sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _side_inputs(lib: Library) -> dict[str, object]:
    dbg = debug_sections(lib.so)
    return {
        "so": str(lib.so),
        "so_sha256": sha256(lib.so),
        "header": str(lib.header),
        "header_sha256": sha256(lib.header),
        "debug_sections": len(dbg),
        "gcc_options": lib.gcc_options,
    }


def _descriptor_options(lib: Library) -> str:
    """The header's compiler options for an ABICC descriptor: the include
    directory is implied by the header path ABICC is given."""
    return " ".join(o for o in lib.gcc_options.split() if not o.startswith("-I"))


def run_modes(
    modes: tuple[str, ...], old: Library, new: Library, d: Path, timeout: float
) -> dict[str, object]:
    runs: dict[str, ToolRun] = {}
    for mode in modes:
        if mode == "abicheck":
            runs[mode] = abicheck_cli.run(
                old.so,
                new.so,
                d,
                headers=([old.header], [new.header]),
                includes=(old.dir, new.dir),
                timeout=timeout,
            )
        elif mode == "abidiff":
            runs[mode] = abidiff.run(
                old.so, new.so, d, headers_dirs=(old.dir, new.dir), timeout=timeout
            )
        elif mode == "abicc-dump":
            runs[mode] = abicc.run_dumps(
                old.so, new.so, d, public_headers=(old.dir, new.dir), timeout=timeout
            )
        elif mode == "abicc-xml":
            runs[mode] = abicc.run_descriptors(
                (old.so, [old.header], _descriptor_options(old)),
                (new.so, [new.header], _descriptor_options(new)),
                d,
                lang=old.config.lang,
                timeout=timeout,
            )
    return runs


def verify_inputs(
    mode: str, ob: score.Observed, run, d: Path, old: Library, new: Library
) -> dict[str, object]:  # type: ignore[no-untyped-def]
    """What the analyzer demonstrably received, against what the pair declares."""
    dwarf = bool(debug_sections(old.so)) and bool(debug_sections(new.so))
    v: dict[str, object] = {
        "declared": ["library", "public header"] + (["DWARF"] if dwarf else []),
        "dwarf_present": dwarf,
    }
    if mode in ("abicc-dump", "abicc-xml") and "report" in run.reports:
        try:
            sides = abicc.parse((d / run.reports["report"]).read_text(encoding="utf-8"))
            b = sides.get("binary") or next(iter(sides.values()))
            v["headers_checked"], v["libs_checked"], v["symbols_checked"] = (
                b.headers_checked,
                b.libs_checked,
                b.symbols_checked,
            )
            v["received_header"] = old.header.name in b.headers_checked
            v["received_library"] = old.so.name in b.libs_checked
        except (ValueError, OSError) as exc:
            v["parse_error"] = str(exc)
    if mode == "abicheck" and "report" in run.reports:
        try:
            rep = json.loads((d / run.reports["report"]).read_text(encoding="utf-8"))
            v["evidence_tier"] = rep.get("evidence_tier")
            v["old_evidence_depth"] = rep.get("old_evidence_depth")
        except (ValueError, OSError) as exc:
            v["parse_error"] = str(exc)
    if mode == "abidiff":
        v["effective"] = (
            "DWARF + header filter"
            if dwarf
            else "ELF symbol table only (no DWARF to read; headers unusable without it)"
        )
    if mode == "abicc-dump" and not dwarf:
        v["effective"] = "abi-dumper requires DWARF; none present"
    return v


def run_case(
    job: tuple[Case, str, str, str, tuple[str, ...], float, Library | None],
) -> dict[str, object]:
    case, kind, compiler, out_s, modes, timeout, old = job
    out = Path(out_s)
    seed = _seeds()[case.seed]
    cfg = make_config(seed, kind, compiler)
    op = None if case.op == CONTROL else OP_BY_NAME[case.op]
    pid = f"{case.id}@{kind}"
    row: dict[str, object] = {
        "pair_id": pid,
        "case": case.id,
        "op": case.op,
        "seed": case.seed,
        "config": kind,
        "compiler": compiler,
        "expected_class": case.cls,
    }
    exp = control_expectation() if op is None else language_expectation(op, cfg)
    row["expected"] = {
        "verdict_min": exp.verdict_min,
        "verdict_max": exp.verdict_max,
        "note": exp.note,
        "source": "identity rebuild"
        if op is None
        else "operator language-rule expectation (EditOp.base + rules)",
    }
    if op is not None and (why := op.requires(cfg) or capability_gap(op, cfg)):
        row["status"] = f"infeasible: {why}"
        return row
    d = out / "pairs" / pid.replace("@", "__").replace("+", "_")
    d.mkdir(parents=True, exist_ok=True)
    if old is None:
        row["status"] = "build_failed: baseline (old side) did not build"
        return row
    try:
        if op is None:
            new = build_library(seed, cfg, d / "new")
        else:
            new = build_library(
                seed,
                op.new_side_config(cfg),
                d / "new",
                overrides=op.edit(seed.roles),
                flags=op.new_flags(cfg.flags),
            )
    except CompileError as exc:
        row["status"] = f"build_failed: {str(exc).splitlines()[0][:300]}"
        return row
    row["inputs"] = {"old": _side_inputs(old), "new": _side_inputs(new)}
    if kind == "stripped+headers" and (
        row["inputs"]["old"]["debug_sections"] or row["inputs"]["new"]["debug_sections"]
    ):  # type: ignore[index]
        row["status"] = "input_violation: stripped side carries DWARF"
        return row
    dt = detail_truth(seed, op, old, new, d)
    row["details_expected"] = dt.to_json()
    runs = run_modes(modes, old, new, d, timeout)
    row["modes"] = {}
    for mode, tr in runs.items():
        ob = score.OBSERVERS[mode](tr, d)
        dwarf = bool(row["inputs"]["old"]["debug_sections"])  # type: ignore[index]
        if mode == "abicc-dump" and not dwarf and ob.status == "error":
            ob.status, ob.reason = (
                "unsupported",
                f"abi-dumper needs DWARF; stripped library ({ob.reason})",
            )
        row["modes"][mode] = {  # type: ignore[index]
            "run": tr.to_json(),
            "observed": ob.to_json(),
            "decision_outcome": score.decision_outcome(mode, ob, exp),
            "details": score.detail_outcomes(mode, ob, dt),
            "inputs_verified": verify_inputs(mode, ob, tr, d, old, new),
        }
    row["status"] = "ran"
    return row


def environment() -> dict[str, object]:
    import platform
    import subprocess

    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=False
    ).stdout.strip()
    return {
        "commit": commit,
        "python": platform.python_version(),
        "platform": f"{platform.system()} {platform.machine()}",
        "gcc": tool_version("gcc"),
        "abidiff": tool_version("abidiff"),
        "abi-dumper": tool_version("abi-dumper"),
        "abi-compliance-checker": tool_version("abi-compliance-checker"),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("out", type=Path)
    ap.add_argument("--select", default="pilot", help="pilot | all | OP[,OP...]")
    ap.add_argument("--modes", default=",".join(MODES))
    ap.add_argument("--configs", default=",".join(CONFIGS))
    ap.add_argument("--compiler", default="gcc")
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--timeout", type=float, default=300.0)
    a = ap.parse_args(argv)
    modes = tuple(m for m in a.modes.split(",") if m)
    configs = tuple(c for c in a.configs.split(",") if c)
    if bad := (set(modes) - set(MODES)) | (set(configs) - set(CONFIGS)):
        ap.error(f"unknown mode/config: {sorted(bad)}")
    if a.select == "pilot":
        cases = pilot_cases()
    elif a.select == "all":
        cases = all_cases()
    else:
        names = a.select.split(",")
        cases = [c for c in all_cases() if c.op in names]
    out = a.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    # Old sides first, serially: one build per (seed, config), shared by every pair.
    olds: dict[tuple[str, str], Library | None] = {}
    for seed_name in dict.fromkeys(c.seed for c in cases):
        seed = _seeds()[seed_name]
        for kind in configs:
            try:
                olds[seed_name, kind] = build_library(
                    seed,
                    make_config(seed, kind, a.compiler),
                    out / "builds" / f"{seed_name}-{kind}-old",
                )
            except CompileError as exc:
                olds[seed_name, kind] = None
                print(f"baseline {seed_name}/{kind} failed: {exc}", file=sys.stderr)
    jobs = [
        (c, k, a.compiler, str(out), modes, a.timeout, olds[c.seed, k])
        for c in cases
        for k in configs
    ]
    with ProcessPoolExecutor(max_workers=a.jobs) as pool:
        rows = list(pool.map(run_case, jobs))
    meta = {
        "select": a.select,
        "modes": modes,
        "configs": configs,
        "compiler": a.compiler,
        "cases": [c.__dict__ for c in cases],
        "environment": environment(),
        "pilot_rule": None
        if a.select != "pilot"
        else {
            "seeds": PILOT_SEEDS,
            "classes": PILOT_CLASSES,
            "per_class": PILOT_PER_CLASS,
            "extra": PILOT_EXTRA,
            "filter": "no rules, rebuild, carrier, new_evidence, libstdc++ need, host arch or licence",
        },
    }
    (out / "run_meta.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
    with (out / "pairs.jsonl").open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, sort_keys=True) + "\n")
    from abiforge.run.competitor_report import write_reports

    write_reports(out)
    print((out / "table.md").read_text(encoding="utf-8"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
