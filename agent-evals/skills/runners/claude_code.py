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

"""Headless Claude Code runner — one recorded run per (scenario, arm, repetition).

The two arms differ in exactly one thing, which is the whole point of running
them: the `skill` arm gets the one published skill the scenario names installed
into the workspace's own `.claude/skills/`, the `baseline` arm gets none.
Everything else — prompt, fixture, tool access, model, turn budget — is
identical, so a difference in outcome is attributable to the skill rather than
to the setup.

Each run happens in a disposable workspace holding a copy of the fixture, with
the recording shim first on PATH. The agent is never told abicheck exists; a
skill that has to be *found* is the thing being measured (ADR-058), and naming
the tool in the prompt would hand the baseline arm the same answer.

**The workspace must live outside this repository, and that is enforced rather
than documented.** Claude Code discovers skills from the project the working
directory belongs to, and this repository's own root carries all four published
trees in `.claude/skills/`. A workspace anywhere beneath it therefore hands the
*baseline* arm every skill it is defined by not having — verified against the
real CLI, which reported all four visible from an in-repo directory and exactly
the one installed from a workspace outside it. That is not a degraded
measurement, it is the absence of one: both arms would be skill arms and the
comparison would read as "the skill changes nothing".

Because that confound is silent in the output, each run also *records* what the
CLI said it could see (`system/init`'s skill list) and refuses to continue when
the arms are not what they claim. Evidence that the treatment was applied
belongs in the transcript, next to the outcome it explains.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
EVAL_DIR = ROOT / "agent-evals" / "skills"

sys.path.insert(0, str(EVAL_DIR))

from graders import claim as claim_mod  # noqa: E402

SHIM = EVAL_DIR / "shim" / "abicheck"
PUBLISHED_SKILLS = ROOT / ".claude" / "skills"
PACK = EVAL_DIR / "skill-eval-pack.json"

ARMS = ("skill", "baseline")

#: G37's 2026-08-11 scope note (docs/contribute/plans/
#: g37-agent-skill-quality-evaluation.md) and ADR-058's same-dated amendment:
#: `native-binary-compatibility-review` is the sole flagship subject for every
#: phase, and the other three shipped skills are prototype status — not an
#: L2/L3 evaluation target until the flagship experiment shows measurable
#: lift. `scenarios.yaml` still carries `status: ready` entries for
#: `native-api-evolution` and `native-release-compatibility` (declared before
#: the freeze; not yet re-scoped to `status: planned` there — see that file's
#: own status-field contract before repurposing it for this), so the default,
#: unscoped "every ready scenario" selection below would otherwise run
#: prototype-skill scenarios and mix their results into a nominally
#: flagship-only experiment. Filtered here rather than in the pack so a
#: prototype skill's already-authored scenarios stay intact for when it
#: re-enters scope, one skill at a time, rather than needing to be re-added.
FLAGSHIP_SKILL = "native-binary-compatibility-review"

#: Identical for both arms — the treatment must be the skill, nothing else.
#: `Skill` is included so the skill arm can actually invoke what it finds;
#: the baseline arm has no skills installed, so offering it the tool changes
#: nothing and keeps the two invocations byte-identical apart from the
#: workspace contents.
ALLOWED_TOOLS = ("Bash", "Read", "Glob", "Grep", "Skill")

#: Appended verbatim to every scenario prompt, in both arms.
#:
#: G37 D3: the zero-tolerance dimensions grade the *claim*, and a regex over
#: prose cannot tell "ABI-compatible but source-breaking" — which is exactly
#: `API_BREAK` — from hedging. So the answer carries its own typed envelope
#: instead of being parsed for one.
#:
#: The cost is stated in the plan and worth repeating here: naming the ordinal
#: vocabulary tells *both* arms which distinctions exist, which makes the
#: interaction less natural than an unprompted one and, if anything, helps the
#: baseline. It buys a safety gate that does not rest on reading free text, and
#: since both arms get the identical text it cannot manufacture a difference
#: between them.
ANSWER_CONTRACT = """

