#!/usr/bin/env python3
"""Workflow-example manifests: the loader, the schema, and the README
drift check.

Phase 5 of the examples/catalog split
(docs/contribute/plans/examples-catalog-split.md). `examples/workflows/<id>/`
holds a curated, task-oriented walkthrough -- a small project plus a README
showing the real `abicheck` invocation a user would run. Before this module
nothing executed those commands: the workflow-coverage number in
`docs/contribute/catalog-coverage.md` was a count of *subdirectories*, so an
empty or broken directory raised it, and the documented commands were free to
rot with the calibration catalog's own gates all still green.

A `workflow.yaml` states what the walkthrough claims: the commands, the exit
code, and the verdict/change kinds. Two consumers share this module so they
cannot drift from each other -- `tests/test_workflow_examples.py` (fast lane,
structural, no compiler) and `validation/scripts/run_workflow_examples.py`
(really runs them).

The load-bearing rule is `readme_drift()`: every `run:` line must appear
verbatim in the workflow's own README, whitespace-normalized. Without it the
manifest is just a second copy of the walkthrough, able to keep passing
against commands the README no longer shows -- which is the exact shape of
the escapes AGENTS.md's "a bug fix's regression test targets the bug class"
section catalogues (a test that asserts a restatement rather than the real
artifact).
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

import yaml

if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
import example_catalog  # noqa: E402

WORKFLOWS_DIR = example_catalog.EXAMPLES_DIR / "workflows"
MANIFEST_NAME = "workflow.yaml"
README_NAME = "README.md"

KNOWN_PLATFORMS = frozenset({"linux", "macos", "windows"})
_STEP_KEYS = frozenset(
    {"name", "run", "expect", "json_variant", "expect_json", "allow_failure"}
)
_EXPECT_KEYS = frozenset({"exit_code", "stdout_contains", "stdout_excludes"})
_MANIFEST_KEYS = frozenset({"id", "task", "platforms", "requires", "steps"})


class ManifestError(Exception):
    """A `workflow.yaml` that does not satisfy the schema above."""


@dataclass(frozen=True)
class Step:
    name: str
    run: str
    exit_code: int | None = None
    stdout_contains: tuple[str, ...] = ()
    stdout_excludes: tuple[str, ...] = ()
    json_variant: tuple[str, ...] = ()
    expect_json: dict[str, object] = field(default_factory=dict)
    allow_failure: bool = False


@dataclass(frozen=True)
class Workflow:
    id: str
    task: str
    directory: Path
    platforms: tuple[str, ...]
    requires: tuple[str, ...]
    steps: tuple[Step, ...]

    @property
    def readme(self) -> Path:
        return self.directory / README_NAME


def workflow_dirs(root: Path | None = None) -> list[Path]:
    """Every `examples/workflows/<id>/` directory, sorted.

    A directory, not a manifest, is what this returns on purpose:
    `load_all()` failing loudly on a manifest-less directory is what stops
    an unfinished workflow from being invisible to the gate.
    """
    base = root or WORKFLOWS_DIR
    if not base.is_dir():
        return []
    return sorted(p for p in base.iterdir() if p.is_dir())


def load(directory: Path) -> Workflow:
    manifest = directory / MANIFEST_NAME
    if not manifest.is_file():
        raise ManifestError(
            f"{directory}: no {MANIFEST_NAME}. Every workflow example carries "
            "an executable contract; a directory without one would count "
            "toward workflow coverage while proving nothing."
        )
    raw = yaml.safe_load(manifest.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ManifestError(f"{manifest}: top level must be a mapping")
    unknown = sorted(set(raw) - _MANIFEST_KEYS)
    if unknown:
        raise ManifestError(f"{manifest}: unknown key(s) {unknown}")

    workflow_id = raw.get("id")
    if workflow_id != directory.name:
        raise ManifestError(
            f"{manifest}: id {workflow_id!r} must match the directory name "
            f"{directory.name!r}"
        )
    task = str(raw.get("task") or "").strip()
    if not task:
        raise ManifestError(f"{manifest}: `task` must state the user's question")

    platforms = tuple(raw.get("platforms") or ())
    if not platforms:
        raise ManifestError(f"{manifest}: `platforms` must list at least one platform")
    bad = sorted(set(platforms) - KNOWN_PLATFORMS)
    if bad:
        raise ManifestError(f"{manifest}: unknown platform(s) {bad}")

    steps_raw = raw.get("steps") or []
    if not steps_raw:
        raise ManifestError(f"{manifest}: `steps` must list at least one command")
    steps: list[Step] = []
    seen: set[str] = set()
    for index, entry in enumerate(steps_raw):
        if not isinstance(entry, dict):
            raise ManifestError(f"{manifest}: step {index} must be a mapping")
        unknown = sorted(set(entry) - _STEP_KEYS)
        if unknown:
            raise ManifestError(
                f"{manifest}: step {index} has unknown key(s) {unknown}"
            )
        name = str(entry.get("name") or "").strip()
        if not name:
            raise ManifestError(f"{manifest}: step {index} needs a `name`")
        if name in seen:
            raise ManifestError(f"{manifest}: duplicate step name {name!r}")
        seen.add(name)
        command = str(entry.get("run") or "").strip()
        if not command:
            raise ManifestError(f"{manifest}: step {name!r} needs a `run` command")
        expect = entry.get("expect") or {}
        if not isinstance(expect, dict):
            raise ManifestError(
                f"{manifest}: step {name!r}: `expect` must be a mapping"
            )
        unknown = sorted(set(expect) - _EXPECT_KEYS)
        if unknown:
            raise ManifestError(
                f"{manifest}: step {name!r}: unknown expect key(s) {unknown}"
            )
        expect_json = entry.get("expect_json") or {}
        json_variant = tuple(entry.get("json_variant") or ())
        if expect_json and not json_variant:
            raise ManifestError(
                f"{manifest}: step {name!r} declares `expect_json` with no "
                "`json_variant` to produce the JSON it would check"
            )
        steps.append(
            Step(
                name=name,
                run=command,
                exit_code=expect.get("exit_code"),
                stdout_contains=tuple(expect.get("stdout_contains") or ()),
                stdout_excludes=tuple(expect.get("stdout_excludes") or ()),
                json_variant=json_variant,
                expect_json=dict(expect_json),
                allow_failure=bool(entry.get("allow_failure", False)),
            )
        )

    return Workflow(
        id=str(workflow_id),
        task=task,
        directory=directory,
        platforms=platforms,
        requires=tuple(raw.get("requires") or ()),
        steps=tuple(steps),
    )


def load_all(root: Path | None = None) -> list[Workflow]:
    return [load(d) for d in workflow_dirs(root)]


def normalize_command(text: str) -> str:
    """Collapse shell line continuations and runs of whitespace.

    A README shows a long invocation wrapped over several lines with
    trailing backslashes; the manifest states it as one string. Comparing
    them verbatim would fail on formatting alone, which is not the drift
    anyone cares about.
    """
    return " ".join(text.replace("\\\n", " ").split())


def readme_drift(workflow: Workflow) -> list[str]:
    """Return one message per `run:` command the README does not show."""
    if not workflow.readme.is_file():
        return [f"{workflow.directory}: no {README_NAME}"]
    documented = normalize_command(workflow.readme.read_text(encoding="utf-8"))
    missing = []
    for step in workflow.steps:
        if normalize_command(step.run) not in documented:
            missing.append(
                f"{workflow.id}: step {step.name!r} runs a command the README "
                f"does not show: {normalize_command(step.run)!r}"
            )
    return missing
