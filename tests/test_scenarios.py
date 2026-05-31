# Copyright 2026 Nikolay Petrov
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

"""End-to-end driver for the user-scenario catalog (tests/scenarios/*.yaml).

Each `automated` scenario is exercised here by invoking the abicheck CLI through
Click's CliRunner against JSON snapshots — proving abicheck works as a *scanner
tool* (exit codes, public-surface scoping, SARIF, gating, offline snapshots),
not only as an ABI/API-change detector. The catalog is also structurally
validated and kept in sync with the use-case registry.

The catalog is an *internal validation* asset (it drives these tests), split
into grouped YAML files under ``tests/scenarios/`` that are merged by globbing —
so it scales to many scenarios without one giant file. See
``tests/scenarios/README.md``.

Adding a missed usage scenario (e.g. issue #235) here makes it a permanent
end-to-end regression guard.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from abicheck.cli import main
from abicheck.model import (
    AbiSnapshot,
    EnumMember,
    EnumType,
    Function,
    Param,
    RecordType,
    TypeField,
    Visibility,
)
from abicheck.serialization import save_snapshot

_REPO = Path(__file__).parent.parent
_CATALOG_DIR = Path(__file__).parent / "scenarios"
_REGISTRY = _REPO / "docs" / "development" / "usecase-registry.yaml"
_ID_RE = re.compile(r"^SC-[A-Z0-9-]+$")


# ── catalog loading ──────────────────────────────────────────────────────────


def _scenarios() -> list[dict]:
    """Merge every grouped catalog file under tests/scenarios/*.yaml."""
    files = sorted(_CATALOG_DIR.glob("*.yaml"))
    assert files, "no scenario catalog files found under tests/scenarios/"
    items: list[dict] = []
    for fp in files:
        data = yaml.safe_load(fp.read_text(encoding="utf-8"))
        assert isinstance(data, dict) and "scenarios" in data, (
            f"{fp.name}: needs a top-level 'scenarios' list"
        )
        group = data["scenarios"]
        assert isinstance(group, list) and group, (
            f"{fp.name}: scenarios must be non-empty"
        )
        for sc in group:
            sc["_source"] = fp.name
            items.append(sc)
    return items


def _registry_ids() -> set[str]:
    data = yaml.safe_load(_REGISTRY.read_text(encoding="utf-8"))
    return {c["id"] for c in data["use_cases"]}


# ── snapshot fixtures (no castxml/gcc needed) ────────────────────────────────


def _fn(name: str, ret: str = "int", params: tuple[str, ...] = ()) -> Function:
    return Function(
        name=name,
        mangled=name,
        return_type=ret,
        params=[Param(name=f"a{i}", type=t) for i, t in enumerate(params)],
        visibility=Visibility.PUBLIC,
    )


def _rec(name: str, size: int = 64) -> RecordType:
    return RecordType(
        name=name,
        kind="struct",
        size_bits=size,
        fields=[TypeField(name="x", type="int")],
    )


def _lib(version: str, funcs: list[Function], **kw: object) -> AbiSnapshot:
    return AbiSnapshot(library="libfoo.so", version=version, functions=funcs, **kw)


def _save(snap: AbiSnapshot, path: Path) -> str:
    save_snapshot(snap, path)
    return str(path)


def _cli(*args: str):
    return CliRunner().invoke(main, list(args))


def _compare(tmp_path: Path, old: AbiSnapshot, new: AbiSnapshot, *args: str):
    o = _save(old, tmp_path / "old.json")
    n = _save(new, tmp_path / "new.json")
    return _cli("compare", o, n, *args)


# ── catalog structure & registry sync ────────────────────────────────────────


def test_catalog_parses_and_ids_unique() -> None:
    ids = [s["id"] for s in _scenarios()]
    assert len(ids) == len(set(ids)), "duplicate scenario ids"
    for sid in ids:
        assert _ID_RE.match(sid), f"id not in SC-… form: {sid}"


@pytest.mark.parametrize("sc", _scenarios(), ids=lambda s: s["id"])
def test_scenario_is_well_formed(sc: dict) -> None:
    for key in ("id", "title", "persona", "narrative", "flow", "validates"):
        assert sc.get(key), f"{sc['id']}: missing/empty {key!r}"
    assert isinstance(sc["flow"], list) and sc["flow"], (
        f"{sc['id']}: flow must be a list"
    )
    assert sc["validates"] in _registry_ids(), (
        f"{sc['id']}: validates={sc['validates']!r} is not a usecase-registry id"
    )


@pytest.mark.parametrize("sc", _scenarios(), ids=lambda s: s["id"])
def test_scenario_automation_is_consistent(sc: dict) -> None:
    if sc.get("automated"):
        assert "test" in sc, f"{sc['id']}: automated scenario needs a test"
        assert sc["test"] in globals(), (
            f"{sc['id']}: test {sc['test']!r} not found in this module"
        )
        assert sc.get("expected"), f"{sc['id']}: automated scenario needs expected"
    else:
        assert sc.get("status") == "planned", (
            f"{sc['id']}: non-automated scenarios must be status: planned"
        )
        plan = sc.get("plan", "")
        assert (plan and (_REPO / plan).exists()) or sc.get("note", "").strip(), (
            f"{sc['id']}: planned scenario needs an existing plan or a note"
        )


def test_every_scenario_test_maps_to_a_scenario() -> None:
    referenced = {s["test"] for s in _scenarios() if s.get("automated")}
    defined = {name for name in globals() if name.startswith("test_sc_")}
    orphans = defined - referenced
    assert not orphans, f"test_sc_* functions with no scenario entry: {orphans}"


# ── automated scenario flows (the end-to-end scanner validation) ─────────────


def test_sc_ci_gate_breaking(tmp_path: Path) -> None:
    res = _compare(tmp_path, _lib("1", [_fn("a"), _fn("b")]), _lib("2", [_fn("a")]))
    assert res.exit_code == 4
    assert "BREAKING" in res.output


def test_sc_ci_gate_additive(tmp_path: Path) -> None:
    res = _compare(tmp_path, _lib("1", [_fn("a")]), _lib("2", [_fn("a"), _fn("c")]))
    assert res.exit_code == 0
    assert "COMPATIBLE" in res.output


def test_sc_exit_contract(tmp_path: Path) -> None:
    # NO_CHANGE -> 0
    same = [_fn("a")]
    assert (
        _compare(tmp_path, _lib("1", list(same)), _lib("2", list(same))).exit_code == 0
    )
    # API_BREAK -> 2 (enum member renamed, same value: source break, binary OK)
    e1 = EnumType(
        name="Mode",
        members=[EnumMember("A", 0), EnumMember("B", 1)],
        underlying_type="int",
    )
    e2 = EnumType(
        name="Mode",
        members=[EnumMember("A", 0), EnumMember("RENAMED", 1)],
        underlying_type="int",
    )
    api = _compare(
        tmp_path,
        _lib("1", [_fn("use")], enums=[e1]),
        _lib("2", [_fn("use")], enums=[e2]),
    )
    assert api.exit_code == 2
    # BREAKING -> 4 (removed symbol)
    assert (
        _compare(
            tmp_path, _lib("1", [_fn("a"), _fn("b")]), _lib("2", [_fn("a")])
        ).exit_code
        == 4
    )


def test_sc_public_surface_scope(tmp_path: Path) -> None:
    # Public API: api_call(Config*) -> Result*. InternalCache is reachable from
    # nothing public — a change to it is a *private* break.
    pub = [_fn("api_call", ret="Result *", params=("Config *",))]
    old = _lib(
        "1",
        list(pub),
        types=[_rec("Result"), _rec("Config"), _rec("InternalCache", 64)],
    )
    new = _lib(
        "2",
        list(pub),
        types=[_rec("Result"), _rec("Config"), _rec("InternalCache", 128)],
    )

    # Without scoping the private break is a compliance error (issue #235).
    assert _compare(tmp_path, old, new).exit_code == 4
    # With public-header scoping it must NOT raise compliance errors: the only
    # change was to a private type, so the verdict is NO_CHANGE (exit 0). Assert
    # the verdict cell, not the word "BREAKING" (which also appears in the
    # severity legend).
    scoped = _compare(tmp_path, old, new, "--scope-public-headers")
    assert scoped.exit_code == 0
    assert "`NO_CHANGE`" in scoped.output
    # The private change is recorded as filtered, not dropped.
    filtered = _compare(tmp_path, old, new, "--scope-public-headers", "--show-filtered")
    assert "InternalCache" in filtered.output


def test_sc_scan_sarif(tmp_path: Path) -> None:
    res = _compare(
        tmp_path,
        _lib("1", [_fn("a"), _fn("b")]),
        _lib("2", [_fn("a")]),
        "--format",
        "sarif",
    )
    assert res.exit_code == 4
    doc = json.loads(res.output)
    assert doc.get("version") == "2.1.0"
    assert doc.get("runs"), "SARIF must contain runs"


def test_sc_release_recommendation(tmp_path: Path) -> None:
    res = _compare(
        tmp_path,
        _lib("1", [_fn("a"), _fn("b")]),
        _lib("2", [_fn("a")]),
        "--format",
        "json",
    )
    assert res.exit_code == 4
    doc = json.loads(res.output)
    assert doc["release_recommendation"]["version_bump"] == "major"


def test_sc_accept_known_break(tmp_path: Path) -> None:
    sup = tmp_path / "suppressions.yaml"
    sup.write_text(
        "version: 1\n"
        "suppressions:\n"
        '  - symbol: "deprecated_fn"\n'
        '    change_kind: "func_removed"\n'
        '    reason: "Removed deprecated entry point in the v2 API cleanup"\n',
        encoding="utf-8",
    )
    res = _compare(
        tmp_path,
        _lib("1", [_fn("a"), _fn("deprecated_fn")]),
        _lib("2", [_fn("a")]),
        "--suppress",
        str(sup),
    )
    assert res.exit_code == 0


def test_sc_offline_snapshot(tmp_path: Path) -> None:
    # Snapshots are the portable interchange format: compare two stored JSON
    # snapshots offline, no binaries or castxml required.
    res = _compare(tmp_path, _lib("1", [_fn("a"), _fn("b")]), _lib("2", [_fn("a")]))
    assert res.exit_code == 4
    assert "BREAKING" in res.output


def test_sc_ci_severity_gate(tmp_path: Path) -> None:
    old, new = _lib("1", [_fn("a"), _fn("b")]), _lib("2", [_fn("a")])
    # Default gate fails on the ABI break …
    assert _compare(tmp_path, old, new).exit_code == 4
    # … but downgrading abi_breaking to a warning passes (severity-aware scheme).
    assert (
        _compare(tmp_path, old, new, "--severity-abi-breaking", "warning").exit_code
        == 0
    )


def test_sc_ci_stat(tmp_path: Path) -> None:
    res = _compare(
        tmp_path, _lib("1", [_fn("a"), _fn("b")]), _lib("2", [_fn("a")]), "--stat"
    )
    assert res.exit_code == 4
    assert "total" in res.output
    assert res.output.count("\n") <= 1  # one-line summary


def test_sc_suppression_expiry(tmp_path: Path) -> None:
    expired = tmp_path / "expired.yaml"
    expired.write_text(
        "version: 1\n"
        "suppressions:\n"
        '  - symbol: "b"\n'
        '    change_kind: "func_removed"\n'
        '    reason: "stale waiver"\n'
        '    expires: "2000-01-01"\n',
        encoding="utf-8",
    )
    res = _compare(
        tmp_path,
        _lib("1", [_fn("a"), _fn("b")]),
        _lib("2", [_fn("a")]),
        "--suppress",
        str(expired),
        "--strict-suppressions",
    )
    assert res.exit_code == 1  # the expired waiver fails the run


def test_sc_policy_profile(tmp_path: Path) -> None:
    e1 = EnumType(
        name="Mode",
        members=[EnumMember("A", 0), EnumMember("B", 1)],
        underlying_type="int",
    )
    e2 = EnumType(
        name="Mode",
        members=[EnumMember("A", 0), EnumMember("RENAMED", 1)],
        underlying_type="int",
    )
    old = _lib("1", [_fn("use")], enums=[e1])
    new = _lib("2", [_fn("use")], enums=[e2])
    assert _compare(tmp_path, old, new).exit_code == 2  # strict_abi: API break
    assert _compare(tmp_path, old, new, "--policy", "sdk_vendor").exit_code == 0


def test_sc_scan_junit(tmp_path: Path) -> None:
    res = _compare(
        tmp_path,
        _lib("1", [_fn("a"), _fn("b")]),
        _lib("2", [_fn("a")]),
        "--format",
        "junit",
    )
    assert res.exit_code == 4
    assert "<testsuites" in res.output


def test_sc_scan_html(tmp_path: Path) -> None:
    res = _compare(
        tmp_path,
        _lib("1", [_fn("a"), _fn("b")]),
        _lib("2", [_fn("a")]),
        "--format",
        "html",
    )
    assert res.exit_code == 4
    assert "<!DOCTYPE html>" in res.output


def test_sc_baseline_registry(tmp_path: Path) -> None:
    reg = str(tmp_path / "registry")
    v1 = _save(_lib("1", [_fn("a"), _fn("b")]), tmp_path / "v1.json")
    v2 = _save(_lib("2", [_fn("a")]), tmp_path / "v2.json")
    pinned = str(tmp_path / "pinned.json")

    assert (
        _cli(
            "baseline",
            "push",
            "libfoo",
            "--version",
            "1",
            "--platform",
            "linux-x86_64",
            "--snapshot",
            v1,
            "--registry",
            reg,
        ).exit_code
        == 0
    )
    assert (
        _cli(
            "baseline", "pull", "libfoo:1:linux-x86_64", "-o", pinned, "--registry", reg
        ).exit_code
        == 0
    )
    # Gate a new build against the pinned baseline pulled from the registry.
    assert _cli("compare", pinned, v2).exit_code == 4
