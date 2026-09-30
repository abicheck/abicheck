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

"""Deterministic grader for the `set-up-abi-compatibility-ci` evaluation.

Scores the GitHub Actions workflows an agent left in a workspace, plus its
final message, against one scenario's checks. No model is called: every check
is a pure function of the files and the text, so two graders can never
disagree about a run.

The checks encode the failure modes the skill exists to prevent (see
`skills-src/set-up-abi-compatibility-ci/references/pitfalls.md`): a workflow
that cannot fail, compares a build with itself, parses private headers, gates
on a baseline that does not exist, or runs PR code with a write token.

Usage::

    python grader.py --workspace <dir> --scenario <id> [--final final.md]
"""

from __future__ import annotations

import argparse
import json
import re
import shlex
import sys
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

HERE = Path(__file__).resolve().parent
SCENARIOS = HERE / "scenarios.yaml"

ABICHECK_USES = re.compile(r"^abicheck/abicheck@(?P<ref>\S+)$")
_SHA = re.compile(r"^[0-9a-f]{40}$")
_EXACT_TAG = re.compile(r"^v\d+\.\d+\.\d+$")
RETIRED_INPUTS = ("against", "audit", "estimate", "crosscheck", "risk-rules")
HEADER_INPUTS = ("header", "new-header", "old-header", "public-header-dir")
LIBRARY_INPUTS = ("old-library", "new-library", "abi-baseline")


# --------------------------------------------------------------------------
# Workspace model
# --------------------------------------------------------------------------


@dataclass
class Step:
    raw: dict[str, Any]
    job: Job

    @property
    def uses(self) -> str:
        return str(self.raw.get("uses") or "")

    @property
    def inputs(self) -> dict[str, Any]:
        value = self.raw.get("with") or {}
        return value if isinstance(value, dict) else {}

    @property
    def is_cli(self) -> bool:
        """A synthetic step standing for a direct `abicheck` CLI call found in
        a `run:` block (see `cli_steps`), rather than the composite Action."""
        return bool(self.raw.get("_cli"))

    @property
    def is_abicheck(self) -> bool:
        return self.is_cli or bool(ABICHECK_USES.match(self.uses.strip()))

    @property
    def mode(self) -> str:
        return str(self.inputs.get("mode", "compare")).strip()

    def expand(self, value: object) -> list[str]:
        """`value` with any `${{ matrix.X }}` reference expanded over the
        job's matrix, so a matrix-driven step is judged by what it runs."""
        text = "" if value is None else str(value)
        refs = re.findall(r"\$\{\{\s*matrix\.([A-Za-z0-9_-]+)\s*\}\}", text)
        if not refs:
            return [text]
        out: list[str] = []
        for row in self.job.matrix_rows():
            expanded = text
            for ref in refs:
                if ref in row:
                    expanded = re.sub(
                        r"\$\{\{\s*matrix\." + re.escape(ref) + r"\s*\}\}",
                        str(row[ref]),
                        expanded,
                    )
            out.append(expanded)
        return out or [text]


@dataclass
class Job:
    name: str
    raw: dict[str, Any]
    workflow: Workflow
    steps: list[Step] = field(default_factory=list)

    def matrix_rows(self) -> list[dict[str, Any]]:
        strategy = self.raw.get("strategy") or {}
        matrix = strategy.get("matrix") if isinstance(strategy, dict) else None
        if not isinstance(matrix, dict):
            return []
        rows: list[dict[str, Any]] = [
            r for r in matrix.get("include") or [] if isinstance(r, dict)
        ]
        axes = {
            k: v for k, v in matrix.items() if k not in ("include", "exclude") and isinstance(v, list)
        }
        if axes and not rows:
            rows = [{}]
            for key, values in axes.items():
                rows = [{**r, key: v} for r in rows for v in values]
        return rows


@dataclass
class Workflow:
    path: Path
    text: str
    data: dict[str, Any] | None
    error: str | None = None
    jobs: list[Job] = field(default_factory=list)

    @property
    def triggers(self) -> dict[str, Any]:
        if not self.data:
            return {}
        # YAML 1.1 reads a bare `on:` key as boolean True.
        on = self.data.get("on", self.data.get(True))
        if isinstance(on, str):
            return {on: None}
        if isinstance(on, list):
            return {str(t): None for t in on}
        return dict(on) if isinstance(on, dict) else {}

    @property
    def uses_abicheck(self) -> bool:
        return any(s.is_abicheck for j in self.jobs for s in j.steps)

    def abicheck_steps(self) -> Iterator[Step]:
        for job in self.jobs:
            for step in job.steps:
                if step.is_abicheck:
                    yield step


