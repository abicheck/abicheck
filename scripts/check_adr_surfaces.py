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

"""ADR <-> use case <-> public surface <-> scenario traceability gate (ADR-076).

Validates ``docs/contribute/adr/adr-surface-registry.yaml`` against the code,
the ADR index, the use-case registry and the scenario catalog, ratchets the
result against ``docs/contribute/adr/adr_surface_baseline.json`` and keeps the
generated coverage report ``docs/contribute/generated/adr-surface-coverage.md``
current.

Checks (each finding names the entry and the fix):

* **registry** — one entry per ADR file; dispositions, ``missing``/``reason``
  present where required; ``use_cases`` name real ``UC-*`` ids;
* **surfaces** — every ``cli`` surface resolves through click introspection
  of ``abicheck.cli.main``, every ``api`` symbol imports, every ``action``
  input exists in ``action.yml`` (or ``actions/<name>/action.yml``), every
  ``report`` field is a top-level property of ``abicheck/schemas/<schema>
  .schema.json`` (``format:<x>`` must be a ``compare -o`` choice);
* **status** — an ADR whose index Status reads fully implemented must be
  ``surfaced``/``internal``; a retired (Deprecated/Superseded) ADR must be
  ``internal``;
* **use-case mirror** — each UC's ``adrs:`` equals the ADRs listing it, and
  each UC carries a valid ``user_task``;
* **scenarios** — a scenario's declared ``cli`` surfaces are actually used by
  one of its ``flow`` commands;
* **ratchet** — against the baseline: no disposition downgrade, no lost
  surface or use case, no new ADR surface untraced by a scenario, no new
  multi-channel (cli+api+action) use case without a shared scenario
  ``family``. Rewrite with ``--write-baseline`` in the same PR to accept.

Usage::

    python scripts/check_adr_surfaces.py                  # check (CI)
    python scripts/check_adr_surfaces.py --write-report   # regenerate report
    python scripts/check_adr_surfaces.py --write-baseline # accept current state
"""

from __future__ import annotations

import argparse
import importlib
import json
import re
import shlex
import sys
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
ADR_DIR = ROOT / "docs" / "contribute" / "adr"
REGISTRY = ADR_DIR / "adr-surface-registry.yaml"
BASELINE = ADR_DIR / "adr_surface_baseline.json"
ADR_INDEX = ADR_DIR / "index.md"
UC_REGISTRY = ROOT / "docs" / "contribute" / "usecase-registry.yaml"
SCENARIO_DIR = ROOT / "tests" / "scenarios"
REPORT = ROOT / "docs" / "contribute" / "generated" / "adr-surface-coverage.md"
SCHEMA_DIR = ROOT / "abicheck" / "schemas"

DISPOSITIONS = ("surfaced", "partial", "gap", "internal")
SURFACE_KINDS = ("cli", "api", "action", "report")
CHANNEL_KINDS = frozenset({"cli", "api", "action"})
USER_TASKS = frozenset({"pr_review", "local_check", "release", "audit"})
#: Reachability ladder. ``internal`` is off the ladder: moving onto or off it
#: is a reclassification, never an automatic upgrade.
_RANK = {"gap": 0, "partial": 1, "surfaced": 2}

