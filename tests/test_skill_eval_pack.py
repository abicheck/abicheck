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

"""G37 Phase 0 — the eval pack and the freshness mechanism (D6).

Phase 0 is done when `pr` fails on a hand-edited pack, an unresolvable fixture
reference, and a stale results artifact. The first and third are here (the
second is `test_skill_eval_scenarios.py`), and the third is the one worth
building a fixture for: no evidence bundle exists yet, so the staleness check
would otherwise pass vacuously — proving only that it does not crash on an
empty tree, which is exactly the kind of green this plan exists to distrust.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "scripts"
PACK = REPO / "agent-evals" / "skills" / "skill-eval-pack.json"


def _load_script(name: str) -> Any:
    """Import a `scripts/` module by path — they are not an importable package."""
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


gen = _load_script("gen_skill_eval_pack")
freshness = _load_script("check_skill_eval_freshness")


@pytest.fixture(scope="module")
def pack() -> dict[str, Any]:
    return json.loads(PACK.read_text(encoding="utf-8"))


# --- the pack --------------------------------------------------------------


def test_committed_pack_matches_the_generator() -> None:
    """The hand-edited-pack half of Phase 0's definition of done."""
    assert gen.main(["--check"]) == 0


def test_pack_is_loadable_json(pack: dict[str, Any]) -> None:
    """`agent-benchmark` consumes this file. A generated-file marker carried as
    a comment header would have made it unparseable, which is why the marker is
    a field."""
    assert gen.GENERATED_MARKER in pack["_generated"]
    assert pack["pack_version"] == freshness.SUPPORTED_PACK_VERSION


def test_every_published_skill_is_in_the_pack(pack: dict[str, Any]) -> None:
    published = {
        p.name
        for p in (REPO / ".agents" / "skills").iterdir()
        if (p / "SKILL.md").is_file()
    }
    assert set(pack["skills"]) == published


def test_every_scenario_is_in_the_pack(pack: dict[str, Any]) -> None:
    import yaml

    manifest = yaml.safe_load(
        (REPO / "agent-evals" / "skills" / "scenarios.yaml").read_text(encoding="utf-8")
    )
    assert set(pack["scenarios"]) == {s["id"] for s in manifest["scenarios"]}


def test_pack_carries_runnable_scenarios(pack: dict[str, Any]) -> None:
    """The pack is the cross-repository interface. A consumer that had to read
    `scenarios.yaml` to get the prompt, or `ground_truth.json` to get the
    expected outcome, would be coupled to this repository's private layout —
    the coupling publishing an artifact instead of a library avoids."""
    for scenario_id, entry in pack["scenarios"].items():
        assert entry["prompt"].strip(), scenario_id
        assert entry["inputs"], scenario_id
        assert "expected" in entry, scenario_id


def test_category_a_expectations_are_resolved_into_the_pack(
    pack: dict[str, Any],
) -> None:
    """Resolved, not restated: the catalog stays the one fact owner and the
    pack carries its answer, so a consumer needs neither."""
    catalog = json.loads(
        (REPO / "examples" / "ground_truth.json").read_text(encoding="utf-8")
    )
    for scenario_id, entry in pack["scenarios"].items():
        if entry["category"] != "A":
            continue
        expected = entry["expected"]
        assert expected["resolved_from"] == "examples/ground_truth.json"
        assert expected["verdict"] in {
            v["expected"] for v in catalog["verdicts"].values()
        }, scenario_id


def test_pack_carries_the_gating_contract(pack: dict[str, Any]) -> None:
    """`k` and each dimension's gating mode are what a consumer grades with."""
    rubric = pack["rubric"]
    assert rubric["repetitions"] >= 1
    assert {d["id"] for d in rubric["dimensions"]} == set(range(1, 7))
    zero = {d["id"] for d in rubric["dimensions"] if d["gating"] == "zero_tolerance"}
    assert zero == {2, 6}