_CLI_CALL = re.compile(r"(?:^|[\s;&|(])abicheck\s+(compare|dump)\b")
_SIDE = re.compile(r"^(old|new)=")


def _parse_cli(mode: str, argv: list[str]) -> dict[str, Any]:
    """Map one `abicheck compare|dump` argv onto the Action's input names,
    so every check judges a CLI call and an Action step by the same rule."""
    inputs: dict[str, Any] = {"mode": mode}
    positional: list[str] = []
    headers: dict[str, list[str]] = {"header": [], "old-header": [], "new-header": []}
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg in ("&&", "||", ";", "|") or arg.startswith(("#", ">")):
            break
        value = None
        if "=" in arg and arg.startswith("--"):
            arg, value = arg.split("=", 1)
        if arg in ("-H", "--header", "-o", "--output", "--config", "-I", "--include", "--version",
                   "--policy", "--suppress", "--severity-preset", "--depth", "--contract",
                   "--used-by", "--sources", "--build-info", "--debug-info"):
            if value is None:
                i += 1
                value = argv[i] if i < len(argv) else ""
            if arg in ("-H", "--header"):
                side = _SIDE.match(value)
                key = f"{side.group(1)}-header" if side else "header"
                headers[key].append(_SIDE.sub("", value))
            elif arg in ("-o", "--output"):
                inputs["output-file"] = value.split("=", 1)[-1] if re.match(r"^[a-z]+=", value) else value
        elif not arg.startswith("-"):
            positional.append(arg)
        i += 1
    for key, vals in headers.items():
        if vals:
            inputs[key] = " ".join(vals)
    if mode == "compare":
        if len(positional) >= 2:
            inputs["old-library"], inputs["new-library"] = positional[0], positional[1]
        elif positional:
            inputs["new-library"] = positional[0]
    elif positional:
        inputs["new-library"] = positional[0]
    return inputs


def cli_steps(raw: dict[str, Any], job: Job) -> list[Step]:
    """Synthetic steps for every `abicheck compare|dump` call in a `run:`."""
    run = raw.get("run")
    if not isinstance(run, str):
        return []
    text = run.replace("\\\n", " ")
    out = []
    for match in _CLI_CALL.finditer(text):
        line_end = text.find("\n", match.end())
        rest = text[match.end(): line_end if line_end != -1 else None]
        try:
            argv = shlex.split(rest, comments=True)
        except ValueError:
            argv = rest.split()
        inputs = _parse_cli(match.group(1), argv)
        out.append(Step({"_cli": True, "uses": "", "with": inputs, "run": run}, job))
    return out


_PIP_PIN = re.compile(r"(pip3?|pipx|uv pip|uv tool)\s+install[^\n]*?\babicheck(?P<spec>[=<>~!]=?[\w.]+)?")


def cli_pin(text: str) -> str | None:
    """`==X.Y.Z` when the workflow installs an exact abicheck, `""` when it
    installs an unpinned one, `None` when it installs none this way."""
    found = None
    for m in _PIP_PIN.finditer(text):
        spec = m.group("spec") or ""
        if spec.startswith("==") and re.match(r"^==\d+\.\d+\.\d+$", spec):
            found = found if found == "" else spec
        else:
            found = ""
    return found


def load_workflows(workspace: Path) -> list[Workflow]:
    root = workspace / ".github" / "workflows"
    out: list[Workflow] = []
    if not root.is_dir():
        return out
    for path in sorted([*root.glob("*.yml"), *root.glob("*.yaml")]):
        text = path.read_text(encoding="utf-8", errors="replace")
        try:
            data = yaml.safe_load(text)
        except yaml.YAMLError as exc:
            out.append(Workflow(path, text, None, error=str(exc)))
            continue
        if not isinstance(data, dict):
            out.append(Workflow(path, text, None, error="not a mapping"))
            continue
        wf = Workflow(path, text, data)
        for name, job in (data.get("jobs") or {}).items():
            if not isinstance(job, dict):
                continue
            j = Job(str(name), job, wf)
            j.steps = []
            for raw in job.get("steps") or []:
                if not isinstance(raw, dict):
                    continue
                j.steps.append(Step(raw, j))
                j.steps.extend(cli_steps(raw, j))
            wf.jobs.append(j)
        out.append(wf)
    return out