When you have finished, end your reply with a fenced ```json block — nothing
after it — in exactly this shape:

{"verdict": "<one of NO_CHANGE, COMPATIBLE, COMPATIBLE_WITH_RISK, API_BREAK, BREAKING, or null if the two sides cannot be compared at all>",
 "evidence": [<which compatibility-tool runs this rests on: number *only* your invocations of the compatibility-checking tool itself, from 0, in the order you ran them — not shell commands, file reads, or compiles. Each run also prints its own number on stderr; use that if you have it>],
 "confident": true or false}

If `confident` is false, add an `"uncertainty"` object with `"reason"` (one of
`not_comparable`, `evidence_too_shallow`, `matrix_target_unrun`,
`contract_coverage_incomplete`) and `"unresolved"` naming what specifically is
unresolved. Give exactly one such block.
"""

#: `python -m abicheck ...` is a documented, supported entry point
#: (`abicheck/__main__.py`), and it does not go through a `PATH` shim named
#: `abicheck` — so a run that reaches the tool that way records no calls at
#: all, and dimensions 3 and 6 then reject a correctly evidenced answer. That
#: biases the *baseline* arm in particular, which is not handed the skill's
#: own spelling of the command, and a biased control is worse than a noisy
#: measurement. This interposer sits on PATH as `python`/`python3` and
#: re-dispatches only that one form through the shim; everything else execs
#: the real interpreter untouched.
#:
#: Written as `/bin/sh` rather than Python on purpose: a Python script named
#: `python3` in the first PATH entry whose shebang is `/usr/bin/env python3`
#: would re-resolve to itself.
#:
#: It walks the interpreter's own options to find the module selector rather
#: than looking only at `$1`. CPython's usage is `python [option] ... [-m mod]`,
#: and all three of `-m abicheck`, the attached `-mabicheck`, and
#: `-X dev -m abicheck` run the tool (verified). Matching `$1` alone sent the
#: latter two straight to the real interpreter — and the backstop below now
#: correctly reads that command's interpreter as `python`, so it would have
#: accepted the unrecorded call as interposed and graded a correct comparison
#: as having obtained no evidence. Detection and interception have to agree
#: about which spellings reach the shim, or one of them silently lies.
#:
#: `-X`, `-W` and `--check-hash-based-pycs` take a separate value, so their
#: value is skipped rather than examined; `-c` and a bare script path both end
#: option processing without a module, so the scan stops there.
_PYTHON_INTERPOSER = """#!/bin/sh
skip=0
seen=0
for arg in "$@"; do
  if [ "$skip" = 1 ]; then skip=0; seen=$((seen+1)); continue; fi
  case "$arg" in
    -m)
      if [ "$(eval echo \\"\\${$((seen+2))}\\")" = "abicheck" ]; then
        shift $((seen+2))
        exec "$SKILL_EVAL_SHIM" "$@"
      fi
      break
      ;;
    -mabicheck)
      shift $((seen+1))
      exec "$SKILL_EVAL_SHIM" "$@"
      ;;
    -X|-W|--check-hash-based-pycs) skip=1 ;;
    -c) break ;;
    -*) ;;
    *) break ;;
  esac
  seen=$((seen+1))
done
exec "$SKILL_EVAL_REAL_PYTHON" "$@"
"""

#: `platform.system()` -> the spelling `examples/ground_truth.json` uses.
_HOST_PLATFORM = {"Linux": "linux", "Darwin": "macos", "Windows": "windows"}


def host_platform() -> str:
    return _HOST_PLATFORM.get(platform.system(), platform.system().lower())


def supported_here(scenario: dict) -> bool:
    """Whether this host can produce the evidence the scenario expects.

    Several catalog cases are Linux-only. Running one elsewhere grades correct
    platform-specific behaviour against a Linux expectation, which is a wrong
    number rather than a missing one. An empty list is "no declared
    restriction" — Category B fixtures, built for this evaluation.
    """
    platforms = scenario.get("platforms") or []
    return not platforms or host_platform() in platforms


#: Compilers that can build a fixture, per language. `cl` appears in both:
#: MSVC is one driver for C and C++ (`/TC`, `/TP`), and it is the *normal*
#: toolchain on Windows — a host with Visual Studio and no GNU or LLVM install
#: was told it lacked both compilers and refused to run, which made the two
#: scenarios the pack marks windows-supported unreachable on the platform they
#: were marked for. Unlike everything else in this file, this entry is reasoned
#: from the pack's own platform declarations rather than verified by running
#: it: this host is Linux, so the MSVC path is untested here.
_C_COMPILERS = ("gcc", "cc", "clang", "cl")
_CXX_COMPILERS = ("g++", "c++", "clang++", "cl")

#: What a fixture's own sources say it needs built.
_CXX_SUFFIXES = (".cpp", ".cc", ".cxx")


def required_languages(scenarios: dict[str, dict]) -> set[str]:
    """Which languages the *selected* scenarios actually need compiled.

    Read off each fixture's own sources. Requiring both unconditionally made a
    C-only selection fail on a host with only a C compiler — a refusal that
    describes the corpus rather than the run being asked for.
    """
    needed: set[str] = set()
    for entry in scenarios.values():
        fixture = ROOT / entry["inputs"]
        for path in fixture.rglob("*"):
            if path.suffix.lower() in _CXX_SUFFIXES:
                needed.add("c++")
            elif path.suffix.lower() == ".c":
                needed.add("c")
    return needed


def missing_toolchain(scenarios: dict[str, dict]) -> list[str]:
    """What this host lacks for the selected scenarios to be runnable at all.

    Every ready fixture ships *source*, not prebuilt libraries, so a host with
    `abicheck` but no compiler cannot produce the two sides — and the run would
    spend real model calls grading tool failures against fixed compatibility
    verdicts, which is a corrupted experiment rather than a missing one. An L2
    scenario additionally needs a header extractor.
    """
    missing: list[str] = []
    languages = required_languages(scenarios)
    if "c" in languages and not any(shutil.which(cc) for cc in _C_COMPILERS):
        missing.append(f"a C compiler ({'/'.join(_C_COMPILERS)})")
    if "c++" in languages and not any(shutil.which(cxx) for cxx in _CXX_COMPILERS):
        missing.append(f"a C++ compiler ({'/'.join(_CXX_COMPILERS)})")
    needs_headers = [
        sid
        for sid, entry in scenarios.items()
        if (entry.get("expected") or {}).get("min_evidence") == "L2"
    ]
    if needs_headers and not any(shutil.which(t) for t in ("castxml", "clang")):
        missing.append(
            f"a header extractor (castxml or clang) for {', '.join(sorted(needs_headers))}"
        )
    return missing


def unknown_scenarios(requested: list[str], pack: dict) -> list[str]:
    """Requested ids that name no ready scenario.

    Silently dropping a typo produced an apparently complete experiment that
    omitted an explicitly requested case — the run exits 0 having measured
    less than was asked for.
    """
    ready = {
        sid for sid, entry in pack["scenarios"].items() if entry["status"] == "ready"
    }
    return sorted(set(requested) - ready)


def is_inside_repo(path: Path) -> bool:
    """Whether `path` would place a workspace inside this checkout."""
    resolved = path.resolve()
    return resolved == ROOT or ROOT in resolved.parents


#: Files in a catalog case that exist to *explain* it, and must never reach the
#: workspace. `README.md` states the verdict in its second line, gives the exact
#: `abicheck compare` command, and quotes the expected findings; `CMakeLists.txt`
#: calls `abicheck_add_case`, naming the tool. Both defeat the measurement in the
#: same way the in-repo workspace did — the baseline arm is handed the tool it is
#: supposed to have to find and the answer it is about to be graded on, and the
#: A/B then reports "the skill changes nothing" for a reason that has nothing to
#: do with the skill. Neither is a library input: an agent building the two sides
#: needs the sources and headers, and `abicheck_add_case` is a macro that exists
#: only inside this repository's own CMake.
EXPLANATORY_FILES = ("README.md", "CMakeLists.txt")

#: What must not appear anywhere in a prepared workspace: the tool's own name,
#: and the verdict vocabulary the run is graded against.
_LEAK_TERMS = ("abicheck", *claim_mod.VERDICT_ORDER)
_LEAK = re.compile("|".join(re.escape(t) for t in _LEAK_TERMS), re.IGNORECASE)

#: Text this scan can read. A compiled artifact or an image is not answer-
#: bearing prose, and decoding one as text produces noise rather than findings.
_SCANNED_SUFFIXES = (".c", ".h", ".cc", ".cpp", ".cxx", ".hpp", ".hh", ".md", ".txt")

#: Translation units whose comments are stripped on the way into a workspace.
SOURCE_SUFFIXES = (".c", ".h", ".cc", ".cpp", ".cxx", ".hpp", ".hh")


def strip_comments(text: str) -> str | None:
    """`text` with its C/C++ comments removed, or None if that isn't safe.

    Needed because excluding explanatory *files* is not enough: the catalog
    annotates its own fixtures with the answer (`/* helper() removed —
    BREAKING change */`, and one header that names the tool and the exact
    change kinds it reports). Those are genuinely useful catalog documentation
    and belong in `examples/`; they just must not travel into a workspace whose
    whole premise is that the agent has to work the answer out. Stripping at
    copy time keeps the corpus intact and the measurement honest, rather than
    degrading published documentation to suit an evaluation.

    Comments only. String and character literals are program *behaviour* — the
    demo consumer prints its own conclusions — so they are preserved here and
    left to `workspace_leaks`, which fails the run rather than rewriting what a
    program does. Newlines inside a block comment are kept so a compiler error
    still names the line the reader is looking at.

    Answers None for a C++ raw string literal (`R"delim(...)"`), whose body may
    contain `//` or `/*` with no comment meaning at all. None of the current
    fixtures uses one; refusing beats mis-stripping, since the scan then still
    sees whatever the file carries and stops the run.
    """
    if re.search(r'\bR"', text):
        return None
    out: list[str] = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch in ('"', "'"):
            quote = ch
            out.append(ch)
            i += 1
            while i < n:
                out.append(text[i])
                if text[i] == "\\" and i + 1 < n:
                    out.append(text[i + 1])
                    i += 2
                    continue
                if text[i] == quote:
                    i += 1
                    break
                i += 1
            continue
        if text.startswith("//", i):
            while i < n and text[i] != "\n":
                i += 1
            continue
        if text.startswith("/*", i):
            end = text.find("*/", i + 2)
            body = text[i:] if end == -1 else text[i : end + 2]
            out.append("\n" * body.count("\n"))
            i = n if end == -1 else end + 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def workspace_leaks(work: Path) -> list[str]:
    """Places in this workspace that name the tool or state the answer.

    Run *after* the workspace is built and before any model call, because the
    exclusion above is necessary and not sufficient: the catalog's own sources
    carry answer comments too (`/* helper() removed — BREAKING change */`), and
    a denylist of filenames cannot see inside a file it correctly copied. A
    scan can, and it fails the run rather than quietly measuring a leak — the
    same reason `check_treatment` refuses when the arms are not what they claim.

    Deliberately case-insensitive and substring-based. A false positive here
    costs one fixture a rename; a false negative costs the whole experiment its
    meaning.
    """
    found: list[str] = []
    for path in sorted(work.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in _SCANNED_SUFFIXES:
            continue
        if ".claude" in path.parts:
            continue  # the treatment itself; naming the tool is its whole job
        text = path.read_text(encoding="utf-8", errors="replace")
        for number, line in enumerate(text.splitlines(), start=1):
            match = _LEAK.search(line)
            if match:
                rel = path.relative_to(work)
                found.append(f"{rel}:{number}: {match.group(0)!r} in {line.strip()!r}")
    return found


#: A token that starts a new argument group in `abicheck_add_case`. Matched by
#: shape rather than against a fixed keyword list: `case05_soname` carries a
#: `V2_LINK_OPTIONS` this code has no interest in, and a fixed list silently
#: folded its value into the preceding group.
_CMAKE_KEYWORD = re.compile(r"^[A-Z][A-Z0-9_]*$")


def demo_app_sources(fixture: Path) -> list[str]:
    """The case's own consumer demo, which is apparatus rather than a library input.

    Read from the case's `abicheck_add_case(APP_SOURCES ...)` rather than
    guessed from a filename, and empty when there is nothing to read — a case
    with no parsable declaration simply keeps everything, and `workspace_leaks`
    is what stops a run if that turns out to matter.

    Excluded because these programs *print the case's conclusion*
    (`"NO_CHANGE at binary ABI level"`), and unlike a comment that text is
    program behaviour this harness will not rewrite. The library sources and
    headers the two sides are built from are what the question is about.
    """
    cml = fixture / "CMakeLists.txt"
    if not cml.is_file() or "abicheck_add_case" not in cml.read_text(encoding="utf-8"):
        return []
    body = cml.read_text(encoding="utf-8").split("abicheck_add_case", 1)[1]
    tokens = body.split(")", 1)[0].split()
    found: list[str] = []
    group: str | None = None
    for token in tokens[1:]:  # tokens[0] is the case name
        if _CMAKE_KEYWORD.match(token):
            group = token
        elif group == "APP_SOURCES":
            found.append(token)
    return found


def _prepare_workspace(work: Path, scenario: dict, arm: str) -> None:
    """A disposable copy of the fixture, plus the arm's skill configuration."""
    fixture = ROOT / scenario["inputs"]
    # Basenames, because `ignore_patterns` matches per directory entry and a
    # pattern containing a separator therefore matches nothing at all. Every
    # current case declares a bare `app.c`/`app.cpp`, but a declared `src/app.c`
    # would silently stop being excluded — and an over-broad basename here only
    # ever drops an extra demo file, which the scan does not care about, while
    # under-exclusion is what puts the answer in the workspace.
    apps = [Path(name).name for name in demo_app_sources(fixture)]
    excluded = (*EXPLANATORY_FILES, *apps)
    shutil.copytree(
        fixture,
        work / "library",
        ignore=shutil.ignore_patterns(*excluded),
    )
    for path in sorted((work / "library").rglob("*")):
        if not path.is_file() or path.suffix.lower() not in SOURCE_SUFFIXES:
            continue
        stripped = strip_comments(path.read_text(encoding="utf-8"))
        if stripped is not None:
            path.write_text(stripped, encoding="utf-8")
    if arm == "skill":
        skills = work / ".claude" / "skills"
        skills.mkdir(parents=True)
        shutil.copytree(
            PUBLISHED_SKILLS / scenario["skill"], skills / scenario["skill"]
        )


