"""No user-visible text or current doc names an option spelling ADR-091 removed.

ADR-091 D7 deletes a renamed CLI option, config key or typed-API keyword
outright, and turns a renamed GitHub Action input into a fail-closed
tombstone. A help string, report heading, error hint or how-to page that
still spells the old name tells a reader to type something that exits 64 or
fails the run. The banned spellings are not a hand list: they come from
``architecture/option-vocabulary.yaml`` --

- ``removed:`` -- every deleted CLI/config/Python spelling, with the regular
  expression that finds it (``match``) and the live row that replaced it;
- ``replacements:`` -- every retired (tombstoned) Action and reusable-workflow
  input, checked as a ``with:`` key of a step that ``uses:`` that action.

Scanned: every non-docstring string literal under ``abicheck/`` and the real
Click help tree (what ``--help`` and errors print); the Action layer's
non-comment lines (``action.yml``, ``actions/*/action.yml``, their shell
scripts, the reusable workflows); and the docs a reader follows as current
instructions (``docs/`` outside the historical trees below, ``README.md``,
every ``AGENTS.md``/``CLAUDE.md``, ``examples/``, ``skills-src/``).

Not scanned, because recording the old spelling is their job: ADRs, plans,
the archive, the ``upgrading-*`` migration pages, pages whose front matter
says ``lifecycle: historical`` or ``migration``, and the changelog. A message
that rejects an old spelling must name it; those few sites are in
``ALLOWED`` with the reason.
"""

from __future__ import annotations

import ast
import functools
import re
from collections.abc import Iterator
from pathlib import Path

import _yaml_fast
import click
import pytest
import yaml

pytestmark = pytest.mark.repo_scan

REPO = Path(__file__).resolve().parent.parent
REGISTRY = REPO / "architecture" / "option-vocabulary.yaml"
INVENTORY = REPO / "architecture" / "option-vocabulary-inventory.json"
PACKAGE = REPO / "abicheck"

#: Docs trees that record history; an old spelling there is the record.
HISTORICAL_PREFIXES: tuple[str, ...] = (
    "docs/contribute/adr/",
    "docs/contribute/plans/",
    "docs/contribute/archive/",
    "docs/start/upgrading-",
)

#: (repo-relative path, removed spelling) -> why the old spelling must stay.
ALLOWED: dict[tuple[str, str], str] = {
    (
        "docs/use/python-api.md",
        "run_compare(policy_file_path)",
    ): "documents load_suppression_and_policy(), which keeps its own policy_file_path keyword",
    (
        "docs/reference/compatibility-evaluation-config.md",
        "run_compare(contract_mode)",
    ): "ExplicitCompatibilityInputs.contract_mode is an internal input, not the typed-request keyword",
}


@functools.cache
def _registry() -> dict:
    return _yaml_fast.safe_load(REGISTRY.read_text(encoding="utf-8"))


@functools.cache
def _removed() -> tuple[tuple[str, re.Pattern[str]], ...]:
    return tuple(
        (r["spelling"], re.compile(r["match"])) for r in _registry()["removed"]
    )


def _hits(rel: str, text: str, line: int = 0) -> Iterator[str]:
    for spelling, rx in _removed():
        if (rel, spelling) in ALLOWED:
            continue
        for m in rx.finditer(text):
            at = line or text.count("\n", 0, m.start()) + 1
            yield f"{rel}:{at}: {m.group(0)!r} (removed {spelling})"


# ── registry integrity ────────────────────────────────────────────────────────


def test_removed_entries_are_dead_and_replaced_by_live_rows() -> None:
    import json

    inv = json.loads(INVENTORY.read_text(encoding="utf-8"))
    live = {
        row["id"] for rows in inv.values() if isinstance(rows, list) for row in rows
    }
    entries = _registry()["removed"]
    assert entries, "vacuity: the registry lists no removed spellings"
    for entry in entries:
        assert entry["spelling"] not in live, (
            f"{entry['spelling']} is still a live option"
        )
        assert entry["replaced_by"] in live, (
            f"{entry['replaced_by']} is not a live option"
        )
        assert entry["surface"] in {"cli", "config", "python"}, entry


