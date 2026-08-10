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

"""Build the skill eval pack (G37 Phase 0) — the one artifact the evaluation
runs against, and the one interface `agent-benchmark` consumes.

The pack is *derived*, never hand-written: it resolves scenario references
against the real corpora and computes every hash D6's freshness check reads.
`--check` re-derives it and fails on drift, the same contract every other
generated artifact in this repository is under.

What it records, and why each entry exists (G37 D6):

  skills[<name>].tree        one skill's own generated tree — its SKILL.md, its
                             references/, and the shared fragments the generator
                             actually resolved into it, so a shared-fragment edit
                             moves exactly the skills that cite it
  scenarios[<id>]            manifest record + fixture closure + ground-truth
                             entry. Hashing the record alone would leave a
                             fixture edited in place looking fresh
  trigger_corpus             the L1l labelled set. Activation precision is
                             computed per skill across the whole corpus, so any
                             prompt or label change invalidates all of it
  harness                    the runner, the recording shim and the graders — a
                             transcript produced under a different treatment is
                             not evidence about the same thing
  abicheck_surface           the surface the skills actually consume — the CLI
                             command/option reference, the detector spec, and the
                             report schema, read as committed files. Deliberately
                             narrow (hashing all of abicheck/ would invalidate
                             every bundle on every source commit) and
                             deliberately not live introspection (that made the
                             pack a function of the host; see SURFACE_SOURCES)
There is deliberately **no** `build` entry: the publication digest is
environment-bound and is computed on demand by `publication_build_digest()`.
Recording even its repo-content half here would make every PR touching
`abicheck/` regenerate the pack, and let two independently-green PRs merge
into a stale one.

Every entry carries two fields beyond its digest, and both exist because a
prose invariant already failed here twice in opposite directions (G37 D6):

  affects   the skills whose evidence the hash invalidates. A hash that moves
            without nominating something to re-run leaves the author with a
            failing freshness check and nothing to run
  roots     the repo-relative paths that route to this hash. This is what makes
            *completeness* checkable: `check_skill_eval_freshness.py` resolves
            every input a run was observed reading through these, and an input
            matching no root is an input contributing to no hash — the failure
            that let the trigger corpus stay unhashed for a commit

`roots` is a routing declaration, not the digest's definition: the digest
function above is authoritative for content, `roots` only answers "which hash
would a change here move". They are deliberately allowed to be coarser (a
scenario's roots name `scenarios.yaml` as a whole, while its digest covers only
that scenario's own record), because a coarse route over-nominates — which is
safe — where a missing one silently accepts stale evidence.

Run:

    python scripts/gen_skill_eval_pack.py            # write the pack
    python scripts/gen_skill_eval_pack.py --check    # verify committed output
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
# The *generated* tree, deliberately not `skills-src/` — see `_skill_tree_digest`.
PUBLISHED_SKILLS = ROOT / ".agents" / "skills"
EVAL_DIR = ROOT / "agent-evals" / "skills"
SCENARIOS = EVAL_DIR / "scenarios.yaml"
RUBRIC = EVAL_DIR / "rubric.yaml"
TRIGGER_CORPUS = ROOT / "tests" / "agent_skills" / "trigger_corpus.yaml"
GROUND_TRUTH = ROOT / "examples" / "ground_truth.json"
EXAMPLES = ROOT / "examples"
PACK = EVAL_DIR / "skill-eval-pack.json"

#: Bumped when the pack's own shape changes, so a consumer reading an older
#: shape fails loudly rather than misreading a field.
PACK_VERSION = 1

#: The consumed surface, hashed through the repository's own *generated*
#: projections of it rather than through live introspection.
#:
#: An earlier version walked the live Click tree and registry. That is a
#: function of the running interpreter, and the pack is a committed artifact
#: whose `--check` runs on Linux, macOS and Windows alike — so any component
#: that varies with the host makes this gate unsatisfiable on some platform,
#: which is exactly what happened (the macOS unit lane failed while every
#: other lane passed). A digest over committed files cannot.
#:
#: These three are not a compromise on narrowness: `cli-reference.md` is
#: generated from the live command tree and carries every command, option,
#: default, choice and help string; `detector-spec.json` carries every
#: `ChangeKind`'s verdict, severity and minimum evidence; the report schema is
#: what a skill parses. All three are drift-gated against the live objects in
#: the same `pr` profile (`gen_cli_reference.py --check`,
#: `gen_detector_spec.py --check`), so a real surface change reaches them in
#: the PR that makes it, while an edit to `cli.py` that changes no user-facing
#: surface moves nothing here — the asymmetry D6 asks for, now without the
#: host dependence.
SURFACE_SOURCES = (
    Path("docs/reference/cli-reference.md"),
    Path("docs/reference/detector-spec.json"),
    Path("abicheck/schemas/compare_report.schema.json"),
)

#: Routing declarations for the surface hash: the artifacts above plus the
#: sources whose change is what regenerates them. Routes over-nominate on
#: purpose (see the module docstring), and a route to the *source* is what
#: makes an observed read of `cli.py` resolve to this hash at all.
SURFACE_ROOTS = (
    *SURFACE_SOURCES,
    Path("abicheck/cli.py"),
    Path("abicheck/cli_options.py"),
    Path("abicheck/cli_options_contract.py"),
    Path("abicheck/schemas"),
    Path("abicheck/change_registry.py"),
    Path("abicheck/checker_policy.py"),
)

#: Everything that determines the installed checker's behaviour, for the
#: publication-grade digest. Defined by inclusion so the committed evidence
#: tree is outside it by construction.
BUILD_SOURCES = (
    Path("abicheck"),
    Path("pyproject.toml"),
)

#: The harness: the runner's own prompt/launch configuration, the recording
#: shim, and the graders. Phase 0 ships none of these yet, so the digest is
#: over an empty set today and moves the first time Phase 1 lands one — which
#: is the correct behaviour, not a gap: evidence recorded under no harness and
#: evidence recorded under the first one are not evidence about the same thing.
HARNESS_SOURCES = (
    Path("agent-evals/skills/runners"),
    Path("agent-evals/skills/shim"),
    Path("agent-evals/skills/graders"),
    Path("agent-evals/skills/run_skill_eval.py"),
    Path("agent-evals/skills/grade_bundle.py"),
)


def _digest(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _digest_paths(paths: list[Path]) -> str:
    """Digest a set of files as one value, path-ordered so it is stable."""
    h = hashlib.sha256()
    for path in sorted(paths, key=lambda p: p.relative_to(ROOT).as_posix()):
        h.update(path.relative_to(ROOT).as_posix().encode())
        h.update(b"\0")
        h.update(path.read_bytes())
        h.update(b"\0")
    return "sha256:" + h.hexdigest()


#: Build and install residue that is present in one checkout and absent in
#: another. Digesting it makes the pack a property of what happened to be run
#: on a machine rather than of the repository — `examples/**/*.so` is
#: gitignored precisely because it is built, and a `pip install -e .` can leave
#: artifacts inside the package tree. `tests/test_skill_eval_pack.py` checks
#: the stronger property (every hashed file is git-tracked); this is the filter
#: that keeps the common cases from ever reaching it.
UNTRACKED_SUFFIXES = frozenset(
    {".pyc", ".pyo", ".so", ".dylib", ".dll", ".pyd", ".o", ".a"}
)
UNTRACKED_DIR_NAMES = frozenset({"__pycache__", ".pytest_cache", "build", "dist"})


def _tree_files(root: Path) -> list[Path]:
    return [
        p
        for p in sorted(root.rglob("*"))
        if p.is_file()
        and not (UNTRACKED_DIR_NAMES & set(p.parts))
        and p.suffix not in UNTRACKED_SUFFIXES
        and not p.name.endswith(".egg-info")
    ]


def _published_skill_names() -> list[str]:
    if not PUBLISHED_SKILLS.is_dir():
        return []
    return sorted(
        p.name for p in PUBLISHED_SKILLS.iterdir() if (p / "SKILL.md").is_file()
    )


def _skill_tree_digest(name: str) -> str:
    """Hash one skill's *generated* tree.

    The generated tree is the right input, not `skills-src/<name>/`: the
    generator resolves shared fragments transitively and copies the ones a
    skill actually cites into it, so this digest moves for exactly the skills
    a `shared/` edit reaches — which is the same dependency graph the
    affected-skill selection rule reads.
    """
    return _digest_paths(_tree_files(PUBLISHED_SKILLS / name))


def _load_scenarios() -> dict[str, Any]:
    return yaml.safe_load(SCENARIOS.read_text(encoding="utf-8"))


def _fixture_paths(scenario: dict[str, Any]) -> list[Path]:
    """Every file whose content the scenario feeds the agent.

    Category A resolves to the examples/ case directory plus that case's own
    ground-truth entry; Category B to its declared fixture tree. A scenario
    whose inputs change must go stale, so the closure is hashed, not the path.
    """
    if scenario["category"] == "A":
        case_dir = EXAMPLES / scenario["case"]
        return _tree_files(case_dir) if case_dir.is_dir() else []
    fixture = ROOT / scenario["fixture"]
    return _tree_files(fixture) if fixture.is_dir() else []


def _scenario_digest(scenario: dict[str, Any], ground_truth: dict[str, Any]) -> str:
    h = hashlib.sha256()
    h.update(json.dumps(scenario, sort_keys=True).encode())
    h.update(b"\0")
    for path in sorted(
        _fixture_paths(scenario), key=lambda p: p.relative_to(ROOT).as_posix()
    ):
        h.update(path.relative_to(ROOT).as_posix().encode())
        h.update(b"\0")
        h.update(path.read_bytes())
        h.update(b"\0")
    if scenario["category"] == "A":
        entry = ground_truth.get("verdicts", {}).get(scenario["case"], {})
        h.update(json.dumps(entry, sort_keys=True).encode())
    return "sha256:" + h.hexdigest()


def _surface_digest() -> str:
    """Digest the surface a skill can actually invoke or read.

    Read off `SURFACE_SOURCES` — three committed, drift-gated projections of
    the live objects — rather than by introspecting those objects here. See
    that constant for why: a committed artifact checked on three operating
    systems cannot be a function of the running interpreter, and hashing
    `abicheck/`'s bytes instead would invalidate every bundle on every source
    commit. The residual — a verdict that changes with no surface change — is
    what Phase 6's full-build-digest pass exists to close.
    """
    return _digest_paths(_expand(SURFACE_SOURCES))


def _expand(sources: tuple[Path, ...]) -> list[Path]:
    files: list[Path] = []
    for rel in sources:
        target = ROOT / rel
        if target.is_dir():
            files.extend(_tree_files(target))
        elif target.is_file():
            files.append(target)
    return files


def publication_build_digest() -> str:
    """The Phase 6 publication digest: repo content **plus** the resolved
    runtime dependency versions.

    Computed on demand and **not recorded in the pack at all**, for two
    reasons that point the same way.

    The dependency versions are a property of the *environment*, so a
    committed value would make the pack fail its own drift check on any
    machine whose lockfile resolved differently. Publication reads this
    function at the moment it publishes, against the environment it publishes
    from — the only moment "is this evidence about the build being shipped"
    has a single answer.

    And the repo-content half alone, had it stayed in the pack, would have
    made *every* PR touching `abicheck/` regenerate the pack: it digests the
    whole package tree. Worse, two independently-green PRs could then merge
    into a stale pack on `main`, since neither saw the other's sources. That
    is a real tax and a real merge hazard for an entry no routine check ever
    reads — it was `publication_only` — so the pack carries no `build` entry
    and Phase 6's publication step calls this function instead.
    """
    from importlib.metadata import (
        PackageNotFoundError,
        distribution,
        version as dist_version,
    )

    try:
        requires = distribution("abicheck").requires or []
    except PackageNotFoundError:  # pragma: no cover - abicheck is installed in CI
        requires = []

    resolved: dict[str, str] = {}
    for requirement in requires:
        if ";" in requirement:  # an extra/marker-gated dependency is not runtime
            continue
        name = re.split(r"[\s<>=!~\[(]", requirement, maxsplit=1)[0].strip()
        if not name:
            continue
        try:
            resolved[name] = dist_version(name)
        except PackageNotFoundError:
            resolved[name] = "absent"

    payload = {
        "repo": _digest_paths(_expand(BUILD_SOURCES)),
        "runtime_dependencies": dict(sorted(resolved.items())),
    }
    return _digest(json.dumps(payload, sort_keys=True).encode())


def _rubric_summary() -> dict[str, Any]:
    """The gating contract, projected into the pack.

    Only what a consumer needs to grade against: `k`, and per dimension its
    grader kind and gating mode. Deliberately not the notes — `rubric.yaml`
    stays the fact owner, and duplicating its prose would give the pack a
    second copy to drift.
    """
    rubric = yaml.safe_load(RUBRIC.read_text(encoding="utf-8"))
    return {
        "schema_version": rubric["schema_version"],
        "repetitions": rubric["repetitions"],
        "dimensions": [
            {
                "id": d["id"],
                "name": d["name"],
                "grader": d["grader"],
                "gating": d["gating"],
                **(
                    {"uncertainty_rules": d["uncertainty_rules"]}
                    if "uncertainty_rules" in d
                    else {}
                ),
            }
            for d in rubric["dimensions"]
        ],
    }


def _roots(sources: tuple[Path, ...]) -> list[str]:
    """Declared routes, not the resolved file list.

    A root is recorded whether or not it exists today: `HARNESS_SOURCES` names
    paths Phase 1 creates, and a route that appears only once its target exists
    would leave the first harness file routing to nothing.
    """
    return sorted(p.as_posix() for p in sources)


def build_pack() -> dict[str, Any]:
    manifest = _load_scenarios()
    ground_truth = json.loads(GROUND_TRUTH.read_text(encoding="utf-8"))
    skills = _published_skill_names()

    scenarios: dict[str, Any] = {}
    for scenario in manifest["scenarios"]:
        if scenario["category"] == "A":
            fixture_root = (EXAMPLES / scenario["case"]).relative_to(ROOT).as_posix()
        else:
            fixture_root = scenario["fixture"]
        # The pack is the cross-repository interface, so a scenario entry has
        # to be runnable and gradeable from the pack alone: a consumer that
        # had to read `scenarios.yaml` and `ground_truth.json` to reconstruct
        # the prompt or the expected outcome would be coupled to this
        # repository's private layout, which is exactly what publishing an
        # artifact instead of a library is meant to avoid (Codex review).
        # Category A's expectation is *resolved* here rather than restated:
        # the catalog stays the one fact owner, and the pack carries its
        # answer.
        expected = dict(scenario.get("expected") or {})
        if scenario["category"] == "A":
            entry = ground_truth.get("verdicts", {}).get(scenario["case"], {})
            expected = {
                "verdict": entry.get("expected"),
                "kinds": entry.get("expected_kinds", []),
                "min_evidence": entry.get("min_evidence"),
                "resolved_from": "examples/ground_truth.json",
            }

        scenarios[scenario["id"]] = {
            "skill": scenario["skill"],
            "category": scenario["category"],
            "status": scenario["status"],
            "prompt": scenario["prompt"],
            "inputs": fixture_root,
            "invocation": scenario.get("invocation", {}),
            "expected": expected,
            "digest": _scenario_digest(scenario, ground_truth),
            # The manifest and the catalog are shared by every scenario, so a
            # path under either routes to all of them. Over-nomination is the
            # safe direction; see the module docstring on `roots`.
            "roots": sorted(
                {
                    fixture_root,
                    SCENARIOS.relative_to(ROOT).as_posix(),
                    GROUND_TRUTH.relative_to(ROOT).as_posix(),
                }
            ),
            "affects": [scenario["skill"]],
        }

    # Every hash names the skills whose evidence it invalidates. A hash with no
    # mapping would reject evidence while nominating nothing to regenerate it.
    return {
        "pack_version": PACK_VERSION,
        # Carried for the same self-containment reason as the scenario bodies
        # above: a consumer grading against this pack needs `k` and each
        # dimension's gating mode, and reading them out of `rubric.yaml`
        # would put it back inside this repository.
        "rubric": _rubric_summary(),
        "skills": {
            name: {
                "tree": _skill_tree_digest(name),
                "roots": [(PUBLISHED_SKILLS / name).relative_to(ROOT).as_posix()],
                "affects": [name],
            }
            for name in skills
        },
        "scenarios": scenarios,
        "shared": {
            "trigger_corpus": {
                "digest": _digest(TRIGGER_CORPUS.read_bytes()),
                "roots": [TRIGGER_CORPUS.relative_to(ROOT).as_posix()],
                "affects": skills,
            },
            "harness": {
                "digest": _digest_paths(_expand(HARNESS_SOURCES)),
                "roots": _roots(HARNESS_SOURCES),
                "affects": skills,
            },
            "abicheck_surface": {
                "digest": _surface_digest(),
                "roots": _roots(SURFACE_ROOTS),
                "affects": skills,
            },
            # No `build` entry, deliberately — see `publication_build_digest()`.
        },
    }


#: The ownership marker `scripts/check_ai_readiness.py`'s
#: `generated-file-ownership` check looks for. Carried as a JSON *field*, not a
#: comment header: JSON has no comments, and a pack a consumer cannot
#: `json.load()` would defeat the point of publishing one.
GENERATED_MARKER = "generated by scripts/gen_skill_eval_pack.py — do not hand-edit"


def describe_drift(committed_text: str, rendered_text: str) -> list[str]:
    """Name the entries that differ, not just the fact that the file does.

    A `--check` failure that says only "out of date" is a fine message for the
    author who just edited a skill and knows why. It is a poor one for the
    failure this exists to make legible: a pack that reproduces on one machine
    and not another. That happened — the macOS lane failed while every other
    passed — and the message gave no way to tell *which* entry moved, which is
    most of what made diagnosing it hard.
    """
    try:
        committed = json.loads(committed_text)
        rendered = json.loads(rendered_text)
    except json.JSONDecodeError:
        return ["(committed pack is not valid JSON — regenerate it)"]

    drift: list[str] = []
    for section in ("skills", "scenarios", "shared"):
        old, new = committed.get(section, {}), rendered.get(section, {})
        for key in sorted(set(old) | set(new)):
            if key not in old:
                drift.append(f"{section}.{key}: added")
            elif key not in new:
                drift.append(f"{section}.{key}: removed")
            elif old[key] != new[key]:
                drift.append(
                    f"{section}.{key}: committed {old[key].get('digest') or old[key].get('tree')}"
                    f" -> generated {new[key].get('digest') or new[key].get('tree')}"
                )
    for key in ("pack_version", "rubric"):
        if committed.get(key) != rendered.get(key):
            drift.append(f"{key}: differs")
    return drift or ["(entries match; the difference is in formatting)"]


def _render(pack: dict[str, Any]) -> str:
    return (
        json.dumps({"_generated": GENERATED_MARKER, **pack}, indent=2, sort_keys=True)
        + "\n"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--check",
        action="store_true",
        help="Verify the committed pack matches what the generator produces; do not write.",
    )
    args = parser.parse_args(argv)

    rendered = _render(build_pack())

    if not args.check:
        PACK.write_text(rendered, encoding="utf-8")
        print(f"wrote {PACK.relative_to(ROOT)}")
        return 0

    if not PACK.is_file():
        print(
            f"ERROR: {PACK.relative_to(ROOT)} is missing — run "
            "`python scripts/gen_skill_eval_pack.py`",
            file=sys.stderr,
        )
        return 1
    committed = PACK.read_text(encoding="utf-8")
    if committed != rendered:
        print(
            f"ERROR: {PACK.relative_to(ROOT)} is out of date (or was hand-edited).\n"
            "       Run `python scripts/gen_skill_eval_pack.py` and commit the result.",
            file=sys.stderr,
        )
        for line in describe_drift(committed, rendered):
            print(f"       {line}", file=sys.stderr)
        return 1
    print(f"{PACK.relative_to(ROOT)} is up to date")
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry
    raise SystemExit(main())