@dataclass
class Context:
    workspace: Path
    workflows: list[Workflow]
    final: str
    params: dict[str, Any]

    @property
    def abicheck_workflows(self) -> list[Workflow]:
        return [w for w in self.workflows if w.uses_abicheck]

    def steps(self) -> list[Step]:
        return [s for w in self.abicheck_workflows for s in w.abicheck_steps()]

    def analysis_steps(self) -> list[Step]:
        return [s for s in self.steps() if s.mode in ("compare", "dump")]

    def compare_steps(self) -> list[Step]:
        return [s for s in self.steps() if s.mode == "compare"]

    def pr_compare_steps(self) -> list[Step]:
        return [
            s
            for s in self.compare_steps()
            if "pull_request" in s.job.workflow.triggers
        ]


@dataclass
class Result:
    check: str
    passed: bool
    detail: str


Check = Callable[[Context], tuple[bool, str]]
CHECKS: dict[str, Check] = {}


def check(name: str) -> Callable[[Check], Check]:
    def register(fn: Check) -> Check:
        CHECKS[name] = fn
        return fn

    return register


# --------------------------------------------------------------------------
# Common checks
# --------------------------------------------------------------------------


@check("yaml_valid")
def _yaml_valid(ctx: Context) -> tuple[bool, str]:
    bad = [f"{w.path.name}: {w.error}" for w in ctx.workflows if w.error]
    return (not bad, "; ".join(bad) or "all workflows parse")


@check("abicheck_workflow_on_pr")
def _on_pr(ctx: Context) -> tuple[bool, str]:
    if not ctx.pr_compare_steps():
        return False, "no abicheck compare step in a pull_request-triggered workflow"
    return True, f"{len(ctx.pr_compare_steps())} PR compare step(s)"


@check("pinned_abicheck")
def _pinned(ctx: Context) -> tuple[bool, str]:
    steps = ctx.steps()
    if not steps:
        return False, "no abicheck step"
    loose = []
    for s in steps:
        if s.is_cli:
            pin = cli_pin(s.job.workflow.text)
            if not pin:
                loose.append("unpinned abicheck install" if pin == "" else "abicheck never installed")
            continue
        ref = ABICHECK_USES.match(s.uses.strip()).group("ref")  # type: ignore[union-attr]
        if not (_SHA.match(ref) or _EXACT_TAG.match(ref)):
            loose.append(ref)
    return (not loose, f"unpinned refs: {loose}" if loose else "all pinned")


@check("pins_are_commits")
def _pins_are_commits(ctx: Context) -> tuple[bool, str]:
    tag_objects = ctx.params.get("tag_object_shas") or {}
    bad = sorted(
        {m for w in ctx.workflows for m in re.findall(r"uses:\s*\S+@([0-9a-f]{40})", w.text) if m in tag_objects}
    )
    return (not bad, f"pinned to annotated-tag objects, not commits: {[tag_objects[b] for b in bad]}" if bad else "ok")


@check("no_pull_request_target")
def _no_prt(ctx: Context) -> tuple[bool, str]:
    bad = [w.path.name for w in ctx.workflows if "pull_request_target" in w.triggers]
    return (not bad, f"pull_request_target in {bad}" if bad else "none")


def _permissions_of(block: object) -> dict[str, str] | str | None:
    if block is None:
        return None
    if isinstance(block, str):
        return block
    if isinstance(block, dict):
        return {str(k): str(v) for k, v in block.items()}
    return None


@check("permissions_declared")
def _perms(ctx: Context) -> tuple[bool, str]:
    problems = []
    for w in ctx.abicheck_workflows:
        top = _permissions_of((w.data or {}).get("permissions"))
        for job in w.jobs:
            own = _permissions_of(job.raw.get("permissions"))
            effective = own if own is not None else top
            if effective is None:
                problems.append(f"{w.path.name}:{job.name} inherits repository default")
            elif effective in ("write-all",):
                problems.append(f"{w.path.name}:{job.name} write-all")
    return (not problems, "; ".join(problems) or "declared everywhere")