def visible_native_skills(events: list[dict]) -> list[str] | None:
    """The published skills the CLI reported seeing, or None if it never said.

    `None` and `[]` are different answers and must not collapse: an absent
    `init` event means the treatment is unverified, while an empty list is
    positive evidence that the baseline arm saw nothing.
    """
    for event in events:
        if event.get("type") == "system" and event.get("subtype") == "init":
            return sorted(s for s in event.get("skills", []) if s.startswith("native-"))
    return None


def resolved_model(events: list[dict]) -> str | None:
    """The model the CLI actually used, or None if it never said.

    Recorded because the two arms run *sequentially* and neither pins a model:
    if the configured or default model changes between them — or between a
    batch and the resume that finishes it — `run_skill_eval.py` still
    aggregates every row by arm alone, and what it would report as skill lift
    could be a model difference. The same class of silent confound as the
    in-repo workspace and the answer-bearing fixture, and answered the same
    way: observe it, record it next to the outcome, and refuse when the arms
    are not comparable.
    """
    for event in events:
        if event.get("type") == "system" and event.get("subtype") == "init":
            model = event.get("model")
            return model if isinstance(model, str) else None
    return None


def check_one_model(index: list[dict], record: dict) -> str | None:
    """Why this run is not comparable with the batch so far, if it is not."""
    models = {row["model"] for row in index if isinstance(row.get("model"), str)}
    model = record.get("model")
    if not isinstance(model, str) or not models or model in models:
        return None
    return (
        f"this run used {model} while the batch so far used "
        f"{', '.join(sorted(models))}; an arm-to-arm difference would not be "
        f"attributable to the skill. Pass --model to pin one, or start a fresh "
        f"output root."
    )


