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


def test_a_new_operand_with_no_supported_input_is_a_zero_comparison_run(
    tmp_path: Path,
) -> None:
    """``discover_live_member_libraries`` returns no member when NEW holds
    nothing to compare. The run must read as a zero-comparison run -- a
    nonzero exit with ``no_comparison_completed`` -- never a clean pass."""
    old_dir, _new_dir = _write_release(tmp_path, _SPECS["versioned"])
    facts = _cli_captured_facts(tmp_path, old_dir)
    empty = tmp_path / "empty"
    empty.mkdir()
    (empty / "README.txt").write_text("not a library\n", encoding="utf-8")
    code, report = _compare_json(tmp_path, facts, empty, "stored")
    assert code != 0
    assert report["comparison_scope"]["no_comparison_completed"] is True
    assert report["run_outcome"]["operational"] == "no_comparison_completed"
    assert report["run_outcome"]["compatibility"] is None
    assert report["libraries"] == []


#: One-sided member files no reader can open: text, empty, binary junk,
#: JSON with no snapshot fingerprint, and a linker script naming nothing.
#: Each is discovered on its ``.so`` name alone (``classify``).
_UNREADABLE: dict[str, bytes] = {
    "notes.so": b"plain text\n",
    "libempty.so": b"",
    "libjunk.so.1": b"\x00\x01binary junk\xff" * 8,
    "libconfig.so": b'{"not": "a snapshot"}\n',
    "libdangling.so": b"INPUT(libnowhere.so.9)\n",
}

#: One-sided member files a reader *can* open: they stay ``not_supplied``.
#: The linker script names a real member, which extraction follows.
_READABLE_ONE_SIDED = {
    "libextra.so.5": snapshot_to_json(_snap("libextra.so.5", ["extra"])).encode(),
    "libalias.so": b"INPUT(libcore.so.1)\n",
}


def _scope_states(report: dict, name: str) -> list[str]:
    return [
        m["state"] for m in report["comparison_scope"]["members"] if m["name"] == name
    ]


def _member_outcome(code: int, report: dict, name: str) -> dict:
    """What a route decided about one member, independent of route wording."""
    return {
        "exit": code,
        "states": _scope_states(report, name),
        "library_verdicts": sorted(
            e.get("verdict") for e in report["libraries"] if e["library"] == name
        ),
        "operational": report["run_outcome"]["operational"],
        "scope": report["run_outcome"]["scope"],
        "unmatched_old": report["unmatched_old"],
        "unmatched_new": report["unmatched_new"],
    }


def _routes(tmp_path: Path, old_dir: Path, new_dir: Path, side: str) -> dict[str, dict]:
    """Every route that holds the member's side live. A stored OLD side
    (bundle facts) holds no file to probe, so OLD-only is live/live only."""
    routes = {"live": _compare_json(tmp_path, old_dir, new_dir, "live")}
    if side == "new":
        facts = _cli_captured_facts(tmp_path, old_dir)
        routes["stored"] = _compare_json(tmp_path, facts, new_dir, "stored")
    return routes


@pytest.mark.parametrize("side", ["new", "old"])
@pytest.mark.parametrize("name", list(_UNREADABLE))
def test_an_unreadable_one_sided_member_is_failed_on_every_route(
    tmp_path: Path, name: str, side: str
) -> None:
    """A one-sided member no reader can open is ``failed`` with an ``ERROR``
    library entry and an extraction-error operational outcome -- what the
    same file reads as once it pairs -- on both routes, never a plain
    ``not_supplied`` member. Oracle: the file content, chosen unreadable by
    construction."""
    old_dir, new_dir = _write_release(tmp_path, _SPECS["versioned"])
    ((new_dir if side == "new" else old_dir) / name).write_bytes(_UNREADABLE[name])

    outcomes = {
        route: _member_outcome(code, report, name)
        for route, (code, report) in _routes(tmp_path, old_dir, new_dir, side).items()
    }

    for outcome in outcomes.values():
        assert outcome["states"] == ["failed"]
        assert outcome["library_verdicts"] == ["ERROR"]
        assert outcome["operational"] == "extraction_error"
        assert outcome["scope"] == "incomplete"
        assert outcome[f"unmatched_{side}"] == [name]
    assert len({repr(o) for o in outcomes.values()}) == 1, outcomes


@pytest.mark.parametrize("name", list(_READABLE_ONE_SIDED))
def test_a_readable_one_sided_member_stays_not_supplied(
    tmp_path: Path, name: str
) -> None:
    """Negative control: a one-sided member a reader can open (a stored
    snapshot, a linker script naming a real member) is still a plain
    unmatched member, read the same on both routes."""
    old_dir, new_dir = _write_release(tmp_path, _SPECS["versioned"])
    (new_dir / name).write_bytes(_READABLE_ONE_SIDED[name])

    outcomes = {
        route: _member_outcome(code, report, name)
        for route, (code, report) in _routes(tmp_path, old_dir, new_dir, "new").items()
    }

    for outcome in outcomes.values():
        assert outcome["states"] == ["not_supplied"]
        assert outcome["library_verdicts"] == []
        assert outcome["unmatched_new"] == [name]
    assert outcomes["live"]["exit"] == outcomes["stored"]["exit"]
    assert outcomes["live"]["operational"] == outcomes["stored"]["operational"]


