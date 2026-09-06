"""Structural tests for the ABI/API failure taxonomy manifest and its page.

Phase 1 of `docs/contribute/plans/abi-api-knowledge-and-corpus.md`. The
taxonomy is a hand-authored judgment call, so nothing here checks whether the
*content* is right -- that is what review is for. What is mechanical, and what
Phases 2-4 will key their whole mapping off, is the id scheme and the rendered
page staying in sync with the manifest it is generated from; both are checked
here so a manifest edit that never regenerated the page fails in the fast lane
rather than silently publishing a stale table.
"""

from __future__ import annotations

import copy
import importlib.util
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "gen_abi_failure_taxonomy_doc.py"


def _load_generator():
    spec = importlib.util.spec_from_file_location(
        "gen_abi_failure_taxonomy_doc", SCRIPT
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


gen = _load_generator()


@pytest.fixture(scope="module")
def manifest() -> dict:
    return gen.load_manifest()


def test_committed_manifest_validates(manifest: dict) -> None:
    gen.validate(manifest)


def test_committed_page_is_in_sync(manifest: dict) -> None:
    assert gen.OUT_PATH.read_text(encoding="utf-8") == gen.render(manifest), (
        "docs/contribute/abi-api-failure-taxonomy.md is stale -- run "
        "`python scripts/gen_abi_failure_taxonomy_doc.py` and commit the result."
    )


def test_every_plan_branch_is_present(manifest: dict) -> None:
    """The thirteen branches the owning plan names all exist.

    The plan fixed the branch list and left the leaves to be written during
    the phase, so this pins the half that was agreed up front.
    """

    expected = {
        "symbol-identity",
        "function-calling-contract",
        "data-layout",
        "cpp-object-model",
        "inline-template-source-abi",
        "export-public-surface-contract",
        "dynamic-linker-contract",
        "dependency-abi",
        "toolchain-platform-abi",
        "multi-library-product-abi",
        "source-api-compatibility",
        "header-only-compatibility",
        "language-ecosystem-specific",
    }
    assert {b["id"] for b in manifest["branches"]} == expected


def test_ids_are_unique_and_branch_prefixed(manifest: dict) -> None:
    seen: set[str] = set()
    for branch in manifest["branches"]:
        for leaf in branch["leaves"]:
            assert leaf["id"] not in seen, f"duplicate leaf id {leaf['id']}"
            seen.add(leaf["id"])
            assert leaf["id"].startswith(branch["id"] + ".")
    assert len(seen) == sum(len(b["leaves"]) for b in manifest["branches"])


def test_every_leaf_carries_the_columns_later_phases_read(manifest: dict) -> None:
    for branch in manifest["branches"]:
        for leaf in branch["leaves"]:
            assert leaf["description"].endswith("."), leaf["id"]
            assert leaf["platforms"], leaf["id"]
            assert leaf["languages"], leaf["id"]
            assert leaf["surface"] in {"binary", "source", "both"}, leaf["id"]


def test_headline_counts_match_the_manifest(manifest: dict) -> None:
    """The rendered headline is what `doc-count-sync` anchors against."""

    text = gen.OUT_PATH.read_text(encoding="utf-8")
    leaves = re.search(r"\*\*(\d+) leaf mechanisms\*\*", text)
    branches = re.search(r"across \*\*(\d+) top-level branches\*\*", text)
    assert leaves and branches
    assert int(leaves.group(1)) == sum(len(b["leaves"]) for b in manifest["branches"])
    assert int(branches.group(1)) == len(manifest["branches"])


@pytest.mark.parametrize(
    "mutate,expected",
    [
        (
            lambda m: m["branches"][0]["leaves"][0].update(
                {"id": "Symbol-Identity.Foo"}
            ),
            "kebab",
        ),
        (
            lambda m: m["branches"][0]["leaves"][0].update({"id": "data-layout.stray"}),
            "branch id",
        ),
        (
            lambda m: m["branches"][0]["leaves"][0].update({"platforms": ["COFF"]}),
            "platform",
        ),
        (
            lambda m: m["branches"][0]["leaves"][0].update({"languages": ["Rust"]}),
            "language",
        ),
        (
            lambda m: m["branches"][0]["leaves"][0].update({"surface": "runtime"}),
            "surface",
        ),
        (
            lambda m: m["branches"][0]["leaves"][0].update({"description": ""}),
            "empty description",
        ),
        (
            lambda m: m["branches"][0]["leaves"][0].update({"title": "a | b"}),
            "rendered table",
        ),
        (lambda m: m["branches"][0].update({"leaves": []}), "no leaves"),
        (lambda m: m["branches"][0].update({"summary": "  "}), "empty summary"),
    ],
)
def test_validation_rejects_a_malformed_manifest(mutate, expected: str) -> None:
    """The id scheme is enforced, not merely documented.

    Phase 2/3 key every mapping off these ids, so a scheme violation has to
    fail at generation time rather than being discovered later by whichever
    consumer happened to parse the id first. Each case below mutates one field
    of a real copy of the committed manifest, so the check is exercised against
    the shape it actually guards.
    """

    broken = copy.deepcopy(gen.load_manifest())
    mutate(broken)
    with pytest.raises(gen.ManifestError, match=expected):
        gen.validate(broken)


def test_a_duplicate_leaf_id_is_rejected() -> None:
    broken = copy.deepcopy(gen.load_manifest())
    first, second = broken["branches"][0]["leaves"][:2]
    second["id"] = first["id"]
    with pytest.raises(gen.ManifestError, match="duplicate id"):
        gen.validate(broken)