_ADR_FILE_RE = re.compile(r"^\d{3}-[a-z0-9.-]+\.md$")
_INDEX_ROW_RE = re.compile(
    r"^\| \[(\d{3}[a-z]?)\]\(([^)]+\.md)\) \| [^|]*\| (.*?) \|?\s*$"
)
#: Words that make an otherwise "implemented" Status describe incomplete work.
_INCOMPLETE_WORDS = re.compile(
    r"\b(partial|partially|substantially|phased|deferred|not implemented|"
    r"not yet|roadmap|remain|remains|still|slice|proposed)\b",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def _yaml(path: Path) -> Any:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def adr_files(adr_dir: Path = ADR_DIR) -> list[str]:
    return sorted(p.name for p in adr_dir.iterdir() if _ADR_FILE_RE.match(p.name))


def index_statuses(index: Path = ADR_INDEX) -> dict[str, str]:
    """``{adr file name: Status column}`` from the ADR index table."""
    out: dict[str, str] = {}
    for line in index.read_text(encoding="utf-8").splitlines():
        m = _INDEX_ROW_RE.match(line)
        if m:
            out[m.group(2)] = m.group(3)
    return out


def status_class(status: str) -> str:
    """``implemented`` | ``retired`` | ``open`` for one index Status text."""
    head = status.split(".")[0]
    if re.search(r"\b(deprecated|superseded)\b", head, re.IGNORECASE):
        return "retired"
    # The qualifier search reads the whole Status: "S1, S2 and S3 implemented
    # (...); S4 not implemented" is not a fully implemented ADR.
    if re.search(
        r"\bimplemented\b", head, re.IGNORECASE
    ) and not _INCOMPLETE_WORDS.search(status):
        return "implemented"
    return "open"


def load_scenarios(scenario_dir: Path = SCENARIO_DIR) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for path in sorted(scenario_dir.glob("*.yaml")):
        data = _yaml(path) or {}
        items.extend(data.get("scenarios") or [])
    return items


# ---------------------------------------------------------------------------
# Surface resolution against the code
# ---------------------------------------------------------------------------


def _surface_kind(surface: Mapping[str, Any]) -> tuple[str, str]:
    if not isinstance(surface, Mapping) or len(surface) != 1:
        raise ValueError(f"surface must be a one-key mapping, got {surface!r}")
    ((kind, value),) = surface.items()
    if kind not in SURFACE_KINDS:
        raise ValueError(
            f"unknown surface kind {kind!r} (expected one of {SURFACE_KINDS})"
        )
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"surface {kind!r} needs a non-empty string")
    return kind, value.strip()


def surface_key(surface: Mapping[str, Any]) -> str:
    kind, value = _surface_kind(surface)
    return f"{kind}: {value}"


@dataclass
class CliTree:
    """``{command path: {option spelling: canonical param name}}``."""

    commands: dict[tuple[str, ...], dict[str, str]] = field(default_factory=dict)
    choices: dict[tuple[str, ...], dict[str, set[str]]] = field(default_factory=dict)

    @classmethod
    def from_click(cls) -> CliTree:
        import click

        from abicheck.cli import main

        tree = cls()

        def walk(cmd: click.Command, path: tuple[str, ...]) -> None:
            opts: dict[str, str] = {}
            choices: dict[str, set[str]] = {}
            for param in cmd.params:
                if not isinstance(param, click.Option):
                    continue
                for spelling in (*param.opts, *param.secondary_opts):
                    opts[spelling] = str(param.name)
                if isinstance(param.type, click.Choice):
                    choices[str(param.name)] = {str(c) for c in param.type.choices}
                # `-o FORMAT=DESTINATION` publishes its format set on the
                # parameter (frontends/cli/options/export.py), not as a Choice.
                export_formats = getattr(param, "export_formats", None)
                if export_formats:
                    choices[str(param.name)] = {str(c) for c in export_formats}
            tree.commands[path] = opts
            tree.choices[path] = choices
            if isinstance(cmd, click.Group):
                for name, sub in cmd.commands.items():
                    walk(sub, (*path, name))

        walk(main, ())
        return tree

    def split(self, tokens: list[str]) -> tuple[tuple[str, ...], list[str]]:
        """Longest command path prefix of ``tokens``, then the remaining tokens."""
        path: tuple[str, ...] = ()
        rest = list(tokens)
        while rest and (*path, rest[0]) in self.commands:
            path = (*path, rest.pop(0))
        return path, rest

    def canonical_flags(self, path: tuple[str, ...], tokens: Iterable[str]) -> set[str]:
        opts = self.commands.get(path, {})
        out: set[str] = set()
        for tok in tokens:
            if not tok.startswith("-"):
                continue
            spelling = tok.split("=", 1)[0]
            if spelling in opts:
                out.add(opts[spelling])
            elif spelling.startswith("-D") and "-D" in opts:
                out.add(opts["-D"])
        return out


