#!/usr/bin/env python3
"""The "same option on the other surfaces" cell of the generated option references.

ADR-091 Phase 7. ``architecture/option-vocabulary.yaml`` says which inventory
rows are one concept; this module turns that into the cross-surface cell each
generator (``gen_cli_reference.py``, ``gen_action_reference.py``,
``gen_config_reference.py``) writes into its option table. Nothing here is
hand-typed per option: the cell is read from the registry's concept membership
and the ``replacements:`` block that records where a retired Action or workflow
input now lives.

Surfaces shown, in column order: CLI flag, Action input (root ``action.yml``),
reusable-workflow input, ``.abicheck.yml`` key, and the ``--set PATH=VALUE``
spelling of that key. A generator's own surface is not repeated in its own
cell: a CLI row names the Action input, not the CLI flag again. ``--set`` is
the escape hatch for a config key, not the primary spelling, and a core
concept (no config key) has no ``--set`` form (ADR-091 D4/D6).

``actions/*`` sub-action inputs (the composite ``check-target`` plumbing the
reusable workflows call) are not a public surface, so they are not listed.

A retired or deprecated Action or workflow input gets no live counterpart: its
cell says ``RETIRED`` (or ``DEPRECATED``) and names the row it was replaced by,
so a tombstone never reads as a live option.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

REPO_DIR = Path(__file__).resolve().parent.parent
REGISTRY_PATH = REPO_DIR / "architecture" / "option-vocabulary.yaml"

#: The concept-membership keys of the registry that a cell can show.
MEMBER_SURFACES = ("cli", "action", "workflow", "config", "python")
#: Column order and label of the surfaces a cell shows.
CELL_SURFACES = (
    ("cli", "CLI"),
    ("action", "Action"),
    ("workflow", "Reusable workflow"),
    ("config", ".abicheck.yml"),
)
SET_LABEL = "--set"
EMPTY = "—"

#: Shown above each generated table so a reader knows what the column means.
LEGEND = (
    "The last column gives the same option on the other surfaces, read from "
    "`architecture/option-vocabulary.yaml`. `--set PATH=VALUE` is the escape "
    "hatch for a `.abicheck.yml` key, not the primary spelling; a core option "
    "has no `--set` form. A `RETIRED` input is a fail-closed tombstone: it "
    "fails the run and names its replacement."
)


def load_registry(path: Path = REGISTRY_PATH) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _public(member: str) -> bool:
    """False for ``actions/*`` sub-action plumbing (not a public surface)."""
    return not member.startswith("actions/")


def _surface_of(member: str) -> str:
    """Which registry key an inventory id belongs to, by its shape.

    Used only for display; membership itself comes from the registry's own
    surface keys, so an id is never guessed from its spelling alone.
    """
    if member.startswith("workflow:"):
        return "workflow"
    if member.startswith(("action:", "actions/")):
        return "action"
    if " --" in member or member.startswith("--"):
        return "cli"
    return "config"


class CrossRefs:
    """Cross-surface cells for every registry member, computed once."""

    def __init__(self, registry: dict[str, Any]) -> None:
        self._concept: dict[str, dict[str, Any]] = {}
        self._surface: dict[str, str] = {}
        for concept in registry.get("concepts", []):
            for surface in MEMBER_SURFACES:
                for member in concept.get(surface, []):
                    self._concept[member] = concept
                    self._surface[member] = surface
        self._replacements: dict[str, dict[str, Any]] = {
            r["id"]: r for r in registry.get("replacements", [])
        }

    @classmethod
    def load(cls, path: Path = REGISTRY_PATH) -> CrossRefs:
        return cls(load_registry(path))

    def replacement(self, member: str) -> dict[str, Any] | None:
        """The ``replacements:`` entry of a retired or deprecated input, if any."""
        return self._replacements.get(member)

    def cell(self, member: str, own: str) -> str:
        """The cross-surface cell for ``member``, a row of the ``own`` surface.

        ``own`` is ``cli``, ``action``, ``workflow`` or ``config``; that
        surface's own items are left out, and ``--set`` is shown for any
        concept that has a config key.
        """
        if member in self._replacements:
            return self.retired_cell(member)
        concept = self._concept.get(member)
        if concept is None:
            return EMPTY
        parts: list[str] = []
        for surface, label in CELL_SURFACES:
            if surface == own:
                continue
            items = self._items(surface, concept.get(surface, []))
            if items:
                parts.append(f"{label}: {', '.join(items)}")
        config = concept.get("config", [])
        if config:
            # An Action row names the Action's own input (`set:`), never the CLI
            # flag; every other row names the flag.
            if own == "action":
                spelled = ", ".join(f"`{key}=VALUE`" for key in config)
                parts.append(f"set: {spelled}")
            else:
                spelled = ", ".join(f"`--set {key}=VALUE`" for key in config)
                parts.append(f"{SET_LABEL}: {spelled}")
        return "; ".join(parts) or EMPTY

    def retired_cell(self, member: str) -> str:
        entry = self._replacements[member]
        status = entry["status"].upper()
        target = entry["replaced_by"]
        if target is None:
            return f"**{status}**, no replacement: {entry['reason']}"
        return f"**{status}**, use {self._spell(target)}"

    # --- display helpers -------------------------------------------------

    def _items(self, surface: str, members: list[str]) -> list[str]:
        members = [m for m in members if _public(m)]
        if surface != "workflow":
            return [self._short(m) for m in members]
        # One reusable input name across both workflows reads as one item.
        grouped: dict[str, list[str]] = {}
        for member in members:
            _, file, name = member.split(":", 2)
            grouped.setdefault(name, []).append(file)
        return [f"`{name}` ({', '.join(files)})" for name, files in grouped.items()]

    def _short(self, member: str) -> str:
        surface = self._surface.get(member, _surface_of(member))
        if surface == "action":
            return f"`{member.split(':', 1)[1]}`"
        return f"`{member}`"

    def _spell(self, member: str) -> str:
        """A replacement target, named in the surface a user would type it in."""
        surface = self._surface.get(member, _surface_of(member))
        if surface == "action":
            return f"the Action input `{member.split(':', 1)[1]}`"
        if surface == "workflow":
            _, file, name = member.split(":", 2)
            return f"the `{name}` input of `{file}`"
        return (
            f"the `.abicheck.yml` key `{member}` "
            f"(or `set: {member}=VALUE` on the Action)"
        )
