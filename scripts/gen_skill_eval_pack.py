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
  abicheck_surface           the surface the skills actually consume: the CLI
                             command/option tree, the report schema version, and
                             the ChangeKind verdict mapping. Deliberately narrow
                             — hashing all of abicheck/ would invalidate every
                             bundle on every source commit
  build                      publication only: abicheck/ + pyproject.toml +
                             resolved runtime dependency versions. abicheck/
                             alone is not the build

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
TRIGGER_CORPUS = ROOT / "tests" / "agent_skills" / "trigger_corpus.yaml"
GROUND_TRUTH = ROOT / "examples" / "ground_truth.json"
EXAMPLES = ROOT / "examples"
PACK = EVAL_DIR / "skill-eval-pack.json"

#: Bumped when the pack's own shape changes, so a consumer reading an older
#: shape fails loudly rather than misreading a field.
PACK_VERSION = 1

#: Routing declarations for the consumed-surface hash. The digest itself is
#: computed from the *live* command tree, report schema and verdict mapping
#: (`_surface_digest`), not from these files' bytes — hashing whole modules
#: would move the hash on every unrelated edit to `cli.py`, which is exactly
#: the invalidate-everything behaviour D6 rejects. These paths only answer
#: "a change here may move that digest".
SURFACE_ROOTS = (
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


def _tree_files(root: Path) -> list[Path]:
    return [
        p
        for p in sorted(root.rglob("*"))
        if p.is_file() and "__pycache__" not in p.parts
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

    Computed from the live objects, the same introspection
    `tests/test_agent_skills_drift.py` and `scripts/gen_cli_reference.py`
    already do — a renamed flag, a removed command, a changed report field or a
    changed default verdict moves it, while a detector-internals commit that
    touches none of them does not. That asymmetry is the whole point (D6):
    hashing `abicheck/`'s bytes here would invalidate every committed bundle on
    every source commit and make the evaluation unrunnable. The residual — a
    verdict that changes with no surface change — is what Phase 6's
    full-build-digest pass exists to close.
    """
    import click

    from abicheck.change_registry import REGISTRY
    from abicheck.cli import main as cli_main
    from abicheck.schemas import load_compare_report_schema

    def walk(cmd: click.Command, path: tuple[str, ...] = ()) -> dict[str, list[str]]:
        options: list[str] = []
        for param in cmd.params:
            options.extend(getattr(param, "opts", []) or [])
            options.extend(getattr(param, "secondary_opts", []) or [])
        tree = {" ".join(path): sorted(set(options))}
        if isinstance(cmd, click.Group):
            for name, sub in cmd.commands.items():
                tree.update(walk(sub, (*path, name)))
        return tree

    surface = {
        "cli": walk(cli_main),
        "report_schema": load_compare_report_schema(),
        "verdicts": {
            kind: meta.default_verdict.value
            for kind, meta in sorted(REGISTRY.entries.items())
        },
    }
    return _digest(json.dumps(surface, sort_keys=True).encode())


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

    Deliberately not what the committed pack records. `abicheck/` and
    `pyproject.toml` are repository content, so their digest is the same for
    every checkout of a commit and `--check` stays deterministic; the resolved
    dependency versions are a property of the *environment*, so folding them
    into the committed value would make the pack fail its own drift check on
    any machine whose lockfile resolved differently. Publication reads this
    function at the moment it publishes, against the environment it publishes
    from, which is the only moment the question "is this evidence about the
    build being shipped" has a single answer.
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
        scenarios[scenario["id"]] = {
            "skill": scenario["skill"],
            "category": scenario["category"],
            "status": scenario["status"],
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
            # Repo-content half only; `publication_build_digest()` folds in the
            # resolved runtime dependency versions at publication time. See
            # that function for why the environment-bound half is not
            # committed.
            "build": {
                "digest": _digest_paths(_expand(BUILD_SOURCES)),
                "roots": _roots(BUILD_SOURCES),
                "affects": skills,
                "publication_only": True,
            },
        },
    }


#: The ownership marker `scripts/check_ai_readiness.py`'s
#: `generated-file-ownership` check looks for. Carried as a JSON *field*, not a
#: comment header: JSON has no comments, and a pack a consumer cannot
#: `json.load()` would defeat the point of publishing one.
GENERATED_MARKER = "generated by scripts/gen_skill_eval_pack.py — do not hand-edit"


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
    if PACK.read_text(encoding="utf-8") != rendered:
        print(
            f"ERROR: {PACK.relative_to(ROOT)} is out of date (or was hand-edited).\n"
            "       Run `python scripts/gen_skill_eval_pack.py` and commit the result.",
            file=sys.stderr,
        )
        return 1
    print(f"{PACK.relative_to(ROOT)} is up to date")
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry
    raise SystemExit(main())