def check_treatment(arm: str, scenario: dict, visible: list[str] | None) -> str | None:
    """The reason this run is not evidence about its arm, if it is not."""
    if visible is None:
        return "the CLI never reported which skills it could see"
    if arm == "baseline" and visible:
        return f"baseline arm could see published skill(s): {', '.join(visible)}"
    if arm == "skill" and visible != [scenario["skill"]]:
        return (
            f"skill arm should see exactly ['{scenario['skill']}'], saw: "
            f"{visible or '[]'}"
        )
    return None


def _final_text(events: list[dict]) -> str:
    for event in reversed(events):
        if event.get("type") == "result":
            return str(event.get("result") or "")
    return ""


def _usage(events: list[dict], elapsed: float) -> dict[str, Any]:
    usage: dict[str, Any] = {"wall_clock_seconds": round(elapsed, 1)}
    for event in reversed(events):
        if event.get("type") != "result":
            continue
        counts = event.get("usage") or {}
        usage["turns"] = event.get("num_turns")
        usage["tokens_in"] = counts.get("input_tokens")
        usage["tokens_out"] = counts.get("output_tokens")
        usage["cost_usd"] = event.get("total_cost_usd")
        break
    usage["tool_calls"] = sum(
        1
        for event in events
        if event.get("type") == "assistant"
        for block in (event.get("message") or {}).get("content") or []
        if isinstance(block, dict) and block.get("type") == "tool_use"
    )
    return usage