def parse_cli(tree: CliTree, value: str) -> tuple[tuple[str, ...], set[str], list[str]]:
    """``(command path, canonical flags, errors)`` for a ``cli`` surface."""
    tokens = shlex.split(value)
    if tokens and tokens[0] == "abicheck":
        tokens = tokens[1:]
    path, rest = tree.split(tokens)
    errors: list[str] = []
    if not path:
        errors.append(f"cli {value!r}: no such abicheck command")
        return path, set(), errors
    opts = tree.commands[path]
    for tok in rest:
        if not tok.startswith("-"):
            errors.append(
                f"cli {value!r}: {tok!r} is not a subcommand of {' '.join(path)!r}"
            )
        elif tok.split("=", 1)[0] not in opts:
            errors.append(f"cli {value!r}: {' '.join(path)!r} has no option {tok!r}")
    return path, tree.canonical_flags(path, rest), errors


def _action_inputs(spec: str) -> tuple[Path, str | None]:
    if spec.startswith("actions/"):
        name, _, inp = spec.partition(":")
        return ROOT / name / "action.yml", (inp or None)
    return ROOT / "action.yml", spec


@dataclass
class Resolver:
    """Resolves surfaces against the code; caches the expensive lookups."""

    cli: CliTree
    _schemas: dict[str, set[str]] = field(default_factory=dict)

    def check(self, surface: Mapping[str, Any]) -> list[str]:
        try:
            kind, value = _surface_kind(surface)
        except ValueError as exc:
            return [str(exc)]
        errors: list[str] = getattr(self, f"_check_{kind}")(value)
        return errors

    def _check_cli(self, value: str) -> list[str]:
        return parse_cli(self.cli, value)[2]

    @staticmethod
    def _check_api(value: str) -> list[str]:
        parts = value.split(".")
        for cut in range(len(parts), 0, -1):
            try:
                obj: Any = importlib.import_module(".".join(parts[:cut]))
            except ImportError:
                continue
            for attr in parts[cut:]:
                if not hasattr(obj, attr):
                    return [
                        f"api {value!r}: {'.'.join(parts[:cut])} has no attribute {attr!r}"
                    ]
                obj = getattr(obj, attr)
            return []
        return [f"api {value!r}: not importable"]

    @staticmethod
    def _check_action(value: str) -> list[str]:
        path, inp = _action_inputs(value)
        if not path.is_file():
            return [f"action {value!r}: {path.relative_to(ROOT)} does not exist"]
        if inp is None:
            return []
        inputs = (_yaml(path) or {}).get("inputs") or {}
        if inp not in inputs:
            return [f"action {value!r}: {path.relative_to(ROOT)} has no input {inp!r}"]
        return []

    def _check_report(self, value: str) -> list[str]:
        if value.startswith("format:"):
            fmt = value.split(":", 1)[1]
            if fmt not in self._compare_formats():
                return [f"report {value!r}: not a `compare -o` choice"]
            return []
        schema, _, fld = value.partition(".")
        if schema not in self._schemas:
            path = SCHEMA_DIR / f"{schema}.schema.json"
            if not path.is_file():
                return [
                    f"report {value!r}: no schema abicheck/schemas/{schema}.schema.json"
                ]
            props = json.loads(path.read_text(encoding="utf-8")).get("properties") or {}
            self._schemas[schema] = set(props)
        if fld.split(".")[0] not in self._schemas[schema]:
            return [f"report {value!r}: {schema} has no top-level property {fld!r}"]
        return []

    def _compare_formats(self) -> set[str]:
        out: set[str] = set()
        for choices in self.cli.choices.get(("compare",), {}).values():
            out |= choices
        return out


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


@dataclass
class Model:
    adrs: list[dict[str, Any]]
    use_cases: list[dict[str, Any]]
    scenarios: list[dict[str, Any]]
    statuses: dict[str, str]
    files: list[str]


def load_model() -> Model:
    return Model(
        adrs=(_yaml(REGISTRY) or {}).get("adrs") or [],
        use_cases=(_yaml(UC_REGISTRY) or {}).get("use_cases") or [],
        scenarios=load_scenarios(),
        statuses=index_statuses(),
        files=adr_files(),
    )


