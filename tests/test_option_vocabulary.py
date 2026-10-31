"""ADR-091: the option-vocabulary registry covers every live option, and it agrees with itself.

``architecture/option-vocabulary-inventory.json`` is generated from the live
surfaces (click tree, ``.abicheck.yml`` key table, ``action.yml`` inputs, typed
request fields); ``architecture/option-vocabulary.yaml`` groups those rows into
concepts by hand. Phase 0 keeps the two honest (the inventory is fresh and every
row belongs to exactly one concept). Phase 1 adds the parity gate:

- name rule (D1): a core/config concept's CLI, Action and Python spellings are
  derived from its canonical path, unless an exception records the deviation;
- value-set rule (D2): where the CLI and Python both expose a closed set for a
  concept, the sets are equal unless an exception says otherwise;
- budget rule (D4): the visible ``compare`` core options and the root Action's
  analysis inputs stay within 15 and 25, or within a recorded no-growth count;
- exceptions are checked both ways: each names a real inventory row, an ADR-091
  rename id, and a deviation that still exists (a stale one fails).

The derivation below is written out in this file, not imported from
``scripts/gen_option_vocabulary.py``: the generator reads the surfaces, and this
gate must not share its code.
"""

from __future__ import annotations

import enum
import fnmatch
import importlib.util
import json
import pathlib
import re
import typing
from collections import Counter
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.repo_scan

REPO = Path(__file__).resolve().parent.parent
REGISTRY = REPO / "architecture" / "option-vocabulary.yaml"
INVENTORY = REPO / "architecture" / "option-vocabulary-inventory.json"
ADR = REPO / "docs" / "contribute" / "adr" / "091-one-option-vocabulary.md"
SURFACES = ("cli", "config", "action", "workflow", "python")
TIERS = {"core", "config", "action", "python", "internal", "retired"}
#: Tiers the D1 name rule applies to: concepts reachable from a config path.
NAMED_TIERS = {"core", "config"}
RULES = {"name", "type", "default", "values", "budget"}
#: Shim parameters of run_compare/run_dump: the snake leaf only (D1 "shim").
SHIM_OWNERS = {"run_compare", "run_dump"}
#: D4 budgets. Exceptions record today's counts; they may only shrink.
CORE_LIMIT = 15
ACTION_LIMIT = 25
#: Approved no-growth ceilings (ADR-091 R22). Lower these with the registry when
#: the counts fall; a PR cannot raise them by editing the registry's `recorded`.
SURFACE_BASELINE = {"compare": 20, "action": 53}
#: The only concepts that may opt out of the D1 name rule (ADR-091 "Out of scope").
OUT_OF_SCOPE_NAMING = {"aggregate", "project"}
#: The rename ids allowed to carry a budget exception (ADR-091 D4 / Phase 5).
RENAMES_FOR_BUDGET = {"R22"}


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


# --- independent derivation of D1 spellings --------------------------------


def _kebab(name: str) -> str:
    return name.replace("_", "-")


def _singular(kebab: str) -> str:
    return kebab[:-1] if kebab.endswith("s") else kebab


def _strip_side(name: str) -> str:
    """Drop the D3 side qualifier (``old-``/``new-``) from a kebab name."""
    for side in ("old-", "new-"):
        if name.startswith(side):
            return name[len(side) :]
    return name


def _strip_side_snake(name: str) -> str:
    for side in ("old_", "new_"):
        if name.startswith(side):
            return name[len(side) :]
    return name


def _leaf(concept: str) -> str:
    return concept.split(".")[-1]


def _accepted_kebab(concept: str, ambiguous: bool) -> set[str]:
    """The kebab spellings D1 allows: the leaf, and the full path only where the
    leaf is ambiguous across blocks (``debug.format`` -> ``debug-format``)."""
    spellings = {_kebab(_leaf(concept))}
    if ambiguous:
        spellings.add(_kebab(concept.replace(".", "_")))
    return spellings


def _ambiguous_leaf(registry: dict, concept: str) -> bool:
    """True when another concept in the registry shares this concept's leaf."""
    leaf = _leaf(concept)
    return sum(1 for c in registry["concepts"] if _leaf(c["concept"]) == leaf) > 1