def test_scenario_digest_covers_the_fixture_not_just_the_record(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Hashing only the manifest entry leaves a fixture edited *in place* — same
    path, same record — looking fresh, so evidence produced against the old
    fixture keeps re-grading green. The closure is what closes that.

    Runs against a temporary examples root rather than creating a case under
    the real one: a crashed run would otherwise leave a case directory behind
    that `examples-ground-truth` then fails on."""
    monkeypatch.setattr(gen, "EXAMPLES", tmp_path)
    monkeypatch.setattr(gen, "ROOT", tmp_path.parent)
    scenario = {
        "id": "x",
        "category": "A",
        "skill": "s",
        "status": "ready",
        "case": "case-x",
    }
    catalog = {"verdicts": {"case-x": {"expected": "BREAKING"}}}
    case_dir = tmp_path / "case-x"

    before = gen._scenario_digest(scenario, catalog)
    case_dir.mkdir()
    (case_dir / "old.h").write_text("int f(int);\n", encoding="utf-8")
    with_fixture = gen._scenario_digest(scenario, catalog)
    (case_dir / "old.h").write_text("int f(long);\n", encoding="utf-8")
    edited_in_place = gen._scenario_digest(scenario, catalog)

    assert before != with_fixture
    assert with_fixture != edited_in_place


def test_scenario_digest_covers_the_ground_truth_entry() -> None:
    """A Category A scenario derives its expected outcome from the catalog, so a
    catalog edit changes what the scenario expects without touching anything
    else the digest would otherwise see."""
    scenario = {
        "id": "removed-export",
        "category": "A",
        "skill": "native-binary-compatibility-review",
        "status": "ready",
        "case": "case01_symbol_removal",
    }
    catalog = json.loads(
        (REPO / "examples" / "ground_truth.json").read_text(encoding="utf-8")
    )
    original = gen._scenario_digest(scenario, catalog)

    mutated = json.loads(json.dumps(catalog))
    mutated["verdicts"]["case01_symbol_removal"]["expected"] = "COMPATIBLE"
    assert gen._scenario_digest(scenario, mutated) != original


def test_surface_digest_is_a_function_of_committed_files_only(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The pack is committed and its `--check` runs on three operating systems,
    so a digest that varies with the host makes the gate unsatisfiable
    somewhere — which is how the macOS lane failed while every other passed.
    Reading committed files is what makes it reproducible; this pins that the
    digest is exactly that and nothing else."""
    surface = tmp_path / "surface.md"
    surface.write_text("one", encoding="utf-8")
    monkeypatch.setattr(gen, "ROOT", tmp_path)
    monkeypatch.setattr(gen, "SURFACE_SOURCES", (Path("surface.md"),))

    before = gen._surface_digest()
    assert gen._surface_digest() == before, "not deterministic within one process"
    surface.write_text("two", encoding="utf-8")
    assert gen._surface_digest() != before


def test_surface_sources_are_the_generated_projections() -> None:
    """Each is drift-gated against the live objects elsewhere in `pr`, which is
    what lets this digest be file-based without going blind to a real surface
    change: a renamed flag reaches `cli-reference.md` in the PR that renames
    it, or `gen_cli_reference.py --check` fails."""
    assert set(gen.SURFACE_SOURCES) == {
        Path("docs/reference/cli-reference.md"),
        Path("docs/reference/detector-spec.json"),
        Path("abicheck/schemas/compare_report.schema.json"),
    }
    for source in gen.SURFACE_SOURCES:
        assert (REPO / source).is_file(), source


def test_every_hashed_input_is_tracked_by_git() -> None:
    """A digest over a file git does not track is a pack that differs between
    a clean checkout and a machine where something was built — `examples/**/*.so`
    is gitignored and would otherwise land in a scenario's fixture closure."""
    import subprocess

    # -z, not plain output: git quotes paths with unusual characters and
    # whitespace-splitting would silently mis-tokenize a path with a space,
    # failing this test for a reason that has nothing to do with what it checks.
    tracked = set(
        subprocess.run(
            ["git", "ls-files", "-z"],
            cwd=REPO,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.split("\0")
    )
    hashed: list[Path] = []
    for name in gen._published_skill_names():
        hashed += gen._tree_files(gen.PUBLISHED_SKILLS / name)
    for scenario in gen._load_scenarios()["scenarios"]:
        hashed += gen._fixture_paths(scenario)
    hashed += gen._expand(gen.SURFACE_SOURCES) + gen._expand(gen.HARNESS_SOURCES)
    # BUILD_SOURCES walks all of `abicheck/`, which is where install residue
    # would land — the remaining candidate for the cross-platform mismatch
    # that motivated this test.
    hashed += gen._expand(gen.BUILD_SOURCES)

    untracked = sorted(
        p.relative_to(REPO).as_posix()
        for p in hashed
        if p.relative_to(REPO).as_posix() not in tracked
    )
    assert not untracked, f"untracked files reached a pack digest: {untracked}"


def test_drift_report_names_the_entry_that_moved() -> None:
    """A `--check` failure saying only 'out of date' is most of what made the
    cross-platform failure hard to diagnose."""
    committed = json.loads(PACK.read_text(encoding="utf-8"))
    mutated = json.loads(json.dumps(committed))
    mutated["shared"]["abicheck_surface"]["digest"] = "sha256:" + "f" * 64

    drift = gen.describe_drift(json.dumps(committed), json.dumps(mutated))
    assert any("abicheck_surface" in line for line in drift), drift


def test_publication_build_digest_is_not_the_committed_one(
    pack: dict[str, Any],
) -> None:
    """The committed value is repo content only, so `--check` is deterministic
    on any checkout; the publication digest folds in the resolved runtime
    dependency versions, which are a property of the environment publishing."""
    assert gen.publication_build_digest() != pack["shared"]["build"]["digest"]
    assert pack["shared"]["build"]["publication_only"] is True


# --- the freshness mechanism ----------------------------------------------


def test_freshness_passes_on_the_committed_tree() -> None:
    assert freshness.main([]) == 0


def test_every_hash_maps_to_a_skill(pack: dict[str, Any]) -> None:
    """D6's mapping invariant: a hash that moves while nominating nothing to
    re-run leaves the author with a failing check and nothing to run. That
    deadlock happened twice in review before it was stated as an invariant."""
    entries = freshness.hash_entries(pack)
    assert entries
    for hash_id, entry in entries.items():
        assert entry["affects"], hash_id
        assert entry["roots"], hash_id
        assert entry["digest"].startswith("sha256:"), hash_id


@pytest.mark.parametrize(
    "path",
    [
        ".agents/skills/native-api-evolution/SKILL.md",
        "agent-evals/skills/scenarios.yaml",
        "examples/ground_truth.json",
        "examples/case01_symbol_removal/old.h",
        "tests/agent_skills/trigger_corpus.yaml",
        "abicheck/cli.py",
        "pyproject.toml",
    ],
)
def test_known_inputs_route_to_a_hash(pack: dict[str, Any], path: str) -> None:
    """D6's completeness invariant, from the other direction: an input the
    evaluation reads that routes to no hash accepts evidence that no longer
    corresponds to anything — which the trigger corpus silently was."""
    assert freshness.route(path, freshness.hash_entries(pack)), path


def test_an_unrouted_input_is_reported(pack: dict[str, Any]) -> None:
    assert not freshness.route("some/unrelated/thing.txt", freshness.hash_entries(pack))


def _bundle(pack: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    skill = "native-binary-compatibility-review"
    scenario = "removed-export"
    hashes = {
        "skill_tree": pack["skills"][skill]["tree"],
        "abicheck_surface": pack["shared"]["abicheck_surface"]["digest"],
        "harness": pack["shared"]["harness"]["digest"],
        "trigger_corpus": pack["shared"]["trigger_corpus"]["digest"],
        "scenarios": {scenario: pack["scenarios"][scenario]["digest"]},
    }
    hashes.update(overrides.pop("hashes", {}))
    return {
        "schema_version": 1,
        "kind": "behavioral",
        "scenario_id": scenario,
        "repetition": 0,
        "agent": "claude-code",
        "model": "test-model",
        "hashes": hashes,
        "observed_inputs": [
            {
                "path": ".agents/skills/native-binary-compatibility-review/SKILL.md",
                "digest": "sha256:" + "0" * 64,
            },
        ],
        **overrides,
    }


def _write_bundle(tmp_path: Path, bundle: dict[str, Any]) -> Path:
    meta = (
        tmp_path
        / "native-binary-compatibility-review"
        / "removed-export"
        / "0"
        / "meta.json"
    )
    meta.parent.mkdir(parents=True)
    meta.write_text(json.dumps(bundle), encoding="utf-8")
    return meta


def _check_bundle(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, bundle: dict[str, Any]
) -> int:
    """Run the real checker against one synthetic bundle tree."""
    _write_bundle(tmp_path, bundle)
    monkeypatch.setattr(freshness, "EVIDENCE", tmp_path)
    return freshness.main([])


def test_a_fresh_bundle_is_accepted(
    pack: dict[str, Any], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    assert _check_bundle(monkeypatch, tmp_path, _bundle(pack)) == 0


def test_a_stale_skill_tree_hash_fails(
    pack: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The stale-results-artifact half of Phase 0's definition of done: a skill
    edited without refreshed evidence. This is the G36 requirement that kept
    being restated in prose because nothing enforced it."""
    bundle = _bundle(pack, hashes={"skill_tree": "sha256:" + "a" * 64})
    assert _check_bundle(monkeypatch, tmp_path, bundle) == 1
    out = capsys.readouterr().out
    assert "stale" in out
    # The failure nominates what to re-run — a check that rejects evidence
    # without naming its replacement is the deadlock D6 states as an invariant.
    assert "native-binary-compatibility-review" in out


@pytest.mark.parametrize(
    "hash_key",
    ["abicheck_surface", "harness", "trigger_corpus"],
)
def test_a_stale_shared_hash_fails(
    pack: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    hash_key: str,
) -> None:
    bundle = _bundle(pack, hashes={hash_key: "sha256:" + "b" * 64})
    assert _check_bundle(monkeypatch, tmp_path, bundle) == 1


def test_a_stale_scenario_hash_fails(
    pack: dict[str, Any], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    bundle = _bundle(
        pack, hashes={"scenarios": {"removed-export": "sha256:" + "c" * 64}}
    )
    assert _check_bundle(monkeypatch, tmp_path, bundle) == 1


def test_a_hash_the_pack_no_longer_has_fails(
    pack: dict[str, Any], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A renamed or deleted input leaves evidence citing something gone. Reading
    that as "nothing to compare, therefore fine" is how stale evidence survives."""
    bundle = _bundle(
        pack, hashes={"scenarios": {"scenario-that-was-deleted": "sha256:" + "d" * 64}}
    )
    assert _check_bundle(monkeypatch, tmp_path, bundle) == 1


def test_an_observed_input_routing_to_no_hash_fails(
    pack: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The completeness check, on a real bundle: a runner that starts reading a
    new file gets a failure rather than silently producing evidence no hash
    covers."""
    bundle = _bundle(pack)
    bundle["observed_inputs"].append(
        {
            "path": "agent-evals/skills/newly-invented-input.yaml",
            "digest": "sha256:" + "e" * 64,
        }
    )
    assert _check_bundle(monkeypatch, tmp_path, bundle) == 1
    assert "routes to no hash" in capsys.readouterr().out


def test_a_bundle_filed_under_the_wrong_skill_is_checked_against_that_skill(
    pack: dict[str, Any], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`skill_tree` resolves through the bundle's own directory, not a field, so
    a bundle cannot claim to be evidence for a skill it is not filed under."""
    bundle = _bundle(
        pack, hashes={"skill_tree": pack["skills"]["native-api-evolution"]["tree"]}
    )
    assert _check_bundle(monkeypatch, tmp_path, bundle) == 1


def test_a_bundle_that_hashes_a_different_scenario_fails(
    pack: dict[str, Any], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A behavioral bundle is evidence for the scenario it names. Hashing a
    *different*, perfectly fresh scenario would otherwise pass every check
    while the named scenario could be edited freely underneath it."""
    other = "changed-signature"
    bundle = _bundle(
        pack, hashes={"scenarios": {other: pack["scenarios"][other]["digest"]}}
    )
    assert _check_bundle(monkeypatch, tmp_path, bundle) == 1


def test_a_bundle_naming_an_unknown_scenario_fails(
    pack: dict[str, Any], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    bundle = _bundle(pack)
    bundle["scenario_id"] = "a-scenario-that-does-not-exist"
    assert _check_bundle(monkeypatch, tmp_path, bundle) == 1


def test_a_bundle_filed_under_the_wrong_skill_for_its_scenario_fails(
    pack: dict[str, Any], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`api-only-break` belongs to a different skill than the directory this
    bundle is filed under — evidence attributed to the wrong skill is how a
    skill ends up with coverage it never earned."""
    scenario = "api-only-break"
    bundle = _bundle(
        pack, hashes={"scenarios": {scenario: pack["scenarios"][scenario]["digest"]}}
    )
    bundle["scenario_id"] = scenario
    assert _check_bundle(monkeypatch, tmp_path, bundle) == 1


def test_a_trigger_bundle_is_not_held_to_the_scenario_rules(
    pack: dict[str, Any], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A trigger bundle's `scenario_id` is a prompt id from the corpus, not a
    scenarios.yaml entry — grading it against the behavioral contract is the
    reason the two kinds exist separately at all."""
    bundle = _bundle(pack)
    bundle["kind"] = "trigger"
    bundle["scenario_id"] = "positive-0"
    assert _check_bundle(monkeypatch, tmp_path, bundle) == 0


@pytest.mark.parametrize(
    "dropped",
    ["schema_version", "kind", "scenario_id", "agent", "model", "observed_inputs"],
)
def test_a_bundle_missing_provenance_fails(
    pack: dict[str, Any], monkeypatch: pytest.MonkeyPatch, tmp_path: Path, dropped: str
) -> None:
    """Every check here compares recorded values, so a bundle that records
    nothing passes by having nothing to compare. An omitted field has to fail
    exactly as loudly as a stale one."""
    bundle = _bundle(pack)
    del bundle[dropped]
    assert _check_bundle(monkeypatch, tmp_path, bundle) == 1


@pytest.mark.parametrize(
    "dropped",
    ["skill_tree", "abicheck_surface", "harness", "trigger_corpus", "scenarios"],
)
def test_a_bundle_missing_a_required_hash_fails(
    pack: dict[str, Any], monkeypatch: pytest.MonkeyPatch, tmp_path: Path, dropped: str
) -> None:
    bundle = _bundle(pack)
    del bundle["hashes"][dropped]
    assert _check_bundle(monkeypatch, tmp_path, bundle) == 1


def test_an_input_routing_only_to_a_hash_this_bundle_lacks_fails(
    pack: dict[str, Any], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Routing to *some* hash in the pack is not enough. A binary-review bundle
    that read another skill's SKILL.md records only its own tree hash, so an
    edit to the skill it actually read leaves it 'fresh' — completeness is a
    property of this bundle's own recorded hashes, not of the pack."""
    bundle = _bundle(pack)
    bundle["observed_inputs"].append(
        {
            "path": ".agents/skills/native-api-evolution/SKILL.md",
            "digest": "sha256:" + "9" * 64,
        }
    )
    assert _check_bundle(monkeypatch, tmp_path, bundle) == 1


def test_a_near_empty_bundle_fails(
    pack: dict[str, Any], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The limiting case a truncated write or a runner regression produces."""
    assert _check_bundle(monkeypatch, tmp_path, {"kind": "trigger"}) == 1


def test_a_newer_pack_version_fails_closed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Same fail-closed rule as the contract-context replay path: a shape this
    build does not understand is an error, never a best-effort read."""
    newer = tmp_path / "pack.json"
    payload = json.loads(PACK.read_text(encoding="utf-8"))
    payload["pack_version"] = freshness.SUPPORTED_PACK_VERSION + 1
    newer.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(freshness, "PACK", newer)
    assert freshness.main([]) == 1


def test_a_missing_pack_fails(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(freshness, "PACK", tmp_path / "absent.json")
    assert freshness.main([]) == 1