def test_an_unreadable_member_reads_the_same_paired_or_one_sided(
    tmp_path: Path,
) -> None:
    """The paired case (its extraction fails) and the one-sided case (its
    format probe fails) agree on the member's state, its library verdict and
    the run's operational outcome."""
    (tmp_path / "paired").mkdir()
    (tmp_path / "one").mkdir()
    paired_old, paired_new = _write_release(tmp_path / "paired", _SPECS["versioned"])
    (paired_old / "notes.so").write_text(
        snapshot_to_json(_snap("notes.so", ["n"])), encoding="utf-8"
    )
    (paired_new / "notes.so").write_bytes(_UNREADABLE["notes.so"])
    one_old, one_new = _write_release(tmp_path / "one", _SPECS["versioned"])
    (one_new / "notes.so").write_bytes(_UNREADABLE["notes.so"])

    paired = _member_outcome(
        *_compare_json(tmp_path / "paired", paired_old, paired_new, "p"), "notes.so"
    )
    one_sided = _member_outcome(
        *_compare_json(tmp_path / "one", one_old, one_new, "o"), "notes.so"
    )

    for key in ("exit", "states", "library_verdicts", "operational", "scope"):
        assert paired[key] == one_sided[key], key


#: The dry-run marker each real-run scope state corresponds to.
_DRY_RUN_MARKER = {
    "failed": "FAILED",
    "not_supplied": "MISSING",
    "available": "compare",
}


def _dry_run_markers(old_dir: Path, new_dir: Path) -> dict[str, str]:
    """``{member name: marker}`` from the dry-run's comparison plan."""
    result = CliRunner().invoke(
        main, ["compare", str(old_dir), str(new_dir), "--dry-run"]
    )
    assert result.exit_code == 0, result.output
    markers: dict[str, str] = {}
    for line in result.output.splitlines():
        marker, sep, rest = line.strip().partition(": ")
        if sep and marker in _DRY_RUN_MARKER.values() and " -- " in rest:
            markers[rest.split(" -- ", 1)[0]] = marker
    return markers


@pytest.mark.parametrize(
    ("name", "content", "side"),
    [
        *((n, c, s) for n, c in _UNREADABLE.items() for s in ("old", "new")),
        *((n, c, "new") for n, c in _READABLE_ONE_SIDED.items()),
    ],
)
def test_the_dry_run_previews_what_the_real_run_records(
    tmp_path: Path, name: str, content: bytes, side: str
) -> None:
    """Every member's dry-run marker matches the state the real run records
    for it: compared, one-sided, or failed because no reader can open it."""
    old_dir, new_dir = _write_release(tmp_path, _SPECS["versioned"])
    ((new_dir if side == "new" else old_dir) / name).write_bytes(content)

    markers = _dry_run_markers(old_dir, new_dir)
    _code, report = _compare_json(tmp_path, old_dir, new_dir, "real")
    states = {m["name"]: m["state"] for m in report["comparison_scope"]["members"]}

    assert markers == {
        member: _DRY_RUN_MARKER[state] for member, state in states.items()
    }


#: One unchanged member: nothing but the member under test moves the exit.
_UNCHANGED: Spec = {"libtop.so.1.2.3": _member(["top"], ["top"])}


@pytest.mark.parametrize("on_incomplete", ["warn", "block"])
@pytest.mark.parametrize(
    "shape", ["one_sided_unreadable", "paired_unreadable", "one_sided_readable"]
)
def test_the_exit_code_folds_the_operational_and_scope_axes(
    tmp_path: Path, shape: str, on_incomplete: str
) -> None:
    """docs/reference/exit-codes.md: a member whose acquisition failed is an
    operational error (4) on every `scope.on_incomplete` setting, and also
    an incomplete-scope member (0 under warn, 1 under block). A one-sided
    unreadable member reads exactly as the same file does paired; a readable
    one-sided member contributes the scope axis alone."""
    old_dir, new_dir = _write_release(tmp_path, _UNCHANGED)
    if shape == "paired_unreadable":
        (old_dir / "notes.so").write_text(
            snapshot_to_json(_snap("notes.so", ["n"])), encoding="utf-8"
        )
    content = (
        _READABLE_ONE_SIDED["libextra.so.5"]
        if shape == "one_sided_readable"
        else _UNREADABLE["notes.so"]
    )
    (
        new_dir / ("libextra.so.5" if shape == "one_sided_readable" else "notes.so")
    ).write_bytes(content)
    cfg = tmp_path / ".abicheck.yml"
    cfg.write_text(f"scope:\n  on_incomplete: {on_incomplete}\n", encoding="utf-8")
    out = tmp_path / "r.json"

    result = CliRunner().invoke(
        main,
        [
            "compare",
            str(old_dir),
            str(new_dir),
            "--config",
            str(cfg),
            "-o",
            f"json={out}",
        ],
    )
    exit_block = json.loads(out.read_text(encoding="utf-8"))["exit"]

    scope = 1 if on_incomplete == "block" else 0
    operational = 0 if shape == "one_sided_readable" else 4
    assert exit_block["incomplete_scope_contribution"] == scope
    assert exit_block["operational_error_contribution"] == operational
    assert result.exit_code == exit_block["code"] == max(scope, operational)
