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

import re
import shlex
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

# A `run:` command is executed with `shell=False` after `shlex.split`, so no
# shell ever interprets it. That is a deliberate choice, not an oversight: a
# committed manifest is repository-authored, but "repository-authored" is
# exactly the trust level `check_ai_readiness.py`'s own banned-imports gate
# declines to grant `subprocess(..., shell=True)` anywhere under `abicheck/`,
# and there is no reason for this runner to hold itself to a weaker bar. A
# command that genuinely needs a pipe, a redirect or a variable is rejected
# here rather than silently mis-executed as a literal argument -- if a
# workflow ever needs one, that is a deliberate decision to make then.
_SHELL_METACHARACTERS = re.compile(r"[|&;<>$`(){}\[\]*?~\n]|\|\|")


class ManifestError(Exception):
    """A `workflow.yaml` that does not satisfy the schema above."""


def _string_list(value: object, where: str) -> tuple[str, ...]:
    """Coerce a YAML sequence-of-strings field, rejecting a bare scalar.

    A bare string must never be accepted here. `tuple("BREAKING")` is eight
    one-character assertions, every one of which `"BRAKEING"` satisfies --
    so the common YAML slip `stdout_contains: BREAKING` (no `- `) would
    silently turn a real output check into one that passes on text missing
    the required word entirely. Normalizing the scalar to a one-item tuple
    would also work, but rejecting says so out loud rather than quietly
    accepting two spellings of the same field.
    """
    if value is None:
        return ()
    if isinstance(value, str):
        raise ManifestError(
            f"{where} must be a list of strings, not the bare string "
            f"{value!r} -- a scalar here becomes one assertion per character. "
            f"Write it as a YAML sequence (`- {value}`)."
        )
    if not isinstance(value, (list, tuple)):
        raise ManifestError(
            f"{where} must be a list of strings, got {type(value).__name__}"
        )
    for item in value:
        if not isinstance(item, str):
            raise ManifestError(
                f"{where} must contain only strings; got {item!r} "
                f"({type(item).__name__})"
            )
    return tuple(value)


@dataclass(frozen=True)
class Step:
    name: str
    run: str
    argv: tuple[str, ...] = ()
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

    platforms = _string_list(raw.get("platforms"), f"{manifest}: `platforms`")
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
        metacharacter = _SHELL_METACHARACTERS.search(command)
        if metacharacter:
            raise ManifestError(
                f"{manifest}: step {name!r} contains the shell metacharacter "
                f"{metacharacter.group()!r}. Commands run with shell=False; "
                "see _SHELL_METACHARACTERS."
            )
        try:
            argv = shlex.split(command)
        except ValueError as exc:
            raise ManifestError(
                f"{manifest}: step {name!r}: cannot parse `run` ({exc})"
            ) from exc
        if not argv:
            raise ManifestError(f"{manifest}: step {name!r}: `run` parses to nothing")
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
        json_variant = _string_list(
            entry.get("json_variant"), f"{manifest}: step {name!r}: `json_variant`"
        )
        if expect_json and not json_variant:
            raise ManifestError(
                f"{manifest}: step {name!r} declares `expect_json` with no "
                "`json_variant` to produce the JSON it would check"
            )
        steps.append(
            Step(
                name=name,
                run=command,
                argv=tuple(argv),
                exit_code=expect.get("exit_code"),
                stdout_contains=_string_list(
                    expect.get("stdout_contains"),
                    f"{manifest}: step {name!r}: `expect.stdout_contains`",
                ),
                stdout_excludes=_string_list(
                    expect.get("stdout_excludes"),
                    f"{manifest}: step {name!r}: `expect.stdout_excludes`",
                ),
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
        requires=_string_list(raw.get("requires"), f"{manifest}: `requires`"),
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


# The language tag is required, and the fence must start a line. An optional
# tag lets the regex pair a *closing* fence with a later opening one and
# capture the prose between them -- which it did, silently, on the first cut.
_FENCE_RE = re.compile(
    r"^[ \t>]*```(?:bash|sh|shell|console)[ \t]*\n(.*?)^[ \t>]*```",
    re.DOTALL | re.MULTILINE,
)


def documented_commands(text: str) -> list[str]:
    """Every shell command a Markdown document shows, one normalized line each.

    Deliberately whole *lines*, not the document as one string. Substring
    matching would accept a manifest command that the README has since
    extended: `abicheck compare old.so new.so` is a substring of
    `abicheck compare old.so new.so --contract public`, so CI would keep
    running a command different from the one a reader copies, with the drift
    check reporting nothing -- the exact failure the check exists to catch.
    """
    commands: list[str] = []
    for block in _FENCE_RE.findall(text):
        # A fence inside a blockquote carries a "> " prefix on every line.
        unquoted = "\n".join(
            line[2:] if line.startswith("> ") else line.removeprefix(">")
            for line in block.splitlines()
        )
        # Join shell line continuations before splitting into lines, so a
        # wrapped invocation is one command rather than several fragments.
        for line in unquoted.replace("\\\n", " ").splitlines():
            line = line.split("#", 1)[0].strip().removeprefix("$ ").strip()
            if line:
                commands.append(normalize_command(line))
    return commands


def readme_drift(workflow: Workflow) -> list[str]:
    """Return one message per `run:` command the README does not show."""
    if not workflow.readme.is_file():
        return [f"{workflow.directory}: no {README_NAME}"]
    documented = documented_commands(workflow.readme.read_text(encoding="utf-8"))
    missing = []
    for step in workflow.steps:
        wanted = normalize_command(step.run)
        if wanted not in documented:
            missing.append(
                f"{workflow.id}: step {step.name!r} runs a command the README "
                f"does not show verbatim: {wanted!r}"
            )
    return missing