def _name_deviations(
    concept: dict, rows: dict[str, dict], ambiguous: bool = False
) -> list[str]:
    """Inventory ids of ``concept``'s members whose spelling breaks D1.

    ``rows`` maps each surface name to ``{inventory id: row}``. A CLI option is
    spelled ``--`` + kebab leaf, singular when repeatable. An Action input is the
    kebab leaf, plural when the option it mirrors is repeatable, with an optional
    ``old-``/``new-`` side prefix. A Python field is the snake leaf (or the
    flattened full path, as request fields are); a shim parameter is the snake
    leaf only.
    """
    path = concept["concept"]
    name_leaf = _leaf(path)
    kebabs = _accepted_kebab(path, ambiguous)
    snake_full = path.replace(".", "_")
    repeatable = any(
        rows["cli"][i]["multiple"] for i in concept.get("cli", []) if i in rows["cli"]
    )
    bad: list[str] = []
    for i in concept.get("cli", []):
        name = _strip_side(rows["cli"][i]["option"][2:])
        ok = name in kebabs or (
            rows["cli"][i]["multiple"] and name in {_singular(k) for k in kebabs}
        )
        if not ok:
            bad.append(i)
    for i in concept.get("action", []):
        if i not in rows["action"]:
            continue  # an unknown id is test_every_registry_id_exists's finding
        name = _strip_side(rows["action"][i]["input"])
        plural = {k + "s" for k in kebabs} if repeatable else set()
        if name not in kebabs | plural:
            bad.append(i)
    for i in concept.get("python", []):
        if i not in rows["python"]:
            continue
        row = rows["python"][i]
        name = _strip_side_snake(row["name"])
        if row["owner"] in SHIM_OWNERS:
            ok = name == name_leaf
        else:
            ok = name in ({name_leaf, snake_full} if ambiguous else {name_leaf})
        if not ok:
            bad.append(i)
    return bad


def _value_set_deviation(concept: dict, rows: dict[str, dict]) -> str | None:
    """The CLI option id when the CLI and Python closed sets of ``concept`` differ."""
    cli = {
        i: set(rows["cli"][i]["choices"])
        for i in concept.get("cli", [])
        if rows["cli"].get(i, {}).get("choices")
    }
    py = {
        i: set(rows["python"][i]["choices"])
        for i in concept.get("python", [])
        if rows["python"].get(i, {}).get("choices")
    }
    if not cli or not py:
        return None
    cli_all = set().union(*cli.values())
    py_all = set().union(*py.values())
    if cli_all == py_all:
        return None
    return next(iter(cli))


def _claims(registry: dict) -> dict[tuple[str, str], str]:
    return {
        (s, i): c["concept"]
        for c in registry["concepts"]
        for s in SURFACES
        for i in c.get(s, [])
    }


#: ADR-091 D4: `--set`/`set:` is the one generic spelling of the config tier,
#: not a concept's own option, so it counts toward neither surface's budget.
GENERIC_OVERRIDE = "config.override"


def _visible_budget(registry: dict, inventory: dict) -> tuple[list[str], list[str]]:
    """Core ``compare`` options that plain ``--help`` shows, and root Action analysis inputs."""
    tier = {c["concept"]: c["tier"] for c in registry["concepts"]}
    claims = _claims(registry)
    core = [
        r["id"]
        for r in inventory["cli"]
        if r["command"] == "compare"
        and r["help_common"]
        and tier.get(claims.get(("cli", r["id"]), ""), "") == "core"
    ]
    analysis = [
        r["id"]
        for r in inventory["action"]
        if r["action"] == "action"
        and tier.get(claims.get(("action", r["id"]), ""), "")
        not in {"action", "retired"}
        and claims.get(("action", r["id"])) != GENERIC_OVERRIDE
    ]
    return core, analysis


def _rows(inventory: dict) -> dict[str, dict]:
    return {s: {r["id"]: r for r in inventory[s]} for s in SURFACES}


def _named_concepts(registry: dict) -> list[dict]:
    return [
        c
        for c in registry["concepts"]
        if c["tier"] in NAMED_TIERS and c.get("naming") != "out_of_scope"
    ]


# --- Phase 0: the registry covers the surfaces -----------------------------


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
        r["id"]
        for s in ("action", "workflow")
        for r in inventory[s]
        if r["status"] in {"retired", "ignored"}
    }
    in_tier = {
        i
        for c in registry["concepts"]
        if c["tier"] == "retired"
        for s in ("action", "workflow")
        for i in c.get(s, [])
    }
    assert in_tier - dead == set(), "live inputs filed under the retired tier"
    assert dead - in_tier == set(), "retired/ignored inputs filed under a live concept"