@check("contents_write_not_on_pr")
def _contents_write(ctx: Context) -> tuple[bool, str]:
    problems = []
    for w in ctx.abicheck_workflows:
        if "pull_request" not in w.triggers:
            continue
        top = _permissions_of((w.data or {}).get("permissions"))
        for job in w.jobs:
            own = _permissions_of(job.raw.get("permissions"))
            eff = own if own is not None else top
            if isinstance(eff, dict) and eff.get("contents") == "write":
                problems.append(f"{w.path.name}:{job.name}")
    return (not problems, f"contents: write on PR jobs {problems}" if problems else "ok")


@check("no_retired_inputs")
def _retired(ctx: Context) -> tuple[bool, str]:
    bad = []
    for s in ctx.steps():
        if s.mode == "scan":
            bad.append("mode: scan")
        bad.extend(k for k in RETIRED_INPUTS if k in s.inputs)
    return (not bad, f"retired inputs: {bad}" if bad else "none")


@check("no_continue_on_error")
def _coe(ctx: Context) -> tuple[bool, str]:
    bad = [
        f"{s.job.workflow.path.name}:{s.job.name}"
        for s in ctx.steps()
        if str(s.raw.get("continue-on-error", "false")).lower() == "true"
        or str(s.job.raw.get("continue-on-error", "false")).lower() == "true"
    ]
    return (not bad, f"continue-on-error on {bad}" if bad else "none")


@check("not_self_compare")
def _self(ctx: Context) -> tuple[bool, str]:
    bad = []
    for s in ctx.compare_steps():
        old = str(s.inputs.get("old-library", "")).strip()
        new = str(s.inputs.get("new-library", "")).strip()
        if old and old == new:
            bad.append(old)
    return (not bad, f"old == new: {bad}" if bad else "ok")


@check("debug_info")
def _debug(ctx: Context) -> tuple[bool, str]:
    text = "\n".join({s.job.workflow.text for s in ctx.pr_compare_steps()})
    ok = bool(
        re.search(r"RelWithDebInfo|debugoptimized|buildtype=debug|CMAKE_BUILD_TYPE=Debug|(?<![\w-])-g(?![\w-])|-ggdb|-c dbg|--compilation_mode[= ]dbg", text)
    )
    if not ok and re.search(r"\./configure\b", text) and "CFLAGS=" not in text:
        # autoconf's default CFLAGS are "-g -O2".
        ok = True
    return ok, "debug info requested" if ok else "no debug-info build flags in the check build"


# --------------------------------------------------------------------------
# Scenario checks
# --------------------------------------------------------------------------


def _repo_config(ctx: Context) -> str:
    path = ctx.workspace / ".abicheck.yml"
    return path.read_text(encoding="utf-8") if path.is_file() else ""


@check("lang")
def _lang(ctx: Context) -> tuple[bool, str]:
    expect = ctx.params.get("expect", "c++")
    steps = ctx.analysis_steps()
    if not steps:
        return False, "no compare/dump step"
    wrong = []
    for s in steps:
        if s.is_cli:
            # The CLI has no --lang; C is selected by `compile: lang: c` in a
            # config the workflow writes or ships.
            got = "c" if re.search(r"lang:\s*['\"]?c['\"]?\s*($|\\n|\n)", s.job.workflow.text + _repo_config(ctx)) else "c++"
            if got != expect:
                wrong.append(got)
            continue
        for value in s.expand(s.inputs.get("lang", "c++")):
            got = value.strip() or "c++"
            if got != expect:
                wrong.append(got)
    return (not wrong, f"lang {wrong} (expected {expect})" if wrong else f"lang {expect}")


@check("headers_public")
def _headers(ctx: Context) -> tuple[bool, str]:
    values: list[str] = []
    headerless = []
    for s in ctx.analysis_steps():
        own = [v for key in HEADER_INPUTS if key in s.inputs for v in s.expand(s.inputs[key])]
        if not own:
            headerless.append(f"{s.job.workflow.path.name}:{s.job.name}")
        values.extend(own)
    if not values or headerless:
        return False, f"no header input (binary-only evidence) in {headerless or 'any step'}"
    bad = []
    for value in values:
        for part in value.split():
            norm = part.strip().rstrip("/")
            tail = re.sub(r"^(old|new)/", "", norm)
            if tail in ("", ".", "./", "${{ github.workspace }}") or re.search(r"(^|/)src(/|$)", tail):
                bad.append(part)
            elif "include" not in tail:
                bad.append(part)
    return (not bad, f"non-public header inputs: {bad}" if bad else f"headers {sorted(set(values))}")


