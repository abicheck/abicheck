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
import yaml

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "scripts"
PACK = REPO / "agent-evals" / "skills" / "skill-eval-pack.json"

# Phase 3 resolver (scripts/CLAUDE.md, docs/contribute/plans/examples-catalog-split.md).
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
import example_catalog  # noqa: E402


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
surface = _load_script("skill_eval_surface")
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
    catalog = example_catalog.load_ground_truth()
    for scenario_id, entry in pack["scenarios"].items():
        if entry["category"] != "A":
            continue
        expected = entry["expected"]
        assert expected["resolved_from"] == "catalog/ground_truth.json"
        assert expected["verdict"] in {
            v["expected"] for v in catalog["verdicts"].values()
        }, scenario_id


def test_category_b_platform_restrictions_are_honored_not_widened(
    pack: dict[str, Any],
) -> None:
    """A Category B scenario's own `platforms` declaration must survive into
    the pack exactly, the same way a Category A case's `PLATFORMS` does —
    never silently widened back to "no restriction".

    Regression: the generator used to hardcode `platforms: []` for every
    Category B entry regardless of what `scenarios.yaml` declared, on the
    since-invalidated assumption that no Category B fixture had
    platform-specific mechanics. A non-Linux CI runner would then execute a
    genuinely Linux-only (raw ELF) fixture, the skill would correctly report
    `NOT_VERIFIED` for the out-of-scope platform, and the grader would score
    that correct refusal as a wrong-verdict failure.
    """
    manifest = yaml.safe_load(
        (REPO / "agent-evals" / "skills" / "scenarios.yaml").read_text(encoding="utf-8")
    )
    declared = {
        s["id"]: s["platforms"] for s in manifest["scenarios"] if "platforms" in s
    }
    # Not vacuous: at least one Category B scenario actually declares a
    # restriction today.
    assert declared, "no scenario declares platforms — test would pass vacuously"
    for scenario_id, platforms in declared.items():
        assert pack["scenarios"][scenario_id]["category"] == "B", scenario_id
        assert pack["scenarios"][scenario_id]["platforms"] == platforms, scenario_id

    # Negative control, against the generator's own helper directly: every
    # current Category B scenario in this corpus happens to declare
    # `platforms` today, so there is no real undeclared example to check
    # against the committed pack — the fix changes what a *declared*
    # restriction means, not the default, and that default is checked here
    # regardless of corpus composition.
    assert gen._category_b_platforms({"category": "B", "id": "x"}) == []
    assert gen._category_b_platforms({"category": "B", "platforms": []}) == []
    assert gen._category_b_platforms(
        {"category": "B", "platforms": ["linux"]}
    ) == ["linux"]