def test_workflow_inputs_share_the_concept_of_the_action_input_they_mirror(
    registry: dict, inventory: dict
) -> None:
    """A reusable workflow forwards its inputs to check-target under the same
    names, so a workflow input that spells a check-target input must sit in that
    input's concept (ADR-091 D2: one concept, one behavior, on every surface)."""
    concept_of = {
        i: c["concept"] for c in registry["concepts"] for i in c.get("action", [])
    }
    claimed = {
        i: c["concept"] for c in registry["concepts"] for i in c.get("workflow", [])
    }
    problems = []
    for row in inventory["workflow"]:
        mirror = f"actions/check-target:{row['input']}"
        if row["id"] in claimed and mirror in concept_of:
            if claimed[row["id"]] != concept_of[mirror]:
                problems.append(f"{row['id']} is not in {concept_of[mirror]}")
    assert not problems, "\n".join(problems)


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


def test_out_of_scope_naming_is_the_adr_out_of_scope_surface(
    registry: dict, inventory: dict
) -> None:
    """ADR-091 "Out of scope" excludes ``aggregate`` and ``project *`` option
    names; only those concepts may opt out of the D1 name rule, and they must
    say so."""
    cli = _rows(inventory)["cli"]
    for c in registry["concepts"]:
        if c.get("naming") is None:
            continue
        assert c["naming"] == "out_of_scope", c["concept"]
        assert c["concept"] in OUT_OF_SCOPE_NAMING, c["concept"]
        assert c.get("naming_reason"), c["concept"]
        for i in c.get("cli", []):
            command = cli[i]["command"]
            assert command == "aggregate" or command.startswith("project "), (
                c["concept"],
                i,
            )
        assert not c.get("exceptions"), c["concept"]


# --- Phase 1: the parity gate ----------------------------------------------


def test_name_rule_holds_or_is_recorded(registry: dict, inventory: dict) -> None:
    """D1: every named concept's CLI/Action/Python spellings follow from its
    canonical path, unless an exception records the deviation."""
    rows = _rows(inventory)
    problems: list[str] = []
    for c in _named_concepts(registry):
        found = set(_name_deviations(c, rows, _ambiguous_leaf(registry, c["concept"])))
        recorded = {e["id"] for e in c.get("exceptions", []) if e["rule"] == "name"}
        problems += [
            f"unrecorded {c['concept']}: {i}" for i in sorted(found - recorded)
        ]
        problems += [f"stale {c['concept']}: {i}" for i in sorted(recorded - found)]
    assert not problems, "\n".join(problems)


def test_value_set_rule_holds_or_is_recorded(registry: dict, inventory: dict) -> None:
    """D2: where CLI and Python both expose a closed set, the sets are equal."""
    rows = _rows(inventory)
    problems: list[str] = []
    for c in _named_concepts(registry):
        found = _value_set_deviation(c, rows)
        recorded = {e["id"] for e in c.get("exceptions", []) if e["rule"] == "values"}
        if found and found not in recorded:
            problems.append(f"unrecorded value set {c['concept']}: {found}")
        if not found and recorded:
            problems.append(f"stale value-set exception {c['concept']}")
        if found and recorded and found not in recorded:
            problems.append(f"value-set exception names another row {c['concept']}")
    assert not problems, "\n".join(problems)


def test_surface_budget_holds_or_is_recorded(registry: dict, inventory: dict) -> None:
    """D4: at most 15 visible core options on ``compare`` and 25 analysis inputs
    on the root Action. An exception records today's count; the count may fall
    but never rise, and an exception whose surface is back under its limit is
    stale."""
    core, analysis = _visible_budget(registry, inventory)
    recorded = {b["surface"]: b for b in registry.get("budget_exceptions", [])}
    assert set(recorded) <= {"compare", "action"}, recorded
    for surface, live, limit in (
        ("compare", len(core), CORE_LIMIT),
        ("action", len(analysis), ACTION_LIMIT),
    ):
        exc = recorded.get(surface)
        if live <= limit:
            assert exc is None, (
                f"stale budget exception for {surface}: live {live} <= {limit}"
            )
            continue
        assert exc is not None, (
            f"{surface} surface has {live} options, over the D4 budget of {limit}"
        )
        assert exc["recorded"] <= SURFACE_BASELINE[surface], (
            f"{surface} recorded count {exc['recorded']} exceeds the approved baseline {SURFACE_BASELINE[surface]}"
        )
        assert live <= exc["recorded"], (
            f"{surface} surface grew to {live} (recorded {exc['recorded']}); the budget is no-growth"
        )
        assert exc["rename"] in RENAMES_FOR_BUDGET, exc