def test_each_match_finds_its_own_spelling_and_spares_the_replacement() -> None:
    samples = {
        "compare --used-by-manifest": "abicheck compare a b --used-by-manifest m.json",
        "compare --required-symbol": "abicheck compare a b --required-symbol foo",
        "compare --view": "abicheck compare a b --view show=breaking",
        "compare --version": "abicheck compare a b --version old=1.0",
        "dump --version": "abicheck dump lib.so --version 1.0",
        "project history --version": "abicheck project history --version 2.0",
        "compare --abi3": "abicheck compare a b --abi3 cp312",
        "deps compare --old-root": "abicheck deps compare a b --old-root /r",
        "deps compare --new-root": "abicheck deps compare a b --new-root /r",
        "deps tree --sysroot": "abicheck deps tree lib.so --sysroot /r",
        "acknowledgment": "acknowledgment:\n  file: acks.yml",
        "source.method": "set source.method: git",
        "run_compare(old_version)": "run_compare(a, b, old_version='1')",
        "run_compare(new_version)": "run_compare(a, b, new_version='2')",
        "run_compare(policy_file_path)": "run_compare(a, b, policy_file_path=p)",
        "run_compare(acknowledgments_path)": "run_compare(a, b, acknowledgments_path=p)",
        "run_compare(force_public_symbols)": "run_compare(a, b, force_public_symbols=s)",
        "run_compare(enable_debuginfod)": "run_compare(a, b, enable_debuginfod=True)",
        "CompareRequest.follow_dependencies": "CompareRequest(follow_dependencies=True)",
        "run_compare(contract_mode)": "run_compare(a, b, contract_mode='public')",
        "run_dump(header_backend)": "run_dump(p, header_backend='clang')",
    }
    live = (
        "abicheck --version; abicheck compare a b --label old=1 --abi3-floor cp312 "
        "--used-by app --root old=/r; deps tree lib.so --root /r; "
        "acknowledgments:\n  file: a.yml\nsources.method: git; output.view: "
        "[show=breaking]; consumers.required_symbols: [foo]; "
        "run_compare(a, b, old_label='1', policy=p, acknowledgments=p, "
        "public_symbols=s, debuginfod=True, contract='public'); "
        "run_dump(p, frontend='clang', version='1')"
    )
    spellings = {s for s, _ in _removed()}
    assert spellings == set(samples), "every removed entry needs a sample here"
    for spelling, rx in _removed():
        assert rx.search(samples[spelling]), (
            f"{spelling}: match misses its own spelling"
        )
        assert not rx.search(live), f"{spelling}: match also hits a live spelling"


def test_allowlist_entries_are_still_needed() -> None:
    known = {s for s, _ in _removed()}
    for (rel, spelling), reason in ALLOWED.items():
        assert reason
        assert spelling in known, f"stale allowlist spelling: {spelling}"
        path = REPO / rel
        assert path.is_file(), f"stale allowlist path: {rel}"
        rx = dict(_removed())[spelling]
        assert rx.search(path.read_text(encoding="utf-8")), (
            f"{rel} no longer names {spelling}; drop its ALLOWED entry"
        )


# ── abicheck/: what --help, errors and reports print ──────────────────────────