def test_category_b_architecture_restrictions_are_honored_not_widened(
    pack: dict[str, Any],
) -> None:
    """A Category B scenario's own `architectures` declaration must survive
    into the pack exactly — the identical rule as `platforms`, one axis
    over (CPU rather than OS).

    Regression: `evidence-too-shallow` ships a committed, prebuilt x86_64
    binary as its old side (the fixture's whole premise is that only the
    binary survived, so there is no source to rebuild it from) while its
    new side is built from source on whichever host runs the harness. An
    OS-only `platforms: [linux]` restriction does not by itself guarantee
    one CPU architecture — this repository's CI runs both x86_64 and
    aarch64 Linux lanes — so on an aarch64 host the new side would build
    for arm64 against an x86_64 old side, producing a real cross-
    architecture break instead of the intended shallow-evidence result.
    """
    manifest = yaml.safe_load(
        (REPO / "agent-evals" / "skills" / "scenarios.yaml").read_text(encoding="utf-8")
    )
    declared = {
        s["id"]: s["architectures"]
        for s in manifest["scenarios"]
        if "architectures" in s
    }
    assert declared, "no scenario declares architectures — test would pass vacuously"
    for scenario_id, architectures in declared.items():
        assert pack["scenarios"][scenario_id]["category"] == "B", scenario_id
        assert (
            pack["scenarios"][scenario_id]["architectures"] == architectures
        ), scenario_id

    # Negative control: a Category B scenario that does *not* declare
    # `architectures` — every one of them except the fixture above — must
    # still resolve to unrestricted (`[]`).
    undeclared_b = [
        s["id"]
        for s in manifest["scenarios"]
        if s["category"] == "B" and "architectures" not in s
    ]
    assert undeclared_b, "no undeclared Category B scenario left to use as a control"
    for scenario_id in undeclared_b:
        assert pack["scenarios"][scenario_id]["architectures"] == [], scenario_id


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
    that `examples-ground-truth` then fails on. Injects a case-dir resolver
    (Phase 3, docs/contribute/plans/examples-catalog-split.md) rather than
    monkeypatching a flat `EXAMPLES` root -- `_scenario_digest`/
    `_fixture_paths` route production resolution through
    `example_catalog.case_dir` by default, so this is the same seam
    `test_fixture_sync.py` injects for `fixture_sync.sync_fixtures`."""
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
    resolver = lambda name: tmp_path / name  # noqa: E731

    before = gen._scenario_digest(scenario, catalog, case_dir=resolver)
    case_dir.mkdir()
    (case_dir / "old.h").write_text("int f(int);\n", encoding="utf-8")
    with_fixture = gen._scenario_digest(scenario, catalog, case_dir=resolver)
    (case_dir / "old.h").write_text("int f(long);\n", encoding="utf-8")
    edited_in_place = gen._scenario_digest(scenario, catalog, case_dir=resolver)

    assert before != with_fixture
    assert with_fixture != edited_in_place


def test_scenario_digest_covers_the_ground_truth_entry() -> None:
    """A Category A scenario derives its expected outcome from the catalog, so a
    catalog edit changes what the scenario expects without touching anything
    else the digest would otherwise see."""
    scenario = {
        "id": "removed-export",
        "category": "A",
        "skill": "check-abi-compatibility",
        "status": "ready",
        "case": "case01_symbol_removal",
    }
    catalog = example_catalog.load_ground_truth()
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
    surface_file = tmp_path / "surface.md"
    surface_file.write_text("one", encoding="utf-8")
    monkeypatch.setattr(surface, "SURFACE_SOURCES", (Path("surface.md"),))

    before = surface.surface_digest(tmp_path)
    assert surface.surface_digest(tmp_path) == before, (
        "not deterministic in one process"
    )
    surface_file.write_text("two", encoding="utf-8")
    assert surface.surface_digest(tmp_path) != before


def test_the_pack_does_not_depend_on_files_other_prs_change(
    pack: dict[str, Any],
) -> None:
    """The merge-skew property, as a check rather than a comment.

    `cli-reference.md` moves whenever any PR adds a CLI option — twice during
    this feature's own review. If the pack committed a digest of it, every such
    PR would owe a regeneration, and one could merge cleanly into a `main`
    whose pack is stale, because the two touch different files and git sees no
    conflict. So no pack entry may route to the CLI surface or the package
    tree; the checker computes that digest live instead.
    """
    volatile = {"abicheck_surface", "build"}
    assert not (volatile & set(pack["shared"])), (
        f"pack records a digest derived from files other PRs change: "
        f"{sorted(volatile & set(pack['shared']))}"
    )


def test_the_surface_digest_still_invalidates_bundles(
    pack: dict[str, Any], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Moving it out of the pack must not weaken it: a bundle recorded against
    a different surface is still stale, because the checker compares what the
    bundle recorded to what the surface is now."""
    bundle = _bundle(pack, hashes={"abicheck_surface": "sha256:" + "3" * 64})
    assert _check_bundle(monkeypatch, tmp_path, bundle) == 1