def check_registry(model: Model, resolver: Resolver | None) -> list[str]:
    errs: list[str] = []
    uc_ids = {uc["id"] for uc in model.use_cases}
    seen_ids: set[str] = set()
    seen_files: set[str] = set()
    for entry in model.adrs:
        aid = entry.get("id", "?")
        where = f"{REGISTRY.name}: {aid}"
        if aid in seen_ids:
            errs.append(f"{where}: duplicate id")
        seen_ids.add(aid)
        fname = entry.get("file", "")
        if fname not in model.files:
            errs.append(
                f"{where}: file {fname!r} is not an ADR under docs/contribute/adr/"
            )
        seen_files.add(fname)
        disp = entry.get("disposition")
        if disp not in DISPOSITIONS:
            errs.append(f"{where}: disposition {disp!r} not one of {DISPOSITIONS}")
            continue
        if disp in ("partial", "gap") and not str(entry.get("missing", "")).strip():
            errs.append(
                f"{where}: disposition {disp} needs `missing:` (what is not reachable)"
            )
        if disp == "internal" and not str(entry.get("reason", "")).strip():
            errs.append(f"{where}: disposition internal needs `reason:`")
        surfaces = entry.get("surfaces") or []
        if disp == "surfaced" and not surfaces:
            errs.append(f"{where}: disposition surfaced needs at least one surface")
        if disp in ("internal", "gap") and surfaces:
            errs.append(f"{where}: disposition {disp} must not list surfaces")
        for uc in entry.get("use_cases") or []:
            if uc not in uc_ids:
                errs.append(f"{where}: use case {uc!r} is not in usecase-registry.yaml")
        if resolver is not None:
            for surface in surfaces:
                errs.extend(f"{where}: {e}" for e in resolver.check(surface))
        status = model.statuses.get(fname)
        if status is None:
            errs.append(f"{where}: no row for {fname} in docs/contribute/adr/index.md")
            continue
        cls = status_class(status)
        if cls == "implemented" and disp not in ("surfaced", "internal"):
            errs.append(
                f"{where}: index Status reads implemented but disposition is {disp}; "
                "surface it, record it internal, or correct the Status"
            )
        if cls == "retired" and disp != "internal":
            errs.append(
                f"{where}: retired ADR (Deprecated/Superseded) must be internal"
            )
    for missing in sorted(set(model.files) - seen_files):
        errs.append(f"{REGISTRY.name}: ADR {missing} has no entry")
    return errs


def adrs_by_use_case(model: Model) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for entry in model.adrs:
        for uc in entry.get("use_cases") or []:
            out.setdefault(uc, []).append(entry["id"])
    return out


def check_use_case_mirror(model: Model) -> list[str]:
    errs: list[str] = []
    derived = adrs_by_use_case(model)
    for uc in model.use_cases:
        uid = uc["id"]
        tasks = uc.get("user_task")
        if not isinstance(tasks, list) or not tasks or not set(tasks) <= USER_TASKS:
            errs.append(
                f"usecase-registry.yaml: {uid}: user_task must be a non-empty list from "
                f"{sorted(USER_TASKS)}"
            )
        declared = uc.get("adrs")
        if not isinstance(declared, list):
            errs.append(
                f"usecase-registry.yaml: {uid}: needs an `adrs:` list (may be empty)"
            )
            continue
        want = sorted(derived.get(uid, []))
        if sorted(declared) != want:
            errs.append(
                f"usecase-registry.yaml: {uid}: adrs {sorted(declared)} != ADRs listing it "
                f"in adr-surface-registry.yaml {want}"
            )
    return errs


def _flow_commands(scenario: Mapping[str, Any]) -> list[str]:
    out: list[str] = []
    for line in scenario.get("flow") or []:
        text = str(line).split(" #", 1)[0].strip()
        if text.startswith("abicheck "):
            out.append(text)
    return out


