"""ADR-091 Phase 7: the generated option references name each option's counterparts.

``scripts/option_cross_refs.py`` turns ``architecture/option-vocabulary.yaml``
into the "Same option on other surfaces" cell of the CLI, Action and
config-key references. These tests read the committed registry and the
rendered tables independently, so a stale or hand-edited reference, a missing
counterpart, or a retired input shown as live fails here:

- every generated row with a registry counterpart names it, and a retired or
  deprecated input names its replacement instead of a live counterpart;
- the three reference files are in sync with their generators (``--check``);
- each ``replacements:`` target is a live registry row, and the tombstone's own
  description names it, so a replacement cannot silently drift from the text
  that tells a user what to type.
"""

from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path
from typing import Any

import pytest
import yaml

pytestmark = pytest.mark.repo_scan

REPO = Path(__file__).resolve().parent.parent
REGISTRY = REPO / "architecture" / "option-vocabulary.yaml"
INVENTORY = REPO / "architecture" / "option-vocabulary-inventory.json"
CLI_DOC = REPO / "docs" / "reference" / "cli-reference.md"
ACTION_DOC = REPO / "docs" / "reference" / "github-action-inputs.md"
CONFIG_DOC = REPO / "docs" / "reference" / "config-keys-reference.md"
COLUMN = "Same option on other surfaces"
#: Surfaces a cell lists, besides the row's own (``--set`` is handled apart).
CELL_SURFACES = ("cli", "action", "workflow", "config")