def test_digests_survive_a_crlf_checkout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Git converts LF to CRLF on Windows by default, so the same commit yields
    different bytes per host — and a committed digest over those bytes fails its
    own `--check` on one platform, which the Windows unit lane reported. The
    digest is a property of the repository, not of the checkout."""
    source = tmp_path / "surface.md"
    monkeypatch.setattr(surface, "SURFACE_SOURCES", (Path("surface.md"),))

    source.write_bytes(b"one\ntwo\n")
    lf = surface.surface_digest(tmp_path)
    source.write_bytes(b"one\r\ntwo\r\n")
    assert surface.surface_digest(tmp_path) == lf

    source.write_bytes(b"one\ntwo\nthree\n")
    assert surface.surface_digest(tmp_path) != lf, (
        "real content change must still move it"
    )


def test_an_observed_input_digest_survives_a_crlf_checkout(tmp_path: Path) -> None:
    """The same invariance, one level down, on the digest a *bundle* records.

    Missing it here is what failed the Windows unit lane after the committed
    pack digest was already fixed: the observed-input comparison had its own
    digest path, and a bundle whose inputs were digested raw would have read as
    stale on every host but the one that produced it."""
    source = tmp_path / "read.md"
    source.write_bytes(b"alpha\nbeta\n")
    lf = freshness._content_digest(source)
    source.write_bytes(b"alpha\r\nbeta\r\n")
    assert freshness._content_digest(source) == lf

    source.write_bytes(b"alpha\nbeta\ngamma\n")
    assert freshness._content_digest(source) != lf


def test_surface_sources_are_the_generated_projections() -> None:
    """Each is drift-gated against the live objects elsewhere in `pr`, which is
    what lets this digest be file-based without going blind to a real surface
    change: a renamed flag reaches `cli-reference.md` in the PR that renames
    it, or `gen_cli_reference.py --check` fails."""
    assert set(surface.SURFACE_SOURCES) == {
        Path("docs/reference/cli-reference.md"),
        Path("docs/reference/detector-spec.json"),
        Path("abicheck/schemas/compare_report.schema.json"),
    }
    for source in surface.SURFACE_SOURCES:
        assert (REPO / source).is_file(), source


def test_every_hashed_input_is_tracked_by_git_or_deterministically_generated() -> None:
    """A digest over a file git does not track, and that isn't reproducible from
    tracked content either, is a pack that differs between a clean checkout and
    a machine where something was built — `examples/**/*.so` is gitignored and
    would otherwise land in a scenario's fixture closure.

    The published skill trees (`.agents/skills/`, per the 2026-08-21 ADR-058
    amendment) are the one deliberate exception: they're no longer tracked by
    git, but they're pure build output of `skills-src/`, which *is* tracked —
    `gen_agent_skills.py --check` (exercised by
    `test_committed_pack_matches_the_generator`/`test_pack_is_loadable_json`
    above and by `tests/test_gen_agent_skills.py` directly) is what actually
    guards their reproducibility, not git-tracking. Everything else this test
    hashes (fixtures, harness sources, `abicheck/` itself) has no such
    generator and must still be tracked outright."""
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
    published_skill_paths: list[Path] = []
    for name in gen._published_skill_names():
        published_skill_paths += gen._tree_files(gen.PUBLISHED_SKILLS / name)

    hashed: list[Path] = list(published_skill_paths)
    for scenario in gen._load_scenarios()["scenarios"]:
        hashed += gen._fixture_paths(scenario)
    hashed += gen._expand(surface.SURFACE_SOURCES) + gen._expand(gen.HARNESS_SOURCES)
    # BUILD_SOURCES walks all of `abicheck/`, which is where install residue
    # would land — the remaining candidate for the cross-platform mismatch
    # that motivated this test.
    hashed += gen._expand(gen.BUILD_SOURCES)

    exempt = {p.relative_to(REPO).as_posix() for p in published_skill_paths}
    untracked = sorted(
        rel
        for p in hashed
        if (rel := p.relative_to(REPO).as_posix()) not in tracked and rel not in exempt
    )
    assert not untracked, f"untracked files reached a pack digest: {untracked}"


def test_drift_report_names_the_entry_that_moved() -> None:
    """A `--check` failure saying only 'out of date' is most of what made the
    cross-platform failure hard to diagnose."""
    committed = json.loads(PACK.read_text(encoding="utf-8"))
    mutated = json.loads(json.dumps(committed))
    mutated["shared"]["trigger_corpus"]["digest"] = "sha256:" + "f" * 64

    drift = gen.describe_drift(json.dumps(committed), json.dumps(mutated))
    assert any("trigger_corpus" in line for line in drift), drift


def test_the_pack_records_no_build_digest(pack: dict[str, Any]) -> None:
    """Digesting `abicheck/` here would make every PR touching the package
    regenerate the pack, and let two independently-green PRs merge into a stale
    one. It bought nothing: no routine check read the entry — it was
    publication-only — and publication computes the environment-bound value
    live instead."""
    assert "build" not in pack["shared"]
    assert gen.publication_build_digest().startswith("sha256:")


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
        ".agents/skills/check-abi-compatibility/SKILL.md",
        "agent-evals/skills/scenarios.yaml",
        "catalog/ground_truth.json",
        "catalog/cases/case01_symbol_removal/old.h",
        "tests/agent_skills/trigger_corpus.yaml",
        "docs/reference/cli-reference.md",
        # Routed via SURFACE_ROOTS: it is not hashed, but a change here is what
        # regenerates the reference that is.
        "abicheck/cli.py",
    ],
)
def test_known_inputs_route_to_a_hash(pack: dict[str, Any], path: str) -> None:
    """D6's completeness invariant, from the other direction: an input the
    evaluation reads that routes to no hash accepts evidence that no longer
    corresponds to anything — which the trigger corpus silently was."""
    assert freshness.route(path, freshness.hash_entries(pack)), path


def test_an_unrouted_input_is_reported(pack: dict[str, Any]) -> None:
    assert not freshness.route("some/unrelated/thing.txt", freshness.hash_entries(pack))


#: One file every synthetic bundle claims to have read.
SKILL_READ = ".agents/skills/check-abi-compatibility/SKILL.md"


def _observed(rel_path: str) -> dict[str, str]:
    """An observed-input record carrying the file's real current digest.

    Over *newline-normalized* content, which is what an observed digest means
    (see the bundle schema) and what the checker compares against. Digesting
    the raw bytes instead passed on Linux and failed the whole Windows unit
    lane, because git writes CRLF there — and it would have been worse than a
    test bug: a runner recording raw bytes on Windows would produce bundles
    that read as permanently stale on every other host.
    """
    import hashlib

    data = surface.normalize_newlines((REPO / rel_path).read_bytes())
    return {"path": rel_path, "digest": "sha256:" + hashlib.sha256(data).hexdigest()}


def _bundle(pack: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    skill = "check-abi-compatibility"
    scenario = "removed-export"
    hashes = {
        "skill_tree": pack["skills"][skill]["tree"],
        # Computed, not read from the pack — see skill_eval_surface.py.
        "abicheck_surface": freshness.hash_entries(pack)["abicheck_surface"]["digest"],
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
        # A real content digest, not a placeholder: the checker compares each
        # observed digest against the file's current content, which is what
        # catches an input whose routed hash is coarser than the file itself.
        "observed_inputs": [_observed(SKILL_READ)],
        **overrides,
    }


def _write_bundle(tmp_path: Path, bundle: dict[str, Any]) -> Path:
    meta = (
        tmp_path
        / "check-abi-compatibility"
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
    assert "check-abi-compatibility" in out


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
    a bundle cannot claim to be evidence for a skill it is not filed under.

    With only one skill published (ADR-058's 2026-08-20 portfolio-reset
    amendment), there is no second real skill's tree hash to borrow here — the
    checker itself does not special-case "another skill's real hash" versus
    "any other digest" (see `check_skill_eval_freshness.check_bundle`: the
    comparison is a plain string mismatch against the hash the bundle's own
    directory routes to), so a synthetic-but-well-formed digest exercises the
    identical code path a second real skill's tree hash would."""
    bundle = _bundle(pack, hashes={"skill_tree": "sha256:" + "9" * 64})
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
    """A scenario belonging to a different skill than the directory this
    bundle is filed under — evidence attributed to the wrong skill is how a
    skill ends up with coverage it never earned.

    With only one skill published (ADR-058's 2026-08-20 portfolio-reset
    amendment) every real scenario now names that same skill, so there is no
    real scenario belonging to "a different skill" to borrow here. A
    synthetic scenario, injected via a patched pack load, exercises the
    identical `scenario.get("skill") != skill` check in
    `check_skill_eval_freshness.check_bundle` that a second real skill's
    scenario would."""
    scenario_id = "synthetic-other-skill-scenario"
    synthetic = dict(pack)
    synthetic["scenarios"] = {
        **pack["scenarios"],
        scenario_id: {
            **pack["scenarios"]["removed-export"],
            "skill": "synthetic-other-skill",
        },
    }
    monkeypatch.setattr(freshness, "_load_pack", lambda: synthetic)
    bundle = _bundle(
        pack,
        hashes={
            "scenarios": {scenario_id: synthetic["scenarios"][scenario_id]["digest"]}
        },
    )
    bundle["scenario_id"] = scenario_id
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