@check("covers_libs")
def _libs(ctx: Context) -> tuple[bool, str]:
    wanted = ctx.params.get("libs", [])
    targets: list[str] = []
    for s in ctx.compare_steps():
        for key in LIBRARY_INPUTS:
            if key in s.inputs:
                targets.extend(s.expand(s.inputs[key]))
        if s.is_cli:
            # A shell loop (`for lib in alpha beta`) names libraries outside
            # the argv itself.
            targets.append(str(s.raw.get("run", "")))
    blob = " ".join(targets)
    missing = [lib for lib in wanted if not re.search(rf"lib{re.escape(lib)}\b|\b{re.escape(lib)}\b", blob)]
    return (not missing, f"libraries not compared: {missing}" if missing else f"compares {wanted}")


def _dump_publishers(ctx: Context) -> list[Step]:
    out = []
    for s in ctx.steps():
        if s.mode != "dump":
            continue
        trig = s.job.workflow.triggers
        tagged = "release" in trig or (
            isinstance(trig.get("push"), dict) and "tags" in (trig.get("push") or {})
        )
        names = [n for v in s.expand(s.inputs.get("output-file", "")) for n in [v] if n]
        if tagged and names and all(n.endswith((".abicheck.json", ".abicheck.json.gz", ".abicheck.json.zst")) for n in names):
            out.append(s)
    return out


_RUN_DUMP_OUTPUT = re.compile(r"\babicheck\s+dump\b[^\n]*?(?:\\\n[^\n]*?)*?(?:-o|--output)[ =]\"?([^\s\"]+)")


def _run_dump_publishers(ctx: Context) -> list[Job]:
    """Jobs in a release-triggered workflow whose `run:` step calls
    `abicheck dump -o <name>.abicheck.json` directly instead of the Action."""
    out = []
    for w in ctx.workflows:
        trig = w.triggers
        if not ("release" in trig or (isinstance(trig.get("push"), dict) and "tags" in (trig.get("push") or {}))):
            continue
        for job in w.jobs:
            for step in job.steps:
                for name in _RUN_DUMP_OUTPUT.findall(str(step.raw.get("run") or "")):
                    if ".abicheck.json" in name:
                        out.append(job)
    return out


@check("toolchain_via_action")
def _toolchain(ctx: Context) -> tuple[bool, str]:
    text = "\n".join(w.text for w in ctx.workflows)
    if re.search(r"apt(-get)?\s+install[^\n]*\bcastxml\b", text):
        return False, "installs distribution castxml, commonly below abicheck's supported range"
    cli_with_headers = [s for s in ctx.analysis_steps() if s.is_cli and any(k in s.inputs for k in HEADER_INPUTS)]
    if cli_with_headers and not re.search(
        r"(conda|mamba|micromamba|pixi)[^\n]*castxml|pip3?\s+install[^\n]*\bcastxml|install-castxml|ast-frontend[ =]clang|ABICHECK_AST_FRONTEND|ABICHECK_ALLOW_AST_FALLBACK",
        text,
    ):
        return False, "CLI header analysis with no CastXML provisioned (the default frontend fails closed)"
    return True, "header-AST toolchain provisioned"


def _tag_rebuild(ctx: Context) -> bool:
    """The PR compare job itself checks out a release tag (resolved from git)
    and builds it as the old side: a valid release baseline with no
    published snapshot, traded for a second build per run."""
    for s in ctx.pr_compare_steps():
        text = s.job.workflow.text
        if re.search(r"git (tag --list|tag -l|describe --tags)|latest.*tag|--sort=-v:refname", text) and re.search(
            r"git worktree add|git checkout|actions/checkout", text
        ) and "old-library" in s.inputs and not str(s.inputs["old-library"]).endswith(".json"):
            return True
    return False