def check_scenarios(model: Model, cli: CliTree | None) -> list[str]:
    errs: list[str] = []
    for sc in model.scenarios:
        sid = sc.get("id", "?")
        for surface in sc.get("surfaces") or []:
            try:
                kind, value = _surface_kind(surface)
            except ValueError as exc:
                errs.append(f"scenario {sid}: {exc}")
                continue
            if kind != "cli" or cli is None:
                continue
            path, flags, perr = parse_cli(cli, value)
            if perr:
                errs.extend(f"scenario {sid}: {e}" for e in perr)
                continue
            used = False
            for cmd in _flow_commands(sc):
                try:
                    fpath, frest = cli.split(shlex.split(cmd)[1:])
                except ValueError:
                    continue
                if fpath == path and flags <= cli.canonical_flags(fpath, frest):
                    used = True
                    break
            if not used:
                errs.append(
                    f"scenario {sid}: declares surface `cli: {value}` but no `flow` "
                    "command runs that command with those flags"
                )
    return errs


# ---------------------------------------------------------------------------
# Trace computation and ratchet
# ---------------------------------------------------------------------------


def _traced(
    adr_surface: Mapping[str, Any],
    scenario_surfaces: list[Mapping[str, Any]],
    cli: CliTree | None,
) -> bool:
    kind, value = _surface_kind(adr_surface)
    for s in scenario_surfaces:
        skind, svalue = _surface_kind(s)
        if skind != kind:
            continue
        if svalue == value:
            return True
        if kind == "cli" and cli is not None:
            apath, aflags, aerr = parse_cli(cli, value)
            spath, sflags, serr = parse_cli(cli, svalue)
            if not aerr and not serr and apath == spath and aflags <= sflags:
                return True
    return False


def compute_trace(model: Model, cli: CliTree | None) -> dict[str, list[str]]:
    scenario_surfaces = [
        s for sc in model.scenarios for s in (sc.get("surfaces") or [])
    ]
    untraced: list[str] = []
    for entry in model.adrs:
        for s in entry.get("surfaces") or []:
            if not _traced(s, scenario_surfaces, cli):
                untraced.append(f"{entry['id']} {surface_key(s)}")
    by_uc = adrs_by_use_case(model)
    entries = {e["id"]: e for e in model.adrs}
    no_family: list[str] = []
    for uid, adr_ids in sorted(by_uc.items()):
        kinds = {
            _surface_kind(s)[0]
            for a in adr_ids
            for s in (entries[a].get("surfaces") or [])
        }
        if not CHANNEL_KINDS <= kinds:
            continue
        families = {
            sc.get("family") for sc in model.scenarios if sc.get("validates") == uid
        }
        if not families - {None}:
            no_family.append(uid)
    return {
        "untraced_surfaces": sorted(untraced),
        "multichannel_without_family": no_family,
    }


def snapshot(model: Model, trace: Mapping[str, list[str]]) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "adrs": {
            e["id"]: {
                "disposition": e.get("disposition"),
                "surfaces": sorted(surface_key(s) for s in e.get("surfaces") or []),
                "use_cases": sorted(e.get("use_cases") or []),
            }
            for e in model.adrs
        },
        **{k: list(v) for k, v in trace.items()},
    }


def check_ratchet(current: Mapping[str, Any], baseline: Mapping[str, Any]) -> list[str]:
    errs: list[str] = []
    hint = " (accept deliberately with --write-baseline in the same PR)"
    for aid, old in (baseline.get("adrs") or {}).items():
        new = (current.get("adrs") or {}).get(aid)
        if new is None:
            errs.append(f"ratchet: {aid} disappeared from the registry{hint}")
            continue
        od, nd = old["disposition"], new["disposition"]
        if od != nd and not (od in _RANK and nd in _RANK and _RANK[nd] > _RANK[od]):
            errs.append(
                f"ratchet: {aid} disposition {od} -> {nd} is not an upgrade{hint}"
            )
        for s in sorted(set(old["surfaces"]) - set(new["surfaces"])):
            errs.append(f"ratchet: {aid} lost surface `{s}`{hint}")
        for uc in sorted(set(old["use_cases"]) - set(new["use_cases"])):
            errs.append(f"ratchet: {aid} lost use case {uc}{hint}")
    for key, label in (
        ("untraced_surfaces", "ADR surface no scenario traces"),
        (
            "multichannel_without_family",
            "cli+api+action use case without a shared scenario family",
        ),
    ):
        for item in sorted(set(current.get(key) or []) - set(baseline.get(key) or [])):
            errs.append(f"ratchet: new {label}: {item}{hint}")
    return errs


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


