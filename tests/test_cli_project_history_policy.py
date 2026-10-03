# SPDX-License-Identifier: Apache-2.0
"""``project history --policy`` takes a policy document, as ``compare`` does.

Bug class: a setting the command accepts and never applies. ``--policy``'s
help said "same values as ``compare --policy``", but history passed the raw
string to ``compare()`` as a profile name, which reads an unknown name as
``strict_abi``: a document's ``overrides:`` never reached the pairwise
verdicts, and its ``versioning:`` block never reached the deprecation-window
check, so ``deprecation_compliance`` was always empty from the CLI.

Oracles, stated here rather than taken from the code:

* the deprecation-window rule as the user docs state it -- a removal
  deprecated at least ``min_releases`` releases earlier conforms; one
  deprecated later than that does not; one with no observed deprecation is
  ``unknown`` (never a proven violation) unless no window is required;
* a document with no ``versioning:`` block states no policy, so the list
  stays empty;
* a document's ``overrides:`` change the pairwise verdict ``compare`` reports
  for the same pair under the same document.
"""

from __future__ import annotations

import itertools
import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from abicheck.checker import compare
from abicheck.cli import main
from abicheck.model import AbiSnapshot, Function
from abicheck.policy_file import PolicyFile
from abicheck.serialization import save_snapshot

_RELEASES = ("1.0.0", "1.1.0", "1.2.0", "1.3.0")


def _chain(tmp_path: Path, deprecated_steps_before_removal: int | None) -> list[str]:
    """``old_api`` present in the first three releases and removed in the
    fourth, deprecated *deprecated_steps_before_removal* releases before its
    removal (``None``: never deprecated)."""
    removal = len(_RELEASES) - 1
    paths = []
    for index, version in enumerate(_RELEASES):
        functions = [Function(name="keep", mangled="keep", return_type="int")]
        if index < removal:
            deprecated = (
                deprecated_steps_before_removal is not None
                and index >= removal - deprecated_steps_before_removal
            )
            functions.append(
                Function(
                    name="old_api",
                    mangled="old_api",
                    return_type="int",
                    deprecated="use keep()" if deprecated else None,
                )
            )
        path = tmp_path / f"{version}.json"
        save_snapshot(
            AbiSnapshot(library="libx.so", version=version, functions=functions), path
        )
        paths.append(str(path))
    return paths


def _expected_status(steps: int | None, min_releases: int) -> str:
    if steps is None:
        return "conforming" if min_releases == 0 else "unknown"
    return "conforming" if steps >= min_releases else "non_conforming"


def _history(args: list[str]) -> dict:
    result = CliRunner().invoke(main, ["project", "history", *args])
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout)


@pytest.mark.parametrize(
    ("steps", "min_releases"),
    list(itertools.product((None, 1, 2), (0, 1, 2, 3))),
)
def test_the_document_s_deprecation_window_is_applied(
    tmp_path: Path, steps: int | None, min_releases: int
) -> None:
    policy = tmp_path / "policy.yaml"
    policy.write_text(
        f"versioning:\n  deprecation_window:\n    min_releases: {min_releases}\n",
        encoding="utf-8",
    )
    doc = _history([*_chain(tmp_path, steps), "--policy", str(policy)])
    found = [(f["display_name"], f["status"]) for f in doc["deprecation_compliance"]]
    assert found == [("old_api", _expected_status(steps, min_releases))]


def test_a_document_without_a_versioning_block_states_no_policy(tmp_path: Path) -> None:
    policy = tmp_path / "policy.yaml"
    policy.write_text("base_policy: strict_abi\n", encoding="utf-8")
    doc = _history([*_chain(tmp_path, None), "--policy", str(policy)])
    assert doc["deprecation_compliance"] == []


def test_a_profile_name_still_works_and_states_no_policy(tmp_path: Path) -> None:
    doc = _history([*_chain(tmp_path, 1), "--policy", "sdk_vendor"])
    assert doc["deprecation_compliance"] == []


def test_the_document_s_overrides_reach_every_pairwise_verdict(tmp_path: Path) -> None:
    policy = tmp_path / "policy.yaml"
    policy.write_text(
        "base_policy: strict_abi\noverrides:\n  func_removed: ignore\n",
        encoding="utf-8",
    )
    paths = _chain(tmp_path, None)
    with_doc = _history([*paths, "--policy", str(policy)])
    without = _history(paths)
    loaded = PolicyFile.load(policy)
    from abicheck.serialization import load_snapshot

    snaps = [load_snapshot(Path(p)) for p in paths]
    oracle = [
        compare(a, b, policy_file=loaded, scope_to_public_surface=True).verdict.value
        for a, b in zip(snaps, snaps[1:])
    ]
    assert [p["verdict"] for p in with_doc["pairwise"]] == oracle
    assert with_doc["pairwise"][-1]["verdict"] != without["pairwise"][-1]["verdict"]


def test_an_unreadable_document_is_a_usage_error(tmp_path: Path) -> None:
    result = CliRunner().invoke(
        main,
        [
            "project",
            "history",
            *_chain(tmp_path, None),
            "--policy",
            str(tmp_path / "missing.yaml"),
        ],
    )
    assert result.exit_code != 0
    assert "--policy" in result.output