def _load(name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, REPO / "scripts" / f"{name}.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def registry() -> dict[str, Any]:
    return yaml.safe_load(REGISTRY.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def inventory() -> dict[str, Any]:
    return json.loads(INVENTORY.read_text(encoding="utf-8"))


# --- the rendered tables, parsed without the generators' code ----------------


def _cells(line: str) -> list[str]:
    return [c.strip() for c in re.split(r"(?<!\\)\|", line.strip()[1:-1])]


def _cross_rows(markdown: str, kind: str) -> list[tuple[list[str], str]]:
    """(candidate ids, cross cell) for every row of a table that has the column.

    ``kind`` says how a row's first cell becomes an inventory id: a CLI row is
    ``<command> <--flag>`` (the command is the last heading), an Action row is
    ``action:<input>``, a config row is ``<block>.<sub-key>`` or a top-level key.
    """
    rows: list[tuple[list[str], str]] = []
    command = block = None
    in_table = False
    for line in markdown.splitlines():
        if line.startswith("#"):
            title = line.lstrip("#").strip()
            command = title.strip("`")
            block = (
                title[len("### `") : -len(":`")]
                if title.startswith("### `") and title.endswith(":`")
                else None
            )
            in_table = False
            continue
        if not line.startswith("|"):
            in_table = False
            continue
        if COLUMN in line:
            in_table = True
            continue
        if not in_table or line.startswith("|---"):
            continue
        cells = _cells(line)
        first = cells[0]
        if kind == "cli":
            ids = [f"{command} {o}" for o in re.findall(r"`(--[\w-]+)`", first)]
        elif kind == "action":
            ids = [f"action:{re.sub(r'^`|`$', '', first)}"]
        else:
            name = re.sub(r"^`|`$", "", first)
            ids = [f"{block}.{name}" if block else name]
        rows.append((ids, cells[-1]))
    return rows


def _rendered(markdown: str, kind: str) -> dict[str, str]:
    """Inventory id -> cross cell, for each row (a CLI row resolves to its flag's id)."""
    out: dict[str, str] = {}
    for ids, cell in _cross_rows(markdown, kind):
        out[ids[0]] = cell
    return out


# --- expectations, read straight from the registry -----------------------------


def _owner_surface(
    registry: dict[str, Any], member: str
) -> tuple[str, dict[str, Any]] | None:
    for concept in registry["concepts"]:
        for surface in ("cli", "action", "workflow", "config", "python"):
            if member in concept.get(surface, []):
                return surface, concept
    return None


#: CLI commands that read no ``.abicheck.yml`` and take no ``--set``.
_NO_CONFIG_COMMANDS = ("deps compare ", "deps tree ")


def _expected_substrings(
    concept: dict[str, Any], own: str, member: str = ""
) -> list[str]:
    """What a row of surface ``own`` must name for its concept's counterparts."""
    wanted: list[str] = []
    no_config = own == "cli" and member.startswith(_NO_CONFIG_COMMANDS)
    for surface in CELL_SURFACES:
        if surface == own or (no_config and surface == "config"):
            continue
        for member in concept.get(surface, []):
            if member.startswith("actions/"):
                continue
            if surface == "workflow":
                _, file, name = member.split(":", 2)
                wanted += [f"`{name}`", file]
            elif surface == "action":
                wanted.append(f"`{member.split(':', 1)[1]}`")
            else:
                wanted.append(f"`{member}`")
    if concept.get("cross_refs") == "none" or concept.get("tier") == "core":
        return wanted  # a broad concept has no cell; a core key refuses --set
    if no_config:
        return wanted
    if own == "action":
        wanted += [f"`{key}=VALUE`" for key in concept.get("config", [])]
    else:
        wanted += [f"`--set {key}=VALUE`" for key in concept.get("config", [])]
    return wanted


def _rendered_docs() -> dict[str, dict[str, str]]:
    return {
        "cli": _rendered(CLI_DOC.read_text(encoding="utf-8"), "cli"),
        "action": _rendered(ACTION_DOC.read_text(encoding="utf-8"), "action"),
        "config": _rendered(CONFIG_DOC.read_text(encoding="utf-8"), "config"),
    }


# --- tests -----------------------------------------------------------------


def test_generated_references_are_in_sync_with_the_registry() -> None:
    """``--check`` for each reference: the committed file equals a fresh render."""
    cli = _load("gen_cli_reference")
    action = _load("gen_action_reference")
    config = _load("gen_config_reference")
    assert CLI_DOC.read_text(encoding="utf-8") == cli.render()
    assert ACTION_DOC.read_text(encoding="utf-8") == action.render(
        yaml.safe_load((REPO / "action.yml").read_text(encoding="utf-8"))
    )
    assert CONFIG_DOC.read_text(encoding="utf-8") == config.render()


def test_every_generated_row_names_its_counterparts(registry: dict[str, Any]) -> None:
    """A row with a registry counterpart names it; a tombstone names its replacement."""
    replacements = {r["id"] for r in registry["replacements"]}
    checked = 0
    for kind, rows in _rendered_docs().items():
        for member, cell in rows.items():
            if member in replacements:
                continue
            found = _owner_surface(registry, member)
            if found is None:
                continue  # an option with no concept; the inventory test owns that
            own, concept = found
            if concept.get("cross_refs") == "none":
                assert cell == "—", (
                    f"{member} is in a broad concept but has a cell: {cell}"
                )
                checked += 1
                continue
            for needle in _expected_substrings(concept, own, member):
                assert needle in cell, f"{member}: cell lacks {needle}: {cell}"
            checked += 1
    assert checked > 100, f"only {checked} rows checked; the references lost rows"


def test_every_visible_option_and_root_input_has_a_generated_row(
    registry: dict[str, Any], inventory: dict[str, Any]
) -> None:
    """Coverage: each visible CLI option and each root Action input is rendered."""
    cli_rendered = set(_rendered(CLI_DOC.read_text(encoding="utf-8"), "cli"))
    visible = {r["id"] for r in inventory["cli"] if not r["hidden"]}
    missing = visible - cli_rendered
    assert not missing, f"CLI options with no generated row: {sorted(missing)[:5]}"

    action_rendered = set(_rendered(ACTION_DOC.read_text(encoding="utf-8"), "action"))
    root = {r["id"] for r in inventory["action"] if r["action"] == "action"}
    assert root == action_rendered, root ^ action_rendered


def test_retired_and_deprecated_inputs_are_never_shown_as_live(
    registry: dict[str, Any], inventory: dict[str, Any]
) -> None:
    """A tombstone's cell is its replacement, labelled RETIRED or DEPRECATED."""
    status = {
        r["id"]: r["status"]
        for s in ("action", "workflow")
        for r in inventory[s]
        if r["status"] in {"retired", "deprecated"}
    }
    action_cells = _rendered(ACTION_DOC.read_text(encoding="utf-8"), "action")
    for member, st in status.items():
        if member not in action_cells:
            continue  # sub-action and workflow tombstones are not in this page
        cell = action_cells[member]
        assert f"**{st.upper()}**" in cell, f"{member}: {cell}"
        assert "Reusable workflow:" not in cell and "CLI:" not in cell, (
            f"{member} is a tombstone but its cell lists live counterparts: {cell}"
        )


def _descriptions() -> dict[str, str]:
    out: dict[str, str] = {}
    for f in [REPO / "action.yml", *sorted((REPO / "actions").glob("*/action.yml"))]:
        owner = "action" if f.parent == REPO else f"actions/{f.parent.name}"
        doc = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        for name, spec in (doc.get("inputs") or {}).items():
            out[f"{owner}:{name}"] = " ".join(
                str((spec or {}).get("description", "")).split()
            )
    for wf in ("check-single.yml", "check-project.yml"):
        doc = yaml.safe_load(
            (REPO / ".github" / "workflows" / wf).read_text(encoding="utf-8")
        )
        on = doc.get(True) or doc.get("on") or {}
        for name, spec in ((on.get("workflow_call") or {}).get("inputs") or {}).items():
            out[f"workflow:{wf}:{name}"] = " ".join(
                str((spec or {}).get("description", "")).split()
            )
    return out


def test_replacements_cover_exactly_the_retired_and_deprecated_inputs(
    registry: dict[str, Any], inventory: dict[str, Any]
) -> None:
    dead = {
        r["id"]
        for s in ("action", "workflow")
        for r in inventory[s]
        if r["status"] in {"retired", "deprecated"}
    }
    listed = [r["id"] for r in registry["replacements"]]
    assert len(listed) == len(set(listed)), "a replacement is listed twice"
    assert set(listed) == dead, (sorted(dead - set(listed)), sorted(set(listed) - dead))
    for entry in registry["replacements"]:
        assert entry["status"] == next(
            r["status"]
            for s in ("action", "workflow")
            for r in inventory[s]
            if r["id"] == entry["id"]
        ), entry


def _names(text: str, token: str) -> bool:
    """True when ``token`` appears as a whole token, not inside a longer name.

    ``header`` must not match ``old-header`` or ``headers``; ``compile.frontend``
    must not match ``compile.frontend_context``.
    """
    return (
        re.search(r"(?<![\w.-])" + re.escape(token) + r"(?![\w.-])", text) is not None
    )


def test_each_replacement_is_a_live_row_its_tombstone_names(
    registry: dict[str, Any],
) -> None:
    descriptions = _descriptions()
    owners = {
        m: c
        for c in registry["concepts"]
        for s in ("cli", "action", "workflow", "config", "python")
        for m in c.get(s, [])
    }
    for entry in registry["replacements"]:
        target = entry["replaced_by"]
        if target is None:
            assert entry.get("reason"), (
                f"{entry['id']} has no replacement and no reason"
            )
            continue
        concept = owners.get(target)
        assert concept is not None, f"{entry['id']} -> {target}: not a registry row"
        assert concept["tier"] != "retired", (
            f"{entry['id']} -> {target}: the target is itself retired"
        )
        leaf = target if "." in target and ":" not in target else target.split(":")[-1]
        text = descriptions[entry["id"]]
        assert _names(text, leaf), (
            f"{entry['id']}'s description does not name {leaf!r}: {text[:160]}"
        )


# --- the cell's own mechanism, on a synthetic registry ----------------------


def _cross_refs(registry: dict[str, Any]):
    return _load("option_cross_refs").CrossRefs(registry)


def test_cell_excludes_its_own_surface_and_adds_set_for_a_config_key() -> None:
    reg = {
        "concepts": [
            {
                "concept": "compile.include_dirs",
                "tier": "config",
                "cli": ["compare --include"],
                "action": ["action:include", "actions/check-target:include"],
                "workflow": ["workflow:check-single.yml:include"],
                "config": ["compile.include_dirs"],
            }
        ],
        "replacements": [],
    }
    refs = _cross_refs(reg)
    assert refs.cell("compare --include", own="cli") == (
        "Action: `include`; Reusable workflow: `include` (check-single.yml); "
        ".abicheck.yml: `compile.include_dirs`; --set: `--set compile.include_dirs=VALUE`"
    )
    assert refs.cell("compile.include_dirs", own="config") == (
        "CLI: `compare --include`; Action: `include`; Reusable workflow: `include` (check-single.yml); "
        "--set: `--set compile.include_dirs=VALUE`"
    )


def test_an_action_row_spells_the_override_as_its_own_set_input() -> None:
    """An Action row names the Action's ``set:`` input, never the CLI's ``--set``."""
    reg = {
        "concepts": [
            {
                "concept": "compile.include_dirs",
                "tier": "config",
                "cli": ["compare --include"],
                "action": ["action:include"],
                "workflow": ["workflow:check-single.yml:include"],
                "config": ["compile.include_dirs"],
            }
        ],
        "replacements": [],
    }
    assert _cross_refs(reg).cell("action:include", own="action") == (
        "CLI: `compare --include`; Reusable workflow: `include` (check-single.yml); "
        ".abicheck.yml: `compile.include_dirs`; set: `compile.include_dirs=VALUE`"
    )


def test_a_core_concept_without_a_config_key_has_no_set_form() -> None:
    reg = {
        "concepts": [
            {
                "concept": "depth",
                "tier": "core",
                "cli": ["compare --depth"],
                "action": ["action:depth"],
            }
        ],
        "replacements": [],
    }
    assert _cross_refs(reg).cell("compare --depth", own="cli") == "Action: `depth`"


def test_a_tombstone_reads_as_retired_with_its_replacement() -> None:
    reg = {
        "concepts": [
            {
                "concept": "compile.frontend",
                "tier": "config",
                "config": ["compile.frontend"],
            }
        ],
        "replacements": [
            {
                "id": "action:frontend",
                "status": "retired",
                "replaced_by": "compile.frontend",
            },
            {
                "id": "action:jobs",
                "status": "retired",
                "replaced_by": None,
                "reason": "auto-detected",
            },
        ],
    }
    refs = _cross_refs(reg)
    assert refs.cell("action:frontend", own="action") == (
        "**RETIRED**, use the `.abicheck.yml` key `compile.frontend` "
        "(or `set: compile.frontend=VALUE` on the Action)"
    )
    assert (
        refs.cell("action:jobs", own="action")
        == "**RETIRED**, no replacement: auto-detected"
    )


def test_an_unclaimed_row_renders_a_dash() -> None:
    assert (
        _cross_refs({"concepts": [], "replacements": []}).cell("compare --x", own="cli")
        == "—"
    )


def test_sub_action_plumbing_is_not_listed_as_a_public_surface() -> None:
    reg = {
        "concepts": [
            {
                "concept": "headers",
                "tier": "core",
                "action": ["action:header", "actions/check-target:header"],
            }
        ],
        "replacements": [],
    }
    assert _cross_refs(reg).cell("action:header", own="action") == "—"


def test_the_core_config_keys_are_exactly_the_keys_set_refuses(
    registry: dict[str, Any],
) -> None:
    """A core concept keeps one spelling: the keys ``--set`` refuses are exactly
    the config keys of the core-tier concepts (ADR-091 D4/D6)."""
    from abicheck.buildsource.build_config_overrides import CORE_CONFIG_FLAGS

    core_keys = {
        key
        for concept in registry["concepts"]
        if concept["tier"] == "core"
        for key in concept.get("config", [])
    }
    assert core_keys == set(CORE_CONFIG_FLAGS), core_keys ^ set(CORE_CONFIG_FLAGS)


def test_no_generated_cell_offers_set_for_a_core_key(registry: dict[str, Any]) -> None:
    """A row of a core concept never names ``--set``: the validator would refuse it."""
    for kind, rows in _rendered_docs().items():
        for member, cell in rows.items():
            found = _owner_surface(registry, member)
            if found is None:
                continue
            _, concept = found
            if concept["tier"] == "core":
                assert "--set" not in cell and "set:" not in cell, (
                    f"{kind} row {member} is core but its cell offers an override: {cell}"
                )


def test_rows_of_commands_without_a_project_config_offer_no_config_spelling() -> None:
    """``deps compare``/``deps tree`` read no ``.abicheck.yml`` and take no
    ``--set``, so their rows never offer either, even though they share a
    concept (``deps.search_paths``, ``deps.ld_library_path``) with ``compare``."""
    refs = _load("option_cross_refs").CrossRefs.load()
    for command in ("deps compare", "deps tree"):
        for flag in ("--search-path", "--ld-library-path"):
            cell = refs.cell(f"{command} {flag}", "cli")
            assert ".abicheck.yml" not in cell and "--set" not in cell, cell
            assert "Action:" in cell
    shared = refs.cell("compare --search-path", "cli")
    assert ".abicheck.yml: `deps.search_paths`" in shared
    assert "--set deps.search_paths=VALUE" in shared