@check("baseline_release")
def _baseline_release(ctx: Context) -> tuple[bool, str]:
    if _tag_rebuild(ctx):
        return True, "PR job rebuilds the latest release tag and compares against it"
    jobs = [s.job for s in _dump_publishers(ctx)] + _run_dump_publishers(ctx)
    if not jobs:
        return False, "no release-triggered dump writing a *.abicheck.json asset"
    uploads = any(
        re.search(r"gh release upload|action-gh-release|upload-release-asset", j.workflow.text)
        for j in jobs
    )
    if not uploads:
        return False, "snapshot dumped on release but never attached to it"
    consumers = []
    for s in ctx.pr_compare_steps():
        if "abi-baseline" in s.inputs or re.search(
            r"gh release download[^\n]*abicheck\.json", s.job.workflow.text
        ):
            consumers.append(s)
    if not consumers:
        return False, "PR check does not consume the release snapshot"
    return True, "release dump -> asset -> PR compare"


@check("release_bootstrap")
def _bootstrap(ctx: Context) -> tuple[bool, str]:
    if _tag_rebuild(ctx):
        return True, "tag rebuild needs no published snapshot"
    jobs = [s.job for s in _dump_publishers(ctx)] + _run_dump_publishers(ctx)
    if any("workflow_dispatch" in j.workflow.triggers for j in jobs):
        return True, "workflow_dispatch backfill"
    if re.search(r"bootstrap|backfill|first run|existing release|re-?run .*release", ctx.final, re.I):
        return True, "bootstrap step stated in the report"
    return False, "first PR run has no snapshot to fetch and nothing says so"


@check("baseline_without_releases")
def _no_release_baseline(ctx: Context) -> tuple[bool, str]:
    steps = ctx.pr_compare_steps()
    if not steps:
        return False, "no PR compare step"
    for s in steps:
        if "latest-release" in str(s.inputs.get("abi-baseline", "")):
            return False, "abi-baseline: latest-release, but the repository has no releases"
    text = "\n".join(s.job.workflow.text for s in steps)
    if re.search(r"base\.sha|base\.ref|base_ref|merge-base|merge_base", text):
        return True, "merge-base build"
    for s in steps:
        old = str(s.inputs.get("old-library", ""))
        candidate = ctx.workspace / old
        if old and candidate.is_file():
            try:
                data = json.loads(candidate.read_text(encoding="utf-8"))
            except ValueError:
                continue
            if isinstance(data, dict) and len(data) > 3:
                return True, f"committed snapshot {old}"
    if any(
        re.search(r"actions/cache|download-artifact|dawidd6/action-download-artifact", s.job.workflow.text)
        and "push" in s.job.workflow.triggers
        for s in steps
    ):
        return True, "accepted-main snapshot carried between runs"
    return False, "no resolvable baseline for a repository without releases"


@check("no_ambiguous_latest_release")
def _ambiguous(ctx: Context) -> tuple[bool, str]:
    if ctx.params.get("libs", 1) < 2:
        return True, "single library"
    for s in ctx.compare_steps():
        if "latest-release" in str(s.inputs.get("abi-baseline", "")):
            return False, "abi-baseline: latest-release with several *.abicheck.json assets is ambiguous"
    return True, "per-library baselines"


@check("single_abi_workflow")
def _single(ctx: Context) -> tuple[bool, str]:
    files = [
        w.path.name
        for w in ctx.abicheck_workflows
        if "pull_request" in w.triggers and any(s.mode == "compare" for s in w.abicheck_steps())
    ]
    return (len(files) == 1, f"PR ABI workflows: {files}")


@check("baseline_meaningful")
def _meaningful(ctx: Context) -> tuple[bool, str]:
    placeholder = ctx.params.get("placeholder", "")
    for s in ctx.pr_compare_steps():
        old = str(s.inputs.get("old-library", "")).strip()
        if placeholder and old.endswith(placeholder):
            path = ctx.workspace / placeholder
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                data = {}
            if not isinstance(data, dict) or len(data) <= 3:
                return False, f"still compares against placeholder {placeholder}"
        if not old and "abi-baseline" not in s.inputs:
            return False, "PR compare with no baseline at all (audit-only)"
    return True, "baseline resolves to a real snapshot or build"


@check("shared_build")
def _shared(ctx: Context) -> tuple[bool, str]:
    text = "\n".join(w.text for w in ctx.abicheck_workflows)
    ok = bool(re.search(r"BUILD_SHARED_LIBS[=:]\s*(ON|1|TRUE|YES)", text, re.I))
    return ok, "shared build requested" if ok else "library defaults to static; check build never enables BUILD_SHARED_LIBS"