def _string_literals(path: Path) -> Iterator[tuple[int, str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings = {
        id(node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
    }
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in docstrings
        ):
            yield node.lineno, node.value


def test_no_removed_spelling_in_package_string_literals() -> None:
    hits: list[str] = []
    for path in sorted(PACKAGE.rglob("*.py")):
        rel = path.relative_to(REPO).as_posix()
        for line, value in _string_literals(path):
            hits.extend(_hits(rel, value, line))
    assert not hits, "removed option spelling in user-visible text:\n" + "\n".join(hits)


def _walk(cmd: click.Command, path: str) -> Iterator[tuple[str, click.Command]]:
    yield path, cmd
    if isinstance(cmd, click.Group):
        for name, sub in cmd.commands.items():
            yield from _walk(sub, f"{path} {name}")


def test_no_removed_spelling_in_click_help() -> None:
    from abicheck.cli import main

    hits: list[str] = []
    commands = list(_walk(main, "abicheck"))
    assert len(commands) > 10, "vacuity: the command walk found no subcommands"
    for path, cmd in commands:
        texts = [cmd.help, cmd.short_help, cmd.epilog]
        texts += [getattr(p, "help", None) for p in cmd.params]
        for text in filter(None, texts):
            hits.extend(_hits(f"<{path} --help>", text, 1))
    assert not hits, "removed option spelling in --help:\n" + "\n".join(hits)


# ── the Action layer ──────────────────────────────────────────────────────────


def _reusable_workflows() -> list[Path]:
    out = []
    for path in sorted((REPO / ".github" / "workflows").glob("*.yml")):
        on = _yaml_fast.safe_load(path.read_text(encoding="utf-8")).get(True, {})
        if isinstance(on, dict) and "workflow_call" in on:
            out.append(path)
    return out


def _action_files() -> list[Path]:
    files = [REPO / "action.yml", *sorted((REPO / "actions").glob("*/action.yml"))]
    files += sorted((REPO / "action").glob("*.sh"))
    files += sorted((REPO / "actions").rglob("*.sh"))
    return files + _reusable_workflows()


def test_no_removed_spelling_in_action_layer() -> None:
    files = _action_files()
    assert len(files) > 5, "vacuity: no Action files found"
    hits: list[str] = []
    for path in files:
        rel = path.relative_to(REPO).as_posix()
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if line.lstrip().startswith("#"):
                continue
            hits.extend(_hits(rel, line, n))
    assert not hits, "removed option spelling in the Action layer:\n" + "\n".join(hits)


# ── current docs ─────────────────────────────────────────────────────────────


_FRONT_MATTER = re.compile(r"\A---\n(.*?)\n---\n", re.S)


def _is_historical(rel: str, text: str) -> bool:
    if rel.startswith(HISTORICAL_PREFIXES):
        return True
    fm = _FRONT_MATTER.match(text)
    if fm:
        meta = yaml.safe_load(fm.group(1)) or {}
        if isinstance(meta, dict) and meta.get("lifecycle") in {
            "historical",
            "migration",
        }:
            return True
    return False


def _current_docs() -> list[tuple[str, str]]:
    paths = set((REPO / "docs").rglob("*.md"))
    paths |= set((REPO / "examples").rglob("*.md"))
    paths |= set((REPO / "examples").rglob("*.y*ml"))
    paths |= set((REPO / "skills-src").rglob("*.md"))
    paths |= {REPO / "README.md"}
    for name in ("AGENTS.md", "CLAUDE.md"):
        paths |= {
            p
            for p in REPO.rglob(name)
            if not any(part.startswith(".") for part in p.relative_to(REPO).parts)
        }
    out = []
    for path in sorted(paths):
        rel = path.relative_to(REPO).as_posix()
        text = path.read_text(encoding="utf-8")
        if not _is_historical(rel, text):
            out.append((rel, text))
    return out


def test_no_removed_spelling_in_current_docs() -> None:
    docs = _current_docs()
    assert len(docs) > 100, "vacuity: the docs walk found too few pages"
    hits: list[str] = []
    for rel, text in docs:
        hits.extend(_hits(rel, text))
    assert not hits, "removed option spelling in a current doc:\n" + "\n".join(hits)


# ── retired Action inputs used as `with:` keys ────────────────────────────────


def _retired_inputs() -> dict[str, set[str]]:
    """``{uses-suffix: {retired input, ...}}`` from ``replacements:``."""
    out: dict[str, set[str]] = {}
    for entry in _registry()["replacements"]:
        if entry["status"] != "retired":
            continue
        rid = entry["id"]
        if rid.startswith("action:"):
            owner = "abicheck/abicheck"
        elif rid.startswith("workflow:"):
            owner = "/.github/workflows/" + rid.split(":")[1]
        else:
            owner = "/" + rid.split(":")[0]
        out.setdefault(owner, set()).add(rid.rsplit(":", 1)[1])
    return out


def _owner(uses: str) -> str | None:
    ref = uses.split("@", 1)[0].rstrip("/")
    if "/.github/workflows/" in ref:
        return "/.github/workflows/" + ref.rsplit("/", 1)[1]
    if "/actions/" in ref or ref.startswith("./actions/"):
        return "/actions/" + ref.rsplit("/", 1)[1]
    if ref in {"abicheck/abicheck", "."} or ref.endswith("/abicheck"):
        return "abicheck/abicheck"
    return None


def _steps(node: object) -> Iterator[dict]:
    if isinstance(node, dict):
        if isinstance(node.get("uses"), str) and isinstance(node.get("with"), dict):
            yield node
        for value in node.values():
            yield from _steps(value)
    elif isinstance(node, list):
        for value in node:
            yield from _steps(value)


_FENCE = re.compile(r"^```ya?ml[^\n]*\n(.*?)^```", re.S | re.M)


def _yaml_documents() -> Iterator[tuple[str, object]]:
    for rel, text in _current_docs():
        chunks = [text] if rel.endswith((".yml", ".yaml")) else _FENCE.findall(text)
        for chunk in chunks:
            try:
                yield rel, yaml.safe_load(chunk)
            except yaml.YAMLError:
                continue
    for path in sorted((REPO / ".github" / "workflows").glob("*.yml")):
        yield (
            path.relative_to(REPO).as_posix(),
            _yaml_fast.safe_load(path.read_text(encoding="utf-8")),
        )


def test_no_retired_action_input_in_a_documented_step() -> None:
    retired = _retired_inputs()
    assert retired.get("abicheck/abicheck"), "vacuity: no retired root Action inputs"
    hits: list[str] = []
    steps = 0
    for rel, doc in _yaml_documents():
        for step in _steps(doc):
            owner = _owner(step["uses"])
            if owner is None:
                continue
            steps += 1
            for key in step["with"]:
                if key in retired.get(owner, set()):
                    hits.append(f"{rel}: {step['uses']} with: {key} (retired input)")
    assert steps > 10, "vacuity: found no abicheck Action steps in docs/workflows"
    assert not hits, "retired Action input in a documented step:\n" + "\n".join(hits)


def test_owner_resolution() -> None:
    assert _owner("abicheck/abicheck@v1") == "abicheck/abicheck"
    assert _owner("./") is None or _owner("./") == "abicheck/abicheck"
    assert (
        _owner("abicheck/abicheck/actions/check-target@v1") == "/actions/check-target"
    )
    assert (
        _owner("abicheck/abicheck/.github/workflows/check-single.yml@v1")
        == "/.github/workflows/check-single.yml"
    )
    assert _owner("actions/checkout@v4") is None