def test_budget_excludes_only_the_generic_override() -> None:
    """The override input is not counted; any other analysis input still is."""
    registry = {
        "concepts": [
            {"concept": GENERIC_OVERRIDE, "tier": "config", "action": ["action:set"]},
            {"concept": "compile.lang", "tier": "config", "action": ["action:lang"]},
            {"concept": "action.runtime", "tier": "action", "action": ["action:mode"]},
        ]
    }
    rows = [
        {"id": f"action:{n}", "action": "action", "input": n}
        for n in ("set", "lang", "mode")
    ]
    _, analysis = _visible_budget(registry, {"cli": [], "action": rows})
    assert analysis == ["action:lang"]


def _adr_row_accepts(adr: str, rename: str) -> bool:
    """True when ADR-091's own table row for ``rename`` records its deviations as
    ACCEPTED (an accepted exception needs no rename of its own, only the row that
    accepts it; ADR-091 maintainer rule, 2026-10-31)."""
    rows = [line for line in adr.splitlines() if line.startswith(f"| {rename} |")]
    return bool(rows) and "ACCEPTED" in rows[0]


def test_every_exception_is_well_formed(registry: dict, inventory: dict) -> None:
    """Each exception names a real row, a known rule, an R-id the ADR states, and a
    note; the budget exceptions name a surface."""
    adr = ADR.read_text(encoding="utf-8")
    live = {(s, r["id"]) for s in SURFACES for r in inventory[s]}
    claims = _claims(registry)
    problems: list[str] = []
    for c in registry["concepts"]:
        for e in c.get("exceptions", []):
            where = f"{c['concept']}: {e}"
            if e.get("rule") not in RULES:
                problems.append(f"unknown rule {where}")
            if not any((s, e.get("id")) in live for s in SURFACES):
                problems.append(f"names no inventory row {where}")
            elif not any(claims.get((s, e["id"])) == c["concept"] for s in SURFACES):
                problems.append(f"row is not a member of this concept {where}")
            if not re.fullmatch(r"R\d+", str(e.get("rename", ""))):
                problems.append(f"no rename id {where}")
            elif not re.search(rf"\b{e['rename']}\b", adr):
                problems.append(f"{e['rename']} is not in ADR-091 {where}")
            if not e.get("note"):
                problems.append(f"no note {where}")
            if "accepted" in e and not isinstance(e["accepted"], bool):
                problems.append(f"accepted must be true or false {where}")
            if e.get("accepted") and not _adr_row_accepts(adr, e.get("rename", "")):
                problems.append(
                    f"accepted exception {where} is not accepted by its ADR-091 row "
                    f"{e.get('rename')}"
                )
    for b in registry.get("budget_exceptions", []):
        where = f"budget {b}"
        if not re.search(rf"\b{b.get('rename')}\b", adr):
            problems.append(f"{b.get('rename')} is not in ADR-091 {where}")
        if not isinstance(b.get("recorded"), int) or not b.get("note"):
            problems.append(f"malformed {where}")
    assert not problems, "\n".join(problems)


# --- negative controls: the derivation on hand-written cases ----------------


def _case(**members) -> tuple[dict, dict[str, dict]]:
    concept = {"concept": "scope.exclude_headers", "tier": "core", **members}
    return concept, {
        "cli": {
            "compare --exclude-header": {
                "id": "compare --exclude-header",
                "option": "--exclude-header",
                "multiple": True,
                "choices": None,
            },
        },
        "action": {
            "action:exclude-headers": {
                "id": "action:exclude-headers",
                "input": "exclude-headers",
            },
        },
        "python": {
            "InputSpec.exclude_headers": {
                "id": "InputSpec.exclude_headers",
                "owner": "InputSpec",
                "name": "exclude_headers",
                "choices": None,
            },
            "run_compare(exclude_headers)": {
                "id": "run_compare(exclude_headers)",
                "owner": "run_compare",
                "name": "exclude_headers",
                "choices": None,
            },
        },
    }


def test_name_derivation_accepts_the_d1_spelling() -> None:
    concept, rows = _case(
        cli=["compare --exclude-header"],
        action=["action:exclude-headers"],
        python=["InputSpec.exclude_headers", "run_compare(exclude_headers)"],
    )
    assert _name_deviations(concept, rows) == []