@check("report_mentions")
def _mentions(ctx: Context) -> tuple[bool, str]:
    missing = [p for p in ctx.params.get("patterns", []) if not re.search(p, ctx.final, re.I)]
    return (not missing, f"final report never mentions {missing}" if missing else "mentioned")


# --------------------------------------------------------------------------
# Scoring
# --------------------------------------------------------------------------


# --------------------------------------------------------------------------
# Depth, toolchain, floors, build-system checks
# --------------------------------------------------------------------------

_CONFIG_DISCOVERY = (".abicheck.yml", ".github/.abicheck.yml", ".github/abicheck/.abicheck.yml")


def _configs(ctx: Context) -> list[tuple[str, dict[str, Any]]]:
    """Every `.abicheck.yml` the checks can actually reach: the discovery
    locations plus any file a step names via `build-config` / `--config`."""
    names = set(_CONFIG_DISCOVERY)
    for s in ctx.analysis_steps():
        if "build-config" in s.inputs:
            names.update(s.expand(s.inputs["build-config"]))
        if s.is_cli:
            names.update(re.findall(r"--config[ =]\"?([^\s\"]+)", str(s.raw.get("run", ""))))
    out = []
    for name in sorted(n for n in names if n and "${{" not in n):
        path = ctx.workspace / name.removeprefix("./")
        if path.is_file():
            try:
                data = yaml.safe_load(path.read_text(encoding="utf-8"))
            except yaml.YAMLError:
                continue
            if isinstance(data, dict):
                out.append((name, data))
    return out


def _version(text: object) -> tuple[int, ...] | None:
    m = re.match(r"^\s*(\d+(?:\.\d+)*)\s*$", str(text))
    return tuple(int(p) for p in m.group(1).split(".")) if m else None


@check("runtime_floors_declared")
def _floors(ctx: Context) -> tuple[bool, str]:
    wanted: dict[str, str] = ctx.params.get("floors", {})
    for name, data in _configs(ctx):
        floors = ((data.get("deployment") or {}).get("runtime_floors") or {}) if isinstance(data.get("deployment"), dict) else {}
        bad = []
        for prefix, promise in wanted.items():
            got = _version(floors.get(prefix))
            # At or below the promise is safe; above it lets a real
            # de-support through.
            if got is None or got > _version(promise):  # type: ignore[operator]
                bad.append(f"{prefix}={floors.get(prefix)!r}")
        if not bad:
            return True, f"{name} declares {floors}"
    return False, f"no reachable .abicheck.yml declares deployment.runtime_floors {wanted}"


@check("runner_pinned")
def _runner_pinned(ctx: Context) -> tuple[bool, str]:
    loose = sorted(
        {
            str(s.job.raw.get("runs-on"))
            for s in ctx.analysis_steps()
            if re.search(r"-latest\b", str(s.job.raw.get("runs-on", "")))
        }
    )
    return (not loose, f"floating runner images {loose}: every image bump moves the runtime floor" if loose else "pinned")


def _cli_flag(step: Step, flag: str) -> str | None:
    m = re.search(rf"{flag}[ =]\"?([^\s\"]+)", str(step.raw.get("run", "")))
    return m.group(1) if m else None


def _depth(step: Step) -> str:
    if step.is_cli:
        return _cli_flag(step, "--depth") or "headers"
    return str(step.inputs.get("depth", "headers")).strip()


@check("source_depth_complete")
def _source_depth(ctx: Context) -> tuple[bool, str]:
    """L4 is asked for on the PR, with clang and a build-evidence input."""
    steps = [s for s in ctx.pr_compare_steps() if _depth(s) == "source"]
    if not steps:
        return False, "no PR compare at depth: source (inline/template/macro changes stay invisible)"
    problems = []
    for s in steps:
        text = s.job.workflow.text
        if s.is_cli:
            has_clang = bool(re.search(r"\bclang\b", text))
            has_build = bool(_cli_flag(s, "--sources") or _cli_flag(s, "--build-info"))
        else:
            has_clang = str(s.inputs.get("dependency-source", "")).strip() in ("conda-forge-clang20", "system")
            has_build = any(k in s.inputs for k in ("sources", "build-info", "compile-db"))
        if not has_clang:
            problems.append("no clang (default dependency-source has none)")
        if not has_build:
            problems.append("no sources/build-info/compile-db")
    return (not problems, "; ".join(sorted(set(problems))) or "depth source with clang and build evidence")


