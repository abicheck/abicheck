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
Two hashes are deliberately **absent** from the pack, both because they are
derived from files other pull requests change — committing them would tax every
such PR with a regeneration and, worse, let one merge cleanly into a `main`
whose pack is now stale (git sees no conflict when the two touch different
files). Neither loses any invalidation: a bundle records what it ran against
and the checker recomputes the current value.

  abicheck_surface           `scripts/skill_eval_surface.py` owns it; the
                             freshness checker computes it at check time
  build                      publication only, and environment-bound:
                             `publication_build_digest()` folds the resolved
                             runtime dependency versions in on demand

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
from collections.abc import Callable
from pathlib import Path
from typing import Any

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))

# Phase 3 resolver (scripts/CLAUDE.md, docs/contribute/plans/examples-catalog-split.md).
import example_catalog  # noqa: E402
from skill_eval_surface import normalize_newlines  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
# The *generated* tree, deliberately not `skills-src/` — see `_skill_tree_digest`.
PUBLISHED_SKILLS = ROOT / ".agents" / "skills"
EVAL_DIR = ROOT / "agent-evals" / "skills"
SCENARIOS = EVAL_DIR / "scenarios.yaml"
RUBRIC = EVAL_DIR / "rubric.yaml"
TRIGGER_CORPUS = ROOT / "tests" / "agent_skills" / "trigger_corpus.yaml"
GROUND_TRUTH = example_catalog.GROUND_TRUTH_PATH
PACK = EVAL_DIR / "skill-eval-pack.json"

#: Bumped when the pack's own shape changes, so a consumer reading an older
#: shape fails loudly rather than misreading a field. Bumped to 2 when
#: Category B scenarios gained an `architectures` restriction field
#: alongside the existing `platforms` one (Codex review, PR #808) — a
#: version-1 consumer has no way to know the new axis exists, and silently
#: ignoring it would run an architecture-restricted fixture (e.g. the
#: prebuilt x86_64-only `evidence-too-shallow` binary) on an unsupported
#: host, exactly the corrupted comparison the restriction exists to
#: prevent.
PACK_VERSION = 2

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
    return "sha256:" + hashlib.sha256(normalize_newlines(data)).hexdigest()


def _digest_paths(paths: list[Path]) -> str:
    """Digest a set of files as one value, path-ordered so it is stable."""
    h = hashlib.sha256()
    for path in sorted(paths, key=lambda p: p.relative_to(ROOT).as_posix()):
        h.update(path.relative_to(ROOT).as_posix().encode())
        h.update(b"\0")
        h.update(normalize_newlines(path.read_bytes()))
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
        # `.egg-info` is a *directory*; its files are PKG-INFO, SOURCES.txt…,
        # so a name-suffix test matched nothing and hashed all of it.
        and not any(part.endswith(".egg-info") for part in p.parts)
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


def _fixture_paths(
    scenario: dict[str, Any],
    *,
    case_dir: Callable[[str], Path] = example_catalog.case_dir,
) -> list[Path]:
    """Every file whose content the scenario feeds the agent.

    Category A resolves to the examples/ case directory plus that case's own
    ground-truth entry; Category B to its declared fixture tree. A scenario
    whose inputs change must go stale, so the closure is hashed, not the path.

    *case_dir* resolves a case name to its on-disk directory -- production
    callers use the default (``example_catalog.case_dir``, Phase 3,
    docs/contribute/plans/examples-catalog-split.md), so Phase 4's directory
    split needs no change here; a test injects its own resolver instead of
    monkeypatching a flat root, mirroring ``fixture_sync.sync_fixtures``'s
    identical parameter.
    """
    if scenario["category"] == "A":
        resolved = case_dir(scenario["case"])
        return _tree_files(resolved) if resolved.is_dir() else []
    fixture = ROOT / scenario["fixture"]
    return _tree_files(fixture) if fixture.is_dir() else []


def _scenario_digest(
    scenario: dict[str, Any],
    ground_truth: dict[str, Any],
    *,
    case_dir: Callable[[str], Path] = example_catalog.case_dir,
) -> str:
    h = hashlib.sha256()
    h.update(json.dumps(scenario, sort_keys=True).encode())
    h.update(b"\0")
    for path in sorted(
        _fixture_paths(scenario, case_dir=case_dir),
        key=lambda p: p.relative_to(ROOT).as_posix(),
    ):
        h.update(path.relative_to(ROOT).as_posix().encode())
        h.update(b"\0")
        h.update(normalize_newlines(path.read_bytes()))
        h.update(b"\0")
    if scenario["category"] == "A":
        entry = ground_truth.get("verdicts", {}).get(scenario["case"], {})
        h.update(json.dumps(entry, sort_keys=True).encode())
    return "sha256:" + h.hexdigest()


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
        spec, _, marker = requirement.partition(";")
        # Only `extra ==` is out of scope — that one is installed on demand.
        # An environment marker (`python_version`, `sys_platform`) still gates a
        # real runtime dependency, whose resolved version must move the digest.
        if "extra" in marker:
            continue
        name = re.split(r"[\s<>=!~\[(]", spec, maxsplit=1)[0].strip()
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