def test_name_derivation_flags_each_broken_surface() -> None:
    concept, rows = _case(
        cli=["compare --exclude"],
        action=["action:exclude-header"],
        python=["InputSpec.exclude_header", "run_compare(scope_exclude_headers)"],
    )
    rows["cli"]["compare --exclude"] = {
        "id": "compare --exclude",
        "option": "--exclude",
        "multiple": False,
        "choices": None,
    }
    rows["action"]["action:exclude-header"] = {
        "id": "action:exclude-header",
        "input": "exclude-header",
    }
    rows["python"]["InputSpec.exclude_header"] = {
        "id": "InputSpec.exclude_header",
        "owner": "InputSpec",
        "name": "exclude_header",
        "choices": None,
    }
    rows["python"]["run_compare(scope_exclude_headers)"] = {
        "id": "run_compare(scope_exclude_headers)",
        "owner": "run_compare",
        "name": "scope_exclude_headers",
        "choices": None,
    }
    assert _name_deviations(concept, rows) == [
        "compare --exclude",
        "action:exclude-header",
        "InputSpec.exclude_header",
        "run_compare(scope_exclude_headers)",
    ]


def test_name_derivation_accepts_a_side_prefix_and_full_path() -> None:
    concept = {"concept": "debug.format", "tier": "config"}
    rows = {
        "cli": {
            "compare --old-debug-format": {
                "id": "compare --old-debug-format",
                "option": "--old-debug-format",
                "multiple": False,
                "choices": None,
            }
        },
        "action": {
            "action:old-debug-format": {
                "id": "action:old-debug-format",
                "input": "old-debug-format",
            }
        },
        "python": {
            "CompareRequest.debug_format": {
                "id": "CompareRequest.debug_format",
                "owner": "CompareRequest",
                "name": "debug_format",
                "choices": None,
            }
        },
    }
    concept["cli"] = ["compare --old-debug-format"]
    concept["action"] = ["action:old-debug-format"]
    concept["python"] = ["CompareRequest.debug_format"]
    assert _name_deviations(concept, rows, ambiguous=True) == []
    # Without a leaf collision the full path is not a D1 spelling, on any surface.
    assert _name_deviations(concept, rows, ambiguous=False) == [
        "compare --old-debug-format",
        "action:old-debug-format",
        "CompareRequest.debug_format",
    ]


def test_value_set_derivation_compares_closed_sets() -> None:
    concept = {
        "concept": "contract",
        "tier": "core",
        "cli": ["compare --contract"],
        "python": ["CompareRequest.contract_mode"],
    }
    rows = {
        "cli": {
            "compare --contract": {
                "id": "compare --contract",
                "choices": ["public", "exports", "all", "auto"],
            }
        },
        "python": {
            "CompareRequest.contract_mode": {
                "id": "CompareRequest.contract_mode",
                "choices": ["public", "exports", "all"],
            }
        },
    }
    assert _value_set_deviation(concept, rows) == "compare --contract"
    rows["python"]["CompareRequest.contract_mode"]["choices"].append("auto")
    assert _value_set_deviation(concept, rows) is None


def test_default_render_is_click_version_independent() -> None:
    """A CLI option's "no default" renders the same on every Click release: ``None``
    on 8.2 and the unset sentinel on 8.3+ are both ``<unset>`` (``False`` for a
    boolean flag), and an explicit default keeps its own spelling. The 8.2.1 vs
    8.5 comparison this pins was run against the committed inventory."""
    gen = _generator()

    class Sentinel(enum.Enum):  # Click 8.3+ shape of the unset marker
        UNSET = "UNSET"

    class Option:
        def __init__(self, default: object, is_flag: bool = False) -> None:
            self.default = default
            self.is_flag = is_flag

    assert gen._click_default_text(Option(None)) == "<unset>"
    assert gen._click_default_text(Option(Sentinel.UNSET)) == "<unset>"
    assert gen._click_default_text(Option(None, is_flag=True)) == "False"
    assert gen._click_default_text(Option(Sentinel.UNSET, is_flag=True)) == "False"
    assert gen._click_default_text(Option("auto")) == "'auto'"
    assert gen._default_text(None) == "None"


def test_annotation_text_is_interpreter_independent() -> None:
    """Annotations are rendered from class names, not str(): Python 3.14 spells
    pathlib.Path as pathlib._local.Path, and the inventory must read the same on
    every interpreter in the CI matrix."""
    gen = _generator()
    assert gen._annotation_text(pathlib.Path | None) == "Path | None"
    assert (
        gen._annotation_text(tuple[pathlib.Path, ...] | None)
        == "tuple[Path, ...] | None"
    )
    assert gen._annotation_text(typing.Literal["a", "b"]) == "Literal['a', 'b']"
    assert gen._annotation_text(bool) == "bool"
