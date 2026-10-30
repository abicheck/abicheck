"""ADR-091 Phase 0: the option-vocabulary registry covers every live option.

``architecture/option-vocabulary-inventory.json`` is generated from the live
surfaces (click tree, ``.abicheck.yml`` key table, ``action.yml`` inputs, typed
request fields); ``architecture/option-vocabulary.yaml`` groups those rows into
concepts by hand. These tests keep the two honest: the inventory is fresh, and
every row belongs to exactly one concept.
"""

from __future__ import annotations

import fnmatch
import importlib.util
import json
from collections import Counter
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parent.parent
REGISTRY = REPO / "architecture" / "option-vocabulary.yaml"
INVENTORY = REPO / "architecture" / "option-vocabulary-inventory.json"
SURFACES = ("cli", "config", "action", "python")
TIERS = {"core", "config", "action", "python", "internal", "retired"}


def _generator():
    spec = importlib.util.spec_from_file_location(
        "gen_option_vocabulary", REPO / "scripts" / "gen_option_vocabulary.py"
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def registry() -> dict:
    return yaml.safe_load(REGISTRY.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def inventory() -> dict:
    return json.loads(INVENTORY.read_text(encoding="utf-8"))


def test_inventory_is_fresh() -> None:
    assert _generator().render() == INVENTORY.read_text(encoding="utf-8"), (
        "architecture/option-vocabulary-inventory.json is stale; run python scripts/gen_option_vocabulary.py "
        "and claim any new row in architecture/option-vocabulary.yaml"
    )


def test_every_registry_id_exists(registry: dict, inventory: dict) -> None:
    live = {s: {r["id"] for r in inventory[s]} for s in SURFACES}
    unknown = [
        (c["concept"], s, i)
        for c in registry["concepts"]
        for s in SURFACES
        for i in c.get(s, [])
        if i not in live[s]
    ]
    assert not unknown, f"registry names options that no surface has: {unknown}"


def test_every_live_option_is_claimed_exactly_once(
    registry: dict, inventory: dict
) -> None:
    claims = Counter(
        (s, i) for c in registry["concepts"] for s in SURFACES for i in c.get(s, [])
    )
    patterns = [o["pattern"] for o in registry.get("out_of_scope", [])]
    twice = sorted(k for k, n in claims.items() if n > 1)
    unclaimed = sorted(
        (s, r["id"])
        for s in SURFACES
        for r in inventory[s]
        if (s, r["id"]) not in claims
        and not any(fnmatch.fnmatchcase(r["id"], p) for p in patterns)
    )
    assert not twice, f"options claimed by more than one concept: {twice}"
    assert not unclaimed, (
        f"options not in any concept of architecture/option-vocabulary.yaml (ADR-091 D6): {unclaimed}"
    )


def test_concepts_are_well_formed(registry: dict) -> None:
    names = [c["concept"] for c in registry["concepts"]]
    assert len(names) == len(set(names)), "duplicate concept names"
    for c in registry["concepts"]:
        assert c["tier"] in TIERS, c
        assert isinstance(c.get("renames", []), list), c
        assert any(c.get(s) for s in SURFACES), f"{c['concept']} has no members"


def test_retired_tier_is_exactly_the_dead_inputs(
    registry: dict, inventory: dict
) -> None:
    """A fail-closed tombstone or an ignored input behaves nothing like the live
    option it once was, so it may not share a concept with it (ADR-091 D2)."""
    dead = {
        r["id"] for r in inventory["action"] if r["status"] in {"retired", "ignored"}
    }
    in_tier = {
        i
        for c in registry["concepts"]
        if c["tier"] == "retired"
        for i in c.get("action", [])
    }
    assert in_tier - dead == set(), "live inputs filed under the retired tier"
    assert dead - in_tier == set(), "retired/ignored inputs filed under a live concept"


def test_retired_status_matches_the_tombstone_contract(inventory: dict) -> None:
    """The inventory's `retired` must be the set the tombstone contract test
    guards, read independently from the manifest."""
    contract = {
        f"action:{name}"
        for name, spec in (
            yaml.safe_load((REPO / "action.yml").read_text(encoding="utf-8"))["inputs"]
        ).items()
        if "RETIRED" in str((spec or {}).get("description", ""))
    }
    ours = {
        r["id"]
        for r in inventory["action"]
        if r["status"] == "retired" and r["action"] == "action"
    }
    assert ours == contract


def test_out_of_scope_entries_name_an_expiry(registry: dict) -> None:
    for entry in registry.get("out_of_scope", []):
        assert entry.get("until") in {"phase-1", "permanent"}, entry
        assert entry.get("reason"), entry