@check("baseline_has_source_evidence")
def _baseline_source(ctx: Context) -> tuple[bool, str]:
    """The old side of an L4 comparison must carry L4 evidence itself: the
    new-side-only `sources` input cannot supply it."""
    for s in ctx.steps():
        if s.mode != "dump" or _depth(s) != "source":
            continue
        if s.is_cli:
            if _cli_flag(s, "--sources") or _cli_flag(s, "--build-info"):
                return True, "baseline dumped with source evidence (CLI)"
        elif any(k in s.inputs for k in ("sources", "build-info", "compile-db")):
            return True, "baseline dumped with source evidence"
    for s in ctx.pr_compare_steps():
        # The CLI (unlike the Action) takes side-scoped build evidence.
        if s.is_cli and re.search(r"--(sources|build-info)[ =]\"?old=", str(s.raw.get("run", ""))):
            return True, "old side gets source evidence via --sources old= (CLI)"
    if re.search(r"(old|baseline)[^.\n]{0,80}(no|without|lacks?|only the new)[^.\n]{0,40}(source|L4)", ctx.final, re.I):
        return True, "one-sided L4 stated in the report"
    return False, "old side has no L4 evidence and the report does not say so"


@check("cross_toolchain")
def _cross(ctx: Context) -> tuple[bool, str]:
    bad = []
    for s in ctx.analysis_steps():
        if re.search(r"arm", str(s.job.raw.get("runs-on", ""))):
            continue
        if s.is_cli:
            ok = bool(re.search(r"aarch64-linux-gnu", s.job.workflow.text + _repo_config(ctx)))
        else:
            ok = any(k in s.inputs for k in ("gcc-prefix", "gcc-path")) or "aarch64" in str(s.inputs.get("build-config", ""))
            if not ok:
                ok = any(re.search(r"aarch64-linux-gnu", str(d.get("compile") or "")) for _, d in _configs(ctx))
        if not ok:
            bad.append(f"{s.job.workflow.path.name}:{s.job.name}")
    return (not bad, f"aarch64 headers parsed with the host compiler in {bad}" if bad else "target toolchain or arm runner")


@check("library_path_plausible")
def _lib_path(ctx: Context) -> tuple[bool, str]:
    """The workflow references where this build system actually puts the
    shared library (e.g. `.libs/` for libtool, `bazel-bin/` for Bazel)."""
    hint = ctx.params.get("hint", "")
    text = "\n".join({s.job.workflow.text for s in ctx.pr_compare_steps()})
    ok = bool(re.search(hint, text))
    return ok, f"references {hint!r}" if ok else f"never references where the build writes the library ({hint!r})"


def load_scenarios(path: Path = SCENARIOS) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def grade(workspace: Path, scenario_id: str, final: str = "", corpus: dict[str, Any] | None = None) -> dict[str, Any]:
    corpus = corpus or load_scenarios()
    scenario = next(s for s in corpus["scenarios"] if s["id"] == scenario_id)
    common_params = {"tag_object_shas": corpus.get("tag_object_shas") or {}}
    plan: list[tuple[str, dict[str, Any]]] = [(c, common_params) for c in corpus["common_checks"]]
    plan += [(name, params or {}) for name, params in (scenario.get("checks") or {}).items()]
    workflows = load_workflows(workspace)
    results: list[Result] = []
    for name, params in plan:
        ctx = Context(workspace, workflows, final, params)
        passed, detail = CHECKS[name](ctx)
        results.append(Result(name, passed, detail))
    critical = set(corpus["critical"])
    failed_critical = [r.check for r in results if not r.passed and r.check in critical]
    return {
        "scenario": scenario_id,
        "score": sum(r.passed for r in results) / len(results),
        "passed": sum(r.passed for r in results),
        "total": len(results),
        "critical_failures": failed_critical,
        "success": not failed_critical,
        "checks": [r.__dict__ for r in results],
        "workflow_files": [w.path.name for w in workflows],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--scenario", required=True)
    parser.add_argument("--final", type=Path)
    args = parser.parse_args(argv)
    final = args.final.read_text(encoding="utf-8") if args.final else ""
    print(json.dumps(grade(args.workspace, args.scenario, final), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