def _run_once(
    scenario_id: str,
    scenario: dict,
    arm: str,
    rep: int,
    out_dir: Path,
    timeout: int,
    model: str | None = None,
) -> dict:
    work = out_dir / "workspace"
    work.mkdir(parents=True)
    _prepare_workspace(work, scenario, arm)

    leaks = workspace_leaks(work)
    if leaks:
        # Before the model call, not after: a leaked answer does not degrade
        # this run, it makes it evidence about the fixture rather than about
        # the skill, and spending the call would only buy a number that cannot
        # be interpreted.
        raise RuntimeError(
            f"{scenario_id}: the workspace names the tool or states the answer, "
            "so neither arm's result would be about the skill:\n  " + "\n  ".join(leaks)
        )

    bin_dir = out_dir / "bin"
    bin_dir.mkdir()
    shim = bin_dir / "abicheck"
    shutil.copy2(SHIM, shim)
    shim.chmod(0o755)

    real = shutil.which("abicheck")
    if real is None:  # checked again in main() before any model call
        raise RuntimeError("abicheck is not on PATH")
    env = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "SKILL_EVAL_CALLS": str(out_dir / "calls.jsonl"),
        "SKILL_EVAL_REAL_ABICHECK": real,
        "SKILL_EVAL_SHIM": str(shim),
    }

    # The supported module entry point, routed back through the recorder. Only
    # where a POSIX shell exists; elsewhere `_bypassed_the_recorder` below is
    # the backstop, so the failure is visible rather than a silent empty log.
    real_python = shutil.which("python3") or shutil.which("python")
    if os.name != "nt" and real_python:
        for name in ("python", "python3"):
            interposer = bin_dir / name
            interposer.write_text(_PYTHON_INTERPOSER, encoding="utf-8")
            interposer.chmod(0o755)
        env["SKILL_EVAL_REAL_PYTHON"] = real_python

    prompt = scenario["prompt"] + ANSWER_CONTRACT
    (out_dir / "prompt.txt").write_text(prompt, encoding="utf-8")

    started = time.monotonic()
    proc = subprocess.run(  # noqa: S603
        [
            "claude",
            "-p",
            prompt,
            "--max-turns",
            "12",
            # stream-json, not text: the event stream is what carries which
            # skill activated and which tools ran, so dimension 1 grades an
            # observation rather than an inference from the final prose.
            "--output-format",
            "stream-json",
            "--verbose",
            # Named tools rather than `--permission-mode bypassPermissions`:
            # that maps to --dangerously-skip-permissions, which the CLI
            # refuses under root, so an evaluation running as root would
            # record 48 four-second failures and call them results. Both arms
            # get the identical list, so the treatment stays the skill alone.
            "--allowedTools",
            *ALLOWED_TOOLS,
            # Pinning is optional but recorded either way: the arms run
            # sequentially, so a default that moves between them would show up
            # as skill lift.
            *(("--model", model) if model else ()),
        ],
        cwd=work,
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    elapsed = time.monotonic() - started

    (out_dir / "events.jsonl").write_text(proc.stdout, encoding="utf-8")
    if proc.stderr:
        (out_dir / "runner.err").write_text(proc.stderr, encoding="utf-8")

    events: list[dict] = []
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    (out_dir / "final.md").write_text(_final_text(events), encoding="utf-8")
    usage = _usage(events, elapsed)
    (out_dir / "usage.json").write_text(json.dumps(usage, indent=2), encoding="utf-8")

    if _bypassed_the_recorder(events, out_dir / "calls.jsonl"):
        raise RuntimeError(
            f"{scenario_id}/{arm}/{rep}: the run reached the tool through "
            f"`python -m abicheck`, which the recorder never saw, so its "
            f"evidence is missing rather than absent. Re-run on a host where "
            f"the PATH interposer applies."
        )

    visible = visible_native_skills(events)
    problem = check_treatment(arm, scenario, visible)
    if problem is not None:
        raise RuntimeError(
            f"{scenario_id}/{arm}/{rep}: {problem}. The arms are not what they "
            f"claim, so no run in this batch is evidence about the skill."
        )

    return {
        "scenario_id": scenario_id,
        "arm": arm,
        "repetition": rep,
        "skill": scenario["skill"],
        "exit_code": proc.returncode,
        "visible_skills": visible,
        "model": resolved_model(events),
        "wall_clock_seconds": usage["wall_clock_seconds"],
    }


#: The two names the interposer is installed under. A module entry spelled any
#: other way — an absolute path, or a versioned `python3.12` — resolves to the
#: real interpreter and never reaches the shim.
INTERPOSED_INTERPRETERS = ("python", "python3")


def _interpreter_before(tokens: list[str], index: int) -> str:
    """The interpreter this module entry was spelled with, or `""` for none.

    Identified by its own name — every spelling that can reach `-m` is some
    `python`: bare, versioned (`python3.12`), or absolute. Nothing before the
    module entry is answerable structurally, since an interpreter option's
    value (`-X dev`) is an ordinary word and indistinguishable from a program
    name by position alone.
    """
    for token in reversed(tokens[:index]):
        if Path(token).name.lower().startswith("python"):
            return token
    return ""


def module_entry_interpreters(events: list[dict]) -> list[str]:
    """The interpreter each observed `-m abicheck` invocation was spelled with.

    `""` when the command names no interpreter before the module entry, which
    cannot reach the interposer either.

    Read from a `Bash` block's `command` alone, not from a serialization of
    every tool input. Serializing the whole payload made any `Write` or `Edit`
    whose *content* mentions `-m abicheck` — this file and its tests among them
    — register as an invocation, and the token before it is then some ordinary
    prose word rather than an interpreter, so the run aborted having bypassed
    nothing. A check that fails correct runs is worse than the gap it closes.
    """
    found: list[str] = []
    for event in events:
        if event.get("type") != "assistant":
            continue
        for block in (event.get("message") or {}).get("content") or []:
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            if block.get("name") != "Bash":
                continue
            command = (block.get("input") or {}).get("command")
            if not isinstance(command, str):
                continue
            tokens = command.split()
            for index, token in enumerate(tokens):
                # Both spellings CPython accepts. `-mabicheck` is not a
                # variant worth ignoring: it is the one the interposer used to
                # miss, so it is exactly the spelling a bypass arrives as.
                separated = (
                    token == "-m"
                    and index + 1 < len(tokens)
                    and tokens[index + 1] == "abicheck"
                )
                if not separated and token != "-mabicheck":
                    continue
                # The nearest preceding token that *is* an interpreter, rather
                # than whatever sits immediately before `-m`: `python -X dev -m
                # abicheck` is spelled with `python`, and reading `dev` as the
                # interpreter would report an interposed run as a bypass.
                # Walking back over options alone cannot do it — `dev` is
                # `-X`'s value and looks like any other word.
                found.append(_interpreter_before(tokens, index))
    return found


def _bypassed_the_recorder(
    events: list[dict], calls: Path, interposed: bool | None = None
) -> bool:
    """Whether the run invoked the tool by a route the shim does not wrap.

    The interposer prevents this where a POSIX shell exists; this backstop is
    what makes the remaining case *visible*. An unrecorded call otherwise reads
    identically whether the agent never reached the tool or reached it by a
    route the recorder does not wrap — and those grade the same (dimensions 3
    and 6 both fail) while meaning opposite things about the agent.

    This used to return early whenever the log was non-empty, which made it
    dead in practice rather than merely narrow: every published skill runs
    `abicheck --version` at preflight, so the log is essentially always
    non-empty by the time a comparison happens, and a later
    `/usr/bin/python3.12 -m abicheck compare ...` — which resolves past the
    `python`/`python3` interposer — was accepted as a complete transcript.

    So each observed module entry is judged on its own spelling. A bare
    `python`/`python3` reaches the interposer and is fine; anything else is a
    bypass, as is *any* module entry on a host where the interposer was not
    installed at all (Windows), and any module entry at all when nothing was
    recorded — the interposer plainly did not do its job in that case however
    the command was spelled.
    """
    if interposed is None:
        interposed = os.name != "nt"
    interpreters = module_entry_interpreters(events)
    if not interpreters:
        return False
    if not interposed:
        return True
    recorded = calls.is_file() and bool(calls.read_text(encoding="utf-8").strip())
    if not recorded:
        return True
    return any(Path(name).name not in INTERPOSED_INTERPRETERS for name in interpreters)


def _recovered_record(
    out_dir: Path,
    sid: str,
    arm: str,
    rep: int,
    scenario: dict,
    interposed: bool | None = None,
) -> dict:
    """An index row for a run whose directory exists but whose row does not.

    A crash between writing `final.md` and rewriting `index.json` used to make
    that repetition permanently invisible: every later resume skipped the
    directory as done and never added the row, so the aggregate silently
    counted one run fewer than was actually paid for.

    The treatment check runs again here, and that is not belt-and-braces. A run
    rejected by `_run_once` has *already written* `final.md` — the check happens
    after — so recovering on the strength of that file alone would launder a
    contaminated baseline into accepted evidence on the next resume, which is
    the one failure the check exists to prevent.
    """
    record = {
        "scenario_id": sid,
        "arm": arm,
        "repetition": rep,
        "skill": scenario["skill"],
        "recovered": True,
    }
    usage_path = out_dir / "usage.json"
    if usage_path.is_file():
        try:
            record["wall_clock_seconds"] = json.loads(
                usage_path.read_text(encoding="utf-8")
            ).get("wall_clock_seconds")
        except json.JSONDecodeError:
            pass
    events_path = out_dir / "events.jsonl"
    events: list[dict] = []
    if events_path.is_file():
        for line in events_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    visible = visible_native_skills(events)
    record["visible_skills"] = visible
    # The resume path is exactly what `check_one_model` was written for, and a
    # recovered row without a model is invisible to it: the batch would then
    # accept the next run on whatever the default has moved to, and the arm
    # comparison would carry the model difference the guard exists to catch.
    record["model"] = resolved_model(events)
    problem = check_treatment(arm, scenario, visible)
    if problem is None:
        # The third rejection `_run_once` can raise. It fires *before* the model
        # call, so a leaked workspace never reaches `final.md` and this is
        # unreachable in the crash-resume path it was written for — but the
        # workspace is still on disk and still answerable, and a check that is
        # cheap and cannot be wrong is worth more than the argument that it
        # cannot trigger.
        leaks = workspace_leaks(out_dir / "workspace")
        if leaks:
            problem = "the workspace names the tool or states the answer: " + "; ".join(
                leaks
            )
    if problem is None and _bypassed_the_recorder(
        events, out_dir / "calls.jsonl", interposed
    ):
        # The same reasoning as the treatment check, for the other rejection
        # `_run_once` can raise after writing `final.md`. On a host without the
        # interposer every module entry bypasses the recorder, so this is the
        # *likelier* of the two to be rediscovered on a resume — and recovering
        # it would index a harness limitation as agent behaviour.
        problem = "the run reached the tool by a route the recorder does not wrap"
    if problem is not None:
        raise RuntimeError(
            f"{sid}/{arm}/{rep}: {problem}. This run was left on disk without an "
            f"index row, which is what a rejected run looks like — delete its "
            f"directory to re-run it rather than recovering it as evidence."
        )
    return record


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--out", required=True, help="Directory to write runs into")
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument(
        "--model",
        default=None,
        help="Pin the model for every run. Recorded either way; see --help notes.",
    )
    parser.add_argument(
        "--arms",
        default=",".join(ARMS),
        help=f"Comma-separated subset of {','.join(ARMS)}",
    )
    parser.add_argument(
        "--scenarios",
        default="",
        help=(
            "Comma-separated ids; default: every ready scenario for the "
            f"flagship skill ({FLAGSHIP_SKILL})"
        ),
    )
    parser.add_argument(
        "--include-prototype-skills",
        action="store_true",
        help=(
            "Also select ready scenarios for prototype-status skills (all "
            f"skills other than {FLAGSHIP_SKILL}). Off by default per G37's "
            "2026-08-11 scope note — the portfolio is frozen and only the "
            "flagship skill is a live evaluation target."
        ),
    )
    parser.add_argument("--timeout", type=int, default=900)
    args = parser.parse_args(argv)

    # These checks run before the first model call: a run that discovers any of
    # them afterwards has spent real money producing output that *looks* like a
    # completed evaluation. An absent abicheck makes the shim answer 70 to every
    # call — indistinguishable at grading time from a tool failure; a mistyped
    # arm silently produces a baseline workspace under its name; and an in-repo
    # output root gives the baseline arm the skills that define it.
    if shutil.which("abicheck") is None:
        print(
            "abicheck is not on PATH; every recorded call would be a shim "
            "misconfiguration. Install it with `pip install -e .`.",
            file=sys.stderr,
        )
        return 1
    if args.repetitions < 1:
        # `range(0)` is empty, so the run would print that zero runs were
        # written and exit 0 — an accidental count reading as a completed
        # evaluation.
        print(
            f"--repetitions must be at least 1, got {args.repetitions}",
            file=sys.stderr,
        )
        return 1
    arms = [a for a in args.arms.split(",") if a]
    unknown = sorted(set(arms) - set(ARMS))
    if unknown:
        print(f"unknown arm(s): {', '.join(unknown)}", file=sys.stderr)
        return 1
    if not arms:
        # `--arms ""` (or only commas) passes the unknown-arm check, since the
        # empty set contains nothing unknown. The batch then runs nothing and
        # exits 0 — the same "invalid input reads as a completed evaluation"
        # shape as a non-positive repetition count.
        print(f"--arms selected none of {', '.join(ARMS)}", file=sys.stderr)
        return 1

    out_root = Path(args.out)
    if is_inside_repo(out_root):
        print(
            f"--out {out_root} is inside {ROOT}, so every workspace would belong "
            f"to this project and the baseline arm would discover the published "
            f"skills in .claude/skills/. Choose a path outside the checkout.",
            file=sys.stderr,
        )
        return 1

    pack = json.loads(PACK.read_text(encoding="utf-8"))
    wanted = [s for s in args.scenarios.split(",") if s]
    bad = unknown_scenarios(wanted, pack)
    if bad:
        print(
            f"no ready scenario named: {', '.join(bad)} — refusing to run a "
            f"subset of what was asked for",
            file=sys.stderr,
        )
        return 1
    if not args.include_prototype_skills:
        # Explicitly named ids get the same refusal an unknown id gets, rather
        # than being silently dropped — the freeze is a policy choice the
        # caller must be told about, not a filter that quietly shrinks what
        # they asked for. The default (unnamed) sweep is narrowed below
        # instead of refused, since "every ready scenario" is documented as
        # meaning "every ready flagship scenario" while the freeze holds.
        prototype_wanted = sorted(
            sid
            for sid in wanted
            if pack["scenarios"].get(sid, {}).get("skill") != FLAGSHIP_SKILL
        )
        if prototype_wanted:
            print(
                f"{', '.join(prototype_wanted)}: not the flagship skill "
                f"({FLAGSHIP_SKILL}) — G37's 2026-08-11 scope note excludes "
                f"prototype-status skills from evaluation by default. Pass "
                f"--include-prototype-skills to run them anyway.",
                file=sys.stderr,
            )
            return 1
    scenarios = {
        sid: entry
        for sid, entry in sorted(pack["scenarios"].items())
        if entry["status"] == "ready"
        and (not wanted or sid in wanted)
        and (args.include_prototype_skills or entry.get("skill") == FLAGSHIP_SKILL)
    }
    if not scenarios:
        print("no ready scenarios selected", file=sys.stderr)
        return 1

    unrunnable = sorted(
        sid for sid in wanted if sid in scenarios and not supported_here(scenarios[sid])
    )
    if unrunnable:
        # Skipping is right for the default all-scenarios sweep — several
        # catalog cases are Linux-only — but an *explicitly named* one that
        # this host cannot run is a request the batch cannot honour, and
        # exiting 0 with it silently omitted reports an experiment that did
        # not happen.
        print(
            f"requested scenario(s) not supported on {host_platform()}: "
            f"{', '.join(unrunnable)}",
            file=sys.stderr,
        )
        return 1

    runnable = {sid: entry for sid, entry in scenarios.items() if supported_here(entry)}
    lacking = missing_toolchain(runnable)
    if lacking:
        print(
            "this host cannot build the fixtures, so every run would grade a "
            f"tool failure against a fixed verdict. Missing: {'; '.join(lacking)}.",
            file=sys.stderr,
        )
        return 1

    out_root.mkdir(parents=True, exist_ok=True)

    # Resuming must not lose what earlier invocations recorded: starting from
    # an empty list and rewriting on the first new run drops every completed
    # repetition from the index while its directory still sits on disk.
    index_path = out_root / "index.json"
    index: list[dict] = []
    if index_path.is_file():
        try:
            index = json.loads(index_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            # Naming the file and the remedy, because the alternative is a bare
            # decode error on every later resume while the run directories are
            # all still on disk and individually recoverable.
            print(
                f"{index_path} is not valid JSON ({exc}) — most likely an "
                f"interrupted write. Delete it and re-run: each completed run "
                f"directory is recovered into a fresh index.",
                file=sys.stderr,
            )
            return 1
    done = {(r["scenario_id"], r["arm"], r["repetition"]) for r in index}

    def flush() -> None:
        # Write-then-rename, so an interruption leaves the previous index
        # intact rather than a truncated one. `write_text` truncates first, and
        # a partial index is exactly what makes the whole output root
        # unresumable — the failure this index exists to prevent.
        tmp = index_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(index, indent=2), encoding="utf-8")
        os.replace(tmp, index_path)

    for sid, scenario in scenarios.items():
        if not supported_here(scenario):
            print(
                f"skip {sid}: not supported on {host_platform()} ({scenario['platforms']})"
            )
            continue
        for arm in arms:
            for rep in range(args.repetitions):
                out_dir = out_root / sid / arm / str(rep)
                if (sid, arm, rep) in done:
                    print(f"skip {sid}/{arm}/{rep} (already run)")
                    continue
                if (out_dir / "final.md").exists():
                    print(f"recover {sid}/{arm}/{rep} (ran, was not indexed)")
                    index.append(_recovered_record(out_dir, sid, arm, rep, scenario))
                    flush()
                    continue
                if out_dir.exists():
                    # A repetition interrupted after `workspace/` was created
                    # but before `final.md` was written is unfinished, and
                    # `_run_once`'s own mkdir would raise on it forever —
                    # making that repetition unresumable without manual
                    # deletion. Nothing here is evidence yet, so clear it.
                    shutil.rmtree(out_dir)
                out_dir.mkdir(parents=True)
                print(f"run  {sid}/{arm}/{rep}", flush=True)
                try:
                    record = _run_once(
                        sid, scenario, arm, rep, out_dir, args.timeout, args.model
                    )
                except subprocess.TimeoutExpired:
                    record = {
                        "scenario_id": sid,
                        "arm": arm,
                        "repetition": rep,
                        "skill": scenario["skill"],
                        "exit_code": None,
                        "timed_out": True,
                    }
                    (out_dir / "final.md").write_text("", encoding="utf-8")
                mixed = check_one_model(index, record)
                if mixed:
                    raise RuntimeError(f"{sid}/{arm}/{rep}: {mixed}")
                index.append(record)
                flush()

    print(f"\n{len(index)} runs written to {out_root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