def test_a_trigger_bundle_naming_no_real_prompt_is_rejected(
    pack: dict[str, Any], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The behavioral rule's counterpart for the other kind. Without it an
    invented id produced activation evidence that passed every check and could
    never be aggregated against the trigger it was supposed to exercise."""
    bundle = _bundle(pack)
    bundle["kind"] = "trigger"
    bundle["scenario_id"] = "positive-99"
    del bundle["hashes"]["scenarios"]
    assert _check_bundle(monkeypatch, tmp_path, bundle) == 1


def test_every_corpus_prompt_has_an_id_in_the_pack(pack: dict[str, Any]) -> None:
    """The ids are positional, which is only safe because the corpus digest is
    itself a pack entry: reordering renames ids *and* invalidates every trigger
    bundle in the same edit, so an id cannot come to mean a different prompt."""
    corpus = yaml.safe_load(
        (REPO / "tests" / "agent_skills" / "trigger_corpus.yaml").read_text(
            encoding="utf-8"
        )
    )
    ids = pack["shared"]["trigger_corpus"]["prompt_ids"]
    assert len(ids) == len(corpus["positive"]) + len(corpus["negative"])
    assert ids[0] == "positive-0"
    assert len(set(ids)) == len(ids)


@pytest.mark.parametrize(
    "dropped",
    [
        "schema_version",
        "kind",
        "scenario_id",
        "repetition",
        "agent",
        "model",
        "hashes",
        "observed_inputs",
    ],
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
    """Routing to *some* hash in the pack is not enough. A bundle that read
    a *different scenario's* fixture records only the scenario hash it
    actually named, so an edit to the scenario it merely happened to read
    leaves it 'fresh' — completeness is a property of this bundle's own
    recorded hashes, not of the pack."""
    bundle = _bundle(pack)
    # Real digest: the input is current, so the only thing wrong with it is
    # that no hash this bundle recorded covers it. A placeholder would fail
    # the content comparison instead and pass this test for the wrong reason.
    # `_bundle()` names the `removed-export` scenario only, so a file unique
    # to a different scenario's own fixture (`changed-signature`'s case
    # directory) routes to a scenario hash this bundle did not record.
    bundle["observed_inputs"].append(
        _observed("catalog/cases/case02_param_type_change/v1.h")
    )
    assert _check_bundle(monkeypatch, tmp_path, bundle) == 1


def test_a_trigger_bundle_needs_no_scenario_hash(
    pack: dict[str, Any], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A trigger run replays a corpus prompt and exercises no scenario.
    Requiring one would force the runner to attach an arbitrary unrelated
    scenario to every activation transcript, and then a scenario edit would
    invalidate trigger evidence that never touched it."""
    bundle = _bundle(pack)
    bundle["kind"] = "trigger"
    bundle["scenario_id"] = "positive-0"
    del bundle["hashes"]["scenarios"]
    assert _check_bundle(monkeypatch, tmp_path, bundle) == 0


def test_a_behavioral_bundle_still_needs_its_scenario_hash(
    pack: dict[str, Any], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    bundle = _bundle(pack)
    del bundle["hashes"]["scenarios"]
    assert _check_bundle(monkeypatch, tmp_path, bundle) == 1


def test_repetition_zero_is_valid(
    pack: dict[str, Any], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Presence, not truthiness: repetition 0 is the first of `k` runs, and a
    falsy check would reject a third of every suite."""
    bundle = _bundle(pack)
    bundle["repetition"] = 0
    assert _check_bundle(monkeypatch, tmp_path, bundle) == 0


def test_a_publication_build_hash_is_not_compared_against_the_pack(
    pack: dict[str, Any], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`hashes.build` is the environment-bound publication digest and the pack
    records no counterpart, so comparing it here would reject every valid
    Phase 6 bundle. Phase 6's own step is what verifies it."""
    bundle = _bundle(pack, hashes={"build": "sha256:" + "7" * 64})
    assert _check_bundle(monkeypatch, tmp_path, bundle) == 0


def test_an_observed_input_that_changed_since_the_run_fails(
    pack: dict[str, Any], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Routing is not freshness. Roots are coarser than the digests they route
    to — `scenarios.yaml` routes to every scenario, while each scenario's hash
    covers only its own record — so editing scenario B leaves A's hash
    untouched while the manifest A actually read changed underneath it."""
    bundle = _bundle(pack)
    bundle["observed_inputs"].append(
        {"path": "agent-evals/skills/scenarios.yaml", "digest": "sha256:" + "1" * 64}
    )
    assert _check_bundle(monkeypatch, tmp_path, bundle) == 1


def test_an_observed_input_that_no_longer_exists_fails(
    pack: dict[str, Any], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    bundle = _bundle(pack)
    bundle["observed_inputs"] = [
        {
            "path": ".agents/skills/deleted-skill/SKILL.md",
            "digest": "sha256:" + "2" * 64,
        }
    ]
    assert _check_bundle(monkeypatch, tmp_path, bundle) == 1


@pytest.mark.parametrize("kind", ["behavioural", "Behavioral", "", "scenario"])
def test_an_unknown_bundle_kind_fails(
    pack: dict[str, Any], monkeypatch: pytest.MonkeyPatch, tmp_path: Path, kind: str
) -> None:
    """Validating only presence let any non-empty typo read as not-behavioral:
    it needed no scenario hash and skipped every behavioral check, so a
    malformed bundle passed while being ungradeable by either contract."""
    bundle = _bundle(pack)
    bundle["kind"] = kind
    del bundle["hashes"]["scenarios"]
    assert _check_bundle(monkeypatch, tmp_path, bundle) == 1


def test_a_missing_surface_source_is_an_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Hashing b"" for an absent source would freeze the digest: a source another
    PR renames would keep producing a stable value that no longer tracks it, and
    every bundle would read as fresh across CLI changes."""
    monkeypatch.setattr(surface, "SURFACE_SOURCES", (Path("not-there.md"),))
    with pytest.raises(FileNotFoundError):
        surface.surface_digest(tmp_path)


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
