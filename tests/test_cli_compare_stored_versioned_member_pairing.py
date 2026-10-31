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

"""A stored ``BundleFacts`` OLD side pairs the members a live OLD side does.

Bug class: two routes of one release comparison derived their member sets
and keys separately. ``compare OLD_DIR NEW_DIR`` discovers NEW's members
with ``collect_release_inputs`` and keys them with ``build_match_map``; the
stored route ``compare OLD.bundle-facts.json NEW_DIR`` walked native
libraries only, so a NEW directory of per-library snapshots named
``libcore.so.1`` yielded no member, and a facts document keyed by a
member's file name (the typed-API capture) never met the canonical
``libcore.so`` key. Either way the run compared nothing and exited 1 where
the live run paired every library.

General invariant: for every release spec -- versioned, unversioned and
mixed member names -- and every way the OLD facts were captured (the CLI's
``--bundle-facts-out``, or ``capture_bundle_facts`` keyed by file name), the
stored/live run pairs exactly the members, with exactly the per-member
verdicts, surface changes, exit code and unmatched lists, that the live/live
run of the same two trees produces. The live/live route is the oracle.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from abicheck.cli import main
from abicheck.model import AbiSnapshot, Function, Param, Visibility
from abicheck.model.elf_facts import ElfMetadata
from abicheck.serialization import save_bundle_facts, snapshot_to_json
from abicheck.workflows.bundle_facts_capture import (
    capture_bundle_facts,
    facts_keyed_by_match_key,
)

Spec = dict[str, dict[str, list[str] | None]]


def _member(old: list[str], new: list[str] | None) -> dict[str, list[str] | None]:
    return {"old": old, "new": new}


#: Each spec: member file name -> functions per side. Every spec has a break,
#: an addition and an unchanged member, so a dropped pair moves the verdict.
_SPECS: dict[str, Spec] = {
    "versioned": {
        "libcore.so.1": _member(["core_a", "core_gone"], ["core_a", "core_new"]),
        "libmid.so.2": _member(["mid"], ["mid", "mid_new"]),
        "libtop.so.1.2.3": _member(["top"], ["top"]),
    },
    "unversioned": {
        "libcore.so": _member(["core_a", "core_gone"], ["core_a", "core_new"]),
        "libmid.so": _member(["mid"], ["mid", "mid_new"]),
        "libtop.so": _member(["top"], ["top"]),
    },
    "mixed": {
        "libcore.so.1": _member(["core_a", "core_gone"], ["core_a", "core_new"]),
        "libmid.so": _member(["mid"], ["mid", "mid_new"]),
        "libtop.so.4": _member(["top"], ["top"]),
    },
    "versioned-with-one-sided-member": {
        "libcore.so.1": _member(["core_a", "core_gone"], ["core_a", "core_new"]),
        "libold.so.3": _member(["old_only"], None),
    },
}


def _fn(name: str) -> Function:
    return Function(
        name=name,
        mangled=name,
        return_type="int",
        params=[Param(name="x", type="int")],
        visibility=Visibility.PUBLIC,
    )


def _snap(member: str, funcs: list[str]) -> AbiSnapshot:
    return AbiSnapshot(
        library=member,
        version="1",
        functions=[_fn(f) for f in funcs],
        elf=ElfMetadata(soname=member),
    )


def _write_release(tmp_path: Path, spec: Spec) -> tuple[Path, Path]:
    old_dir, new_dir = tmp_path / "old", tmp_path / "new"
    old_dir.mkdir()
    new_dir.mkdir()
    for member, sides in spec.items():
        for side, directory in (("old", old_dir), ("new", new_dir)):
            funcs = sides[side]
            if funcs is not None:
                (directory / member).write_text(
                    snapshot_to_json(_snap(member, funcs)), encoding="utf-8"
                )
    return old_dir, new_dir


def _compare_json(tmp_path: Path, old: Path, new: Path, name: str) -> tuple[int, dict]:
    out = tmp_path / f"{name}.json"
    result = CliRunner().invoke(
        main, ["compare", str(old), str(new), "-o", f"json={out}"]
    )
    if result.exception and not isinstance(result.exception, SystemExit):
        raise result.exception
    return result.exit_code, json.loads(out.read_text(encoding="utf-8"))


def _pairing(code: int, report: dict) -> dict:
    """What a comparison decided, independent of how its OLD side was read."""
    return {
        "exit": code,
        "verdict": report["verdict"],
        "members": sorted(
            (entry["library"], entry["verdict"]) for entry in report["libraries"]
        ),
        "surface_changes": report["surface_changes"],
        "unmatched_old": sorted(report["unmatched_old"]),
        "unmatched_new": sorted(report["unmatched_new"]),
        # Who is on which side, keyed and named. Not each member's *state*:
        # a stored capture may assert a complete inventory a live directory
        # cannot, which legitimately proves more (ADR-065 D2).
        "scope_members": sorted(
            (m["member"], m["name"], m["old_present"], m["new_present"])
            for m in report["comparison_scope"]["members"]
        ),
    }


def _cli_captured_facts(tmp_path: Path, old_dir: Path) -> Path:
    facts = tmp_path / "cli.bundle-facts.json"
    result = CliRunner().invoke(
        main,
        [
            "compare",
            str(old_dir),
            str(old_dir),
            "--bundle-facts-out",
            str(facts),
            "-o",
            "oneline=-",
        ],
    )
    # An unreadable member makes the capturing self-compare exit 4, but the
    # facts (with that member marked degraded) are still written.
    assert facts.is_file(), result.output
    return facts


def _api_captured_facts(tmp_path: Path, old_dir: Path, spec: Spec) -> Path:
    """A typed-API capture keyed by each member's *file name*."""
    snapshots = {m: _snap(m, s["old"]) for m, s in spec.items() if s["old"] is not None}
    facts = tmp_path / "api.bundle-facts.json"
    save_bundle_facts(capture_bundle_facts(snapshots, inventory_complete=True), facts)
    return facts