def _trigger_prompt_ids() -> list[str]:
    """The id of every prompt in the trigger corpus, `<set>-<index>`.

    The corpus names its prompts positionally rather than carrying an `id`
    field, so this is where that convention is defined — the freshness check
    cannot read the YAML itself (stdlib only) and needs somewhere to resolve a
    trigger bundle's `scenario_id` against, exactly as a behavioral bundle
    resolves against `scenarios`. Without it an invented id such as
    `positive-99` produced activation evidence that passed every check and
    could never be aggregated against an expected trigger.

    Positional ids are safe here precisely because the corpus digest is already
    a pack entry affecting every skill: reordering the corpus renames ids *and*
    invalidates every trigger bundle in the same edit, so an id can never
    quietly come to mean a different prompt.
    """
    corpus = yaml.safe_load(TRIGGER_CORPUS.read_text(encoding="utf-8"))
    return [
        f"{group}-{index}"
        for group in ("positive", "negative")
        for index, _ in enumerate(corpus.get(group) or [])
    ]


def _category_b_platforms(scenario: dict[str, Any]) -> list[str]:
    """A Category B scenario's own declared platform restriction, or `[]`
    ("unrestricted") when it declares none.

    Category B fixtures are built by this repository for the evaluation, so
    there is no catalog to derive a platform constraint from -- a scenario's
    optional `platforms` field is that fixture's equivalent of a Category A
    case's `PLATFORMS` declaration. An empty list still means "no declared
    restriction", but only when the scenario genuinely omits the field -- a
    fixture built from raw ELF mechanics and marked `platforms: [linux]`
    must not silently widen to "runs everywhere" here (Codex review): a
    non-Linux runner would then execute it, and the skill's own
    out-of-validated-scope `NOT_VERIFIED` response would be graded against a
    concrete verdict it was never meant to produce.
    """
    return list(scenario.get("platforms") or [])


def _category_b_architectures(scenario: dict[str, Any]) -> list[str]:
    """A Category B scenario's own declared CPU-architecture restriction, or
    `[]` ("unrestricted") when it declares none.

    The identical rule as `_category_b_platforms`, one axis over: `platforms`
    restricts by OS, this restricts by CPU. Needed because an OS-only
    restriction does not by itself guarantee one architecture (this
    repository's own CI runs both x86_64 and aarch64 Linux lanes) --
    unrestricted is correct for a fixture that builds both sides from source
    on whichever host runs it, but a fixture shipping a prebuilt,
    architecture-specific artifact must say so explicitly (Codex review), or
    a from-source side built on a different host architecture silently stops
    matching it.
    """
    return list(scenario.get("architectures") or [])


def build_pack() -> dict[str, Any]:
    manifest = _load_scenarios()
    ground_truth = json.loads(GROUND_TRUTH.read_text(encoding="utf-8"))
    skills = _published_skill_names()

    scenarios: dict[str, Any] = {}
    for scenario in manifest["scenarios"]:
        if scenario["category"] == "A":
            fixture_root = (
                example_catalog.case_dir(scenario["case"]).relative_to(ROOT).as_posix()
            )
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
        platforms: list[str] = _category_b_platforms(scenario)
        architectures: list[str] = _category_b_architectures(scenario)
        if scenario["category"] == "A":
            entry = ground_truth.get("verdicts", {}).get(scenario["case"], {})
            expected = {
                "verdict": entry.get("expected"),
                "kinds": entry.get("expected_kinds", []),
                "min_evidence": entry.get("min_evidence"),
                "resolved_from": "examples/ground_truth.json",
            }
            # The catalog limits several cases to specific hosts. Dropping that
            # here would let a macOS or Windows runner execute a Linux-only
            # fixture and grade the (correct) platform-specific behaviour
            # against a Linux expectation.
            platforms = entry.get("platforms", [])
            # No catalog case ships a prebuilt, architecture-specific
            # artifact -- every case builds both sides from source, which is
            # automatically architecture-consistent -- so Category A never
            # has an architecture restriction to carry.

        scenarios[scenario["id"]] = {
            "skill": scenario["skill"],
            "category": scenario["category"],
            "status": scenario["status"],
            "platforms": platforms,
            "architectures": architectures,
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
                "prompt_ids": _trigger_prompt_ids(),
            },
            "harness": {
                "digest": _digest_paths(_expand(HARNESS_SOURCES)),
                "roots": _roots(HARNESS_SOURCES),
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
    if not isinstance(committed, dict) or not isinstance(rendered, dict):
        # `[]` and `null` are valid JSON and would reach `.get` below as an
        # AttributeError rather than as the actionable drift report this
        # function exists to produce.
        return ["(pack root is not a JSON object — regenerate it)"]

    def _fingerprint(entry: Any) -> Any:
        # This runs on a possibly hand-edited pack — one of the two failures it
        # exists to explain — so it must not raise on a non-object entry.
        if isinstance(entry, dict):
            return entry.get("digest") or entry.get("tree")
        return f"(not an object: {type(entry).__name__})"

    drift: list[str] = []
    for section in ("skills", "scenarios", "shared"):
        old, new = committed.get(section, {}), rendered.get(section, {})
        if not isinstance(old, dict) or not isinstance(new, dict):
            drift.append(f"{section}: not an object")
            continue
        for key in sorted(set(old) | set(new)):
            if key not in old:
                drift.append(f"{section}.{key}: added")
            elif key not in new:
                drift.append(f"{section}.{key}: removed")
            elif old[key] != new[key]:
                drift.append(
                    f"{section}.{key}: committed {_fingerprint(old[key])}"
                    f" -> generated {_fingerprint(new[key])}"
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