def render_report(model: Model, trace: Mapping[str, list[str]]) -> str:
    counts = {d: 0 for d in DISPOSITIONS}
    for e in model.adrs:
        counts[e["disposition"]] = counts.get(e["disposition"], 0) + 1
    lines = [
        "# ADR surface coverage",
        "",
        "<!-- generated by scripts/check_adr_surfaces.py --write-report; do not hand-edit -->",
        "",
        "Which public surface reaches each ADR, which use cases it serves, and "
        "whether a user scenario traces it. Contract: "
        "[ADR-076](../adr/076-adr-use-case-surface-traceability.md); source: "
        "`docs/contribute/adr/adr-surface-registry.yaml`.",
        "",
        "| Disposition | ADRs |",
        "|---|---|",
        *(f"| {d} | {counts[d]} |" for d in DISPOSITIONS),
        "",
        "## ADRs",
        "",
        "| ADR | Disposition | Use cases | Surfaces | Missing / reason |",
        "|---|---|---|---|---|",
    ]
    for e in model.adrs:
        surfaces = (
            "<br>".join(f"`{surface_key(s)}`" for s in e.get("surfaces") or []) or "—"
        )
        ucs = ", ".join(e.get("use_cases") or []) or "—"
        note = " ".join(str(e.get("missing") or e.get("reason") or "—").split())
        lines.append(
            f"| [{e['id']}](../adr/{e['file']}) | {e['disposition']} | {ucs} | {surfaces} | {note} |"
        )
    uc_tasks = {uc["id"]: uc.get("user_task") or [] for uc in model.use_cases}
    lines += [
        "",
        "## Use cases by user task",
        "",
        "| User task | Use cases |",
        "|---|---|",
    ]
    for task in sorted(USER_TASKS):
        ids = sorted(u for u, t in uc_tasks.items() if task in t)
        lines.append(f"| {task} | {len(ids)} |")
    lines += ["", "## ADR surfaces no scenario traces", ""]
    lines += [f"- {item}" for item in trace["untraced_surfaces"]] or ["- none"]
    lines += ["", "## cli+api+action use cases without a shared scenario family", ""]
    lines += [f"- {item}" for item in trace["multichannel_without_family"]] or [
        "- none"
    ]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def run(write_report: bool = False, write_baseline: bool = False) -> list[str]:
    model = load_model()
    cli = CliTree.from_click()
    resolver = Resolver(cli)
    errs = check_registry(model, resolver)
    errs += check_use_case_mirror(model)
    errs += check_scenarios(model, cli)
    if errs:
        return errs
    trace = compute_trace(model, cli)
    current = snapshot(model, trace)
    if write_baseline:
        BASELINE.write_text(
            json.dumps(current, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    elif not BASELINE.is_file():
        errs.append(
            f"{BASELINE.relative_to(ROOT)} missing; create it with --write-baseline"
        )
    else:
        errs += check_ratchet(current, json.loads(BASELINE.read_text(encoding="utf-8")))
    report = render_report(model, trace)
    if write_report or write_baseline:
        REPORT.parent.mkdir(parents=True, exist_ok=True)
        REPORT.write_text(report, encoding="utf-8")
    elif not REPORT.is_file() or REPORT.read_text(encoding="utf-8") != report:
        errs.append(
            f"{REPORT.relative_to(ROOT)} is stale; regenerate with "
            "`python scripts/check_adr_surfaces.py --write-report`"
        )
    return errs


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--write-report", action="store_true", help="regenerate the coverage report"
    )
    parser.add_argument(
        "--write-baseline",
        action="store_true",
        help="accept the current state as the ratchet baseline",
    )
    args = parser.parse_args(argv)
    errs = run(write_report=args.write_report, write_baseline=args.write_baseline)
    if errs:
        print("adr-surfaces: FAILED\n")
        for e in errs:
            print(f"  - {e}")
        return 1
    print("adr-surfaces: OK — ADR registry, use cases, surfaces and scenarios agree")
    return 0


if __name__ == "__main__":
    sys.exit(main())