@pytest.mark.parametrize("capture", ["cli", "api"])
@pytest.mark.parametrize("spec_name", sorted(_SPECS))
def test_stored_old_pairs_exactly_what_live_old_pairs(
    tmp_path: Path, spec_name: str, capture: str
) -> None:
    spec = _SPECS[spec_name]
    old_dir, new_dir = _write_release(tmp_path, spec)
    live = _pairing(*_compare_json(tmp_path, old_dir, new_dir, "live"))
    # The oracle itself must pair every two-sided member and break.
    two_sided = sorted(m for m, s in spec.items() if s["new"] is not None)
    assert [m for m, _ in live["members"]] == two_sided
    assert live["exit"] == 4

    facts = (
        _cli_captured_facts(tmp_path, old_dir)
        if capture == "cli"
        else _api_captured_facts(tmp_path, old_dir, spec)
    )
    stored = _pairing(*_compare_json(tmp_path, facts, new_dir, "stored"))
    assert stored == live


class TestFactsKeyedByMatchKey:
    """The re-keying primitive, against ``build_match_map``'s own keys."""

    @pytest.mark.parametrize(
        "names",
        [
            ("libcore.so.1", "libmid.so"),
            ("libcore.so.1.2", "libmid.so.7", "libz.so"),
            ("libplain.so",),
        ],
    )
    def test_keys_match_the_live_match_map(
        self, tmp_path: Path, names: tuple[str, ...]
    ) -> None:
        from abicheck.binary_utils import build_match_map

        files = []
        for name in names:
            (tmp_path / name).write_text(
                snapshot_to_json(_snap(name, ["f"])), encoding="utf-8"
            )
            files.append(tmp_path / name)
        live_keys, _ = build_match_map(files)
        facts = capture_bundle_facts({n: _snap(n, ["f"]) for n in names})
        rekeyed = facts_keyed_by_match_key(facts)
        assert set(rekeyed.per_library_snapshots) == set(live_keys)
        # The versioned spelling survives for SONAME skew.
        for name in names:
            key = next(k for k, p in live_keys.items() if p.name == name)
            assert rekeyed.library_filenames.get(key, key) == name

    def test_two_versions_of_one_library_keep_their_names(self) -> None:
        facts = capture_bundle_facts(
            {
                "libfoo.so.1": _snap("libfoo.so.1", ["f"]),
                "libfoo.so.2": _snap("libfoo.so.2", ["f"]),
            }
        )
        assert facts_keyed_by_match_key(facts) is facts


def test_file_name_keyed_facts_pair_with_the_canonical_live_side(
    tmp_path: Path,
) -> None:
    """The typed-API capture keys members by file name (``libcore.so.1``),
    the live NEW side by ``build_match_map``'s canonical key (``libcore.so``).
    The document really is file-name keyed, and still pairs every member."""
    spec = _SPECS["versioned"]
    old_dir, new_dir = _write_release(tmp_path, spec)
    facts = _api_captured_facts(tmp_path, old_dir, spec)
    written = json.loads(facts.read_text(encoding="utf-8"))
    assert sorted(written["per_library_snapshots"]) == sorted(spec)
    code, report = _compare_json(tmp_path, facts, new_dir, "stored")
    assert code == 4
    assert sorted(e["library"] for e in report["libraries"]) == sorted(spec)
    assert report["unmatched_old"] == [] and report["unmatched_new"] == []
    assert {m["member"] for m in report["comparison_scope"]["members"]} == {
        "libcore.so",
        "libmid.so",
        "libtop.so",
    }


@pytest.mark.parametrize(
    "old_notes",
    ["unreadable", "snapshot"],
    ids=["unreadable-both-sides", "unreadable-new-only"],
)
def test_a_non_binary_library_name_is_a_failed_member_on_both_routes(
    tmp_path: Path, old_notes: str
) -> None:
    """A text file named like a library (``notes.so``) is a member on both
    routes, and on both it is an explicit ``failed`` member with an
    operational extraction error -- never an empty, clean one (a failed
    extractor is an error, not an empty surface)."""
    old_dir, new_dir = _write_release(tmp_path, _SPECS["versioned"])
    if old_notes == "snapshot":
        (old_dir / "notes.so").write_text(
            snapshot_to_json(_snap("notes.so", ["n"])), encoding="utf-8"
        )
    else:
        (old_dir / "notes.so").write_text("plain text\n", encoding="utf-8")
    (new_dir / "notes.so").write_text("plain text\n", encoding="utf-8")

    def _notes_state(report: dict) -> list[str]:
        return [
            m["state"]
            for m in report["comparison_scope"]["members"]
            if m["name"] == "notes.so"
        ]

    live_code, live = _compare_json(tmp_path, old_dir, new_dir, "live")
    facts = _cli_captured_facts(tmp_path, old_dir)
    stored_code, stored = _compare_json(tmp_path, facts, new_dir, "stored")
    for report in (live, stored):
        assert _notes_state(report) == ["failed"]
        assert report["run_outcome"]["scope"] == "incomplete"
        assert report["comparison_scope"]["completeness"] != "complete"
    assert live_code == stored_code == 4
    if old_notes == "snapshot":
        # NEW's extraction failed in this run on both routes.
        for report in (live, stored):
            assert report["run_outcome"]["operational"] == "extraction_error"
            assert ("notes.so", "ERROR") in {
                (e["library"], e["verdict"]) for e in report["libraries"]
            }
