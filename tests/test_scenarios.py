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
from abicheck.dwarf_advanced import AdvancedDwarfMetadata
from abicheck.model import (
    AbiSnapshot,
    EnumMember,
    EnumType,
    Function,
    Param,
    RecordType,
    TypeField,
    Variable,
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


def _vfn(mangled: str, vtable_index: int) -> Function:
    return Function(
        name=mangled,
        mangled=mangled,
        return_type="void",
        params=[],
        visibility=Visibility.PUBLIC,
        is_virtual=True,
        vtable_index=vtable_index,
    )


def _var(name: str, type: str = "int") -> Variable:
    return Variable(name=name, mangled=name, type=type, visibility=Visibility.PUBLIC)


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
    # Mode isn't referenced by any public function here, so opt out of the
    # default public-header scoping (ADR-024 Phase 5) to exercise the contract.
    api = _compare(
        tmp_path,
        _lib("1", [_fn("use")], enums=[e1]),
        _lib("2", [_fn("use")], enums=[e2]),
        "--no-scope-public-headers",
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
    # Scoping is on by default since ADR-024 Phase 5, so opt out explicitly here.
    assert _compare(tmp_path, old, new, "--no-scope-public-headers").exit_code == 4
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


def test_sc_public_surface_scope_fallback(tmp_path: Path) -> None:
    # The "don't overclaim" half of issue #235. With no Visibility.PUBLIC
    # symbols the public surface is unresolvable, so --scope-public-headers must
    # fall back to the full export table rather than silently report a clean
    # public surface: the private break is KEPT, the JSON records the fallback
    # as manual-review-required, and a warning is emitted to stderr.
    old = _lib("1", [], types=[_rec("InternalCache", 64)])
    new = _lib("2", [], types=[_rec("InternalCache", 128)])
    res = _compare(tmp_path, old, new, "--scope-public-headers", "--format", "json")
    assert res.exit_code == 4  # fallback kept the break → still gated
    doc = json.loads(res.stdout)
    assert doc["scope"]["resolved"] is False
    assert doc["scope"]["fell_back"] is True
    assert doc["scope"]["manual_review_required"] is True
    # The private change is kept (fallback), never silently dropped.
    blob = json.dumps(doc["changes"])
    assert "InternalCache" in blob
    # Human-facing warning on stderr (machine contract is the scope block above).
    assert "could not resolve the public surface" in res.stderr


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
    # Mode isn't referenced by a public function, so opt out of default
    # public-header scoping (ADR-024 Phase 5) to exercise the policy contrast.
    assert (
        _compare(tmp_path, old, new, "--no-scope-public-headers").exit_code == 2
    )  # strict_abi: API break
    assert (
        _compare(
            tmp_path, old, new, "--no-scope-public-headers", "--policy", "sdk_vendor"
        ).exit_code
        == 0
    )


def test_sc_probe_matrix_into_compare(tmp_path: Path) -> None:
    # G2: build-config findings (here a raised C++ standard floor) need a
    # multi-config probe matrix that plain compare does not have. Passing the
    # matrices folds those findings into the mainline gate: a comparison that is
    # NO_CHANGE on the binary surface becomes API_BREAK once the matrix raises
    # the floor 17 -> 20.
    from abicheck.probe_harness import (
        MatrixSnapshot,
        ProbeResult,
        write_matrix_snapshot,
    )

    same = [_fn("a")]
    o = _save(_lib("1", list(same)), tmp_path / "o.json")
    n = _save(_lib("2", list(same)), tmp_path / "n.json")
    # Baseline: identical surfaces → NO_CHANGE.
    assert _cli("compare", o, n).exit_code == 0

    def _matrix(version: str, std: int) -> MatrixSnapshot:
        return MatrixSnapshot(
            library="libfoo",
            version=version,
            spec_name="t",
            cxx_stds={"a": std},
            results=[
                ProbeResult(
                    configuration_id="a",
                    probe_id="p0",
                    snapshot=_lib(version, list(same)),
                )
            ],
        )

    om = str(tmp_path / "om.json")
    nm = str(tmp_path / "nm.json")
    write_matrix_snapshot(_matrix("1", 17), om)
    write_matrix_snapshot(_matrix("2", 20), nm)

    res = _cli(
        "compare",
        o,
        n,
        "--format",
        "json",
        "--probe-matrix-old",
        om,
        "--probe-matrix-new",
        nm,
    )
    assert res.exit_code == 2  # API_BREAK from the build-config finding
    doc = json.loads(res.stdout)
    assert doc["verdict"] == "API_BREAK"
    assert any(c["kind"] == "cxx_standard_floor_raised" for c in doc["changes"])


def test_sc_review_digest(tmp_path: Path) -> None:
    # The review digest is the GitHub-facing presentation layer: verdict +
    # merge effect, a counts table, and the release recommendation.
    res = _compare(
        tmp_path,
        _lib("1", [_fn("a"), _fn("b")]),
        _lib("2", [_fn("a")]),
        "--format",
        "review",
    )
    assert res.exit_code == 4
    out = res.output
    assert "ABI review" in out
    assert "`BREAKING`" in out
    assert "Release recommendation:" in out
    assert "| Category | Count |" in out
    # The removed symbol is surfaced as a top impacted symbol.
    assert "func_removed" in out


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


def test_sc_consume_json(tmp_path: Path) -> None:
    # The JSON report is the machine decision contract any non-trivial gate keys
    # off (exit 0 alone is lossy). It must be a stable, versioned document with a
    # top-level verdict and a per-finding changes list.
    res = _compare(
        tmp_path,
        _lib("1", [_fn("a"), _fn("b")]),
        _lib("2", [_fn("a")]),
        "--format",
        "json",
    )
    assert res.exit_code == 4
    doc = json.loads(res.output)
    assert doc["report_schema_version"]  # versioned contract
    assert doc["verdict"] == "BREAKING"
    assert isinstance(doc["changes"], list) and doc["changes"]


def test_sc_malformed_input(tmp_path: Path) -> None:
    # Degraded-mode contract: a corrupt snapshot must fail cleanly (exit 1 with a
    # diagnostic), never crash or silently read as compatible (exit 0).
    good = _save(_lib("1", [_fn("a")]), tmp_path / "good.json")
    corrupt = tmp_path / "corrupt.json"
    corrupt.write_text("{ not valid json ]", encoding="utf-8")
    res = _cli("compare", good, str(corrupt))
    assert res.exit_code == 1
    assert "Failed to load" in res.output


def test_sc_c_struct_layout(tmp_path: Path) -> None:
    # Pure-C archetype: a public function takes Point by value; growing Point
    # changes its size, so every caller is ABI-incompatible — no symbol added or
    # removed, yet a binary break (type_size_changed → BREAKING).
    def _point(size: int, fields: list[TypeField]) -> RecordType:
        return RecordType(name="Point", kind="struct", size_bits=size, fields=fields)

    pub = [_fn("use_point", ret="int", params=("Point",))]
    old = _lib(
        "1",
        list(pub),
        types=[_point(64, [TypeField("x", "int"), TypeField("y", "int")])],
    )
    new = _lib(
        "2",
        list(pub),
        types=[
            _point(
                96,
                [TypeField("x", "int"), TypeField("y", "int"), TypeField("z", "int")],
            )
        ],
    )
    res = _compare(tmp_path, old, new)
    assert res.exit_code == 4
    doc = json.loads(_compare(tmp_path, old, new, "--format", "json").output)
    assert doc["verdict"] == "BREAKING"
    assert any(c["kind"] == "type_size_changed" for c in doc["changes"])


def test_sc_cpp_vtable_break(tmp_path: Path) -> None:
    # C++ archetype: inserting a virtual into a polymorphic base shifts every
    # later vtable slot — a silent dispatch break the export table cannot see.
    def _shape(vtable: list[str]) -> RecordType:
        return RecordType(
            name="Shape",
            kind="class",
            size_bits=64,
            fields=[TypeField("_vptr", "void*")],
            vtable=vtable,
        )

    old = _lib(
        "1",
        [_vfn("_ZN5Shape4areaEv", 0), _vfn("_ZN5Shape9perimeterEv", 1)],
        types=[_shape(["_ZN5Shape4areaEv", "_ZN5Shape9perimeterEv"])],
    )
    new = _lib(
        "2",
        [
            _vfn("_ZN5Shape4areaEv", 0),
            _vfn("_ZN5Shape4drawEv", 1),
            _vfn("_ZN5Shape9perimeterEv", 2),
        ],
        types=[
            _shape(["_ZN5Shape4areaEv", "_ZN5Shape4drawEv", "_ZN5Shape9perimeterEv"])
        ],
    )
    res = _compare(tmp_path, old, new, "--no-scope-public-headers", "--format", "json")
    assert res.exit_code == 4
    doc = json.loads(res.output)
    assert doc["verdict"] == "BREAKING"
    assert any(c["kind"] == "type_vtable_changed" for c in doc["changes"])


def test_sc_exported_var_removed(tmp_path: Path) -> None:
    # Data surface: dropping a public exported global variable breaks every
    # consumer that referenced it (var_removed → BREAKING), like a removed symbol.
    old = _lib("1", [_fn("a")], variables=[_var("g_count")])
    new = _lib("2", [_fn("a")], variables=[])
    res = _compare(tmp_path, old, new, "--no-scope-public-headers", "--format", "json")
    assert res.exit_code == 4
    doc = json.loads(res.output)
    assert doc["verdict"] == "BREAKING"
    assert any(c["kind"] == "var_removed" for c in doc["changes"])


def test_sc_dual_abi_flip(tmp_path: Path) -> None:
    # libstdc++ dual-ABI flip: the std::string family re-mangles when
    # _GLIBCXX_USE_CXX11_ABI toggles. A naive diff sees mass add/remove churn;
    # the scanner must recognise the __cxx11 marker pattern and emit one grouped
    # glibcxx_dual_abi_flip_detected (still a real break: exit 4).
    legacy = [_fn(f"_ZN3foo3barESs{i}") for i in range(6)]  # no __cxx11 marker
    cxx11 = [_fn(f"_ZN3foo3barENSt7__cxx1112basic_stringE{i}") for i in range(6)]
    res = _compare(
        tmp_path,
        _lib("1", legacy),
        _lib("2", cxx11),
        "--no-scope-public-headers",
        "--format",
        "json",
    )
    assert res.exit_code == 4
    doc = json.loads(res.output)
    assert doc["verdict"] == "BREAKING"
    assert any(c["kind"] == "glibcxx_dual_abi_flip_detected" for c in doc["changes"])


def test_sc_integer_model_flip(tmp_path: Path) -> None:
    # LP64↔ILP64 switch: an integer-named typedef (MKL_INT) flips width 32→64.
    # No symbol added/removed, yet every caller now passes/reads the wrong width
    # (integer_model_changed → BREAKING).
    old = _lib("1", [_fn("solve")], typedefs={"MKL_INT": "int"})
    new = _lib("2", [_fn("solve")], typedefs={"MKL_INT": "int64_t"})
    res = _compare(tmp_path, old, new, "--no-scope-public-headers", "--format", "json")
    assert res.exit_code == 4
    doc = json.loads(res.output)
    assert doc["verdict"] == "BREAKING"
    assert any(c["kind"] == "integer_model_changed" for c in doc["changes"])


def test_sc_toolchain_flag_drift(tmp_path: Path) -> None:
    # ABI-affecting compiler flags (recorded in DWARF toolchain metadata) drift
    # between two builds of the same sources. The scanner surfaces
    # toolchain_flag_drift as a *risk* signal — visible, but not a confirmed
    # break, so the default gate stays green (exit 0).
    def _adv(flags: set[str]) -> AdvancedDwarfMetadata:
        meta = AdvancedDwarfMetadata(has_dwarf=True)
        meta.toolchain.abi_flags = flags
        return meta

    old = _lib("1", [_fn("a")], dwarf_advanced=_adv({"-fno-exceptions"}))
    new = _lib("2", [_fn("a")], dwarf_advanced=_adv({"-fno-exceptions", "-ffast-math"}))
    res = _compare(tmp_path, old, new, "--no-scope-public-headers", "--format", "json")
    assert res.exit_code == 0
    doc = json.loads(res.output)
    assert doc["verdict"] == "COMPATIBLE"
    assert any(c["kind"] == "toolchain_flag_drift" for c in doc["changes"])


def test_sc_cxx_std_floor(tmp_path: Path) -> None:
    # The toolchain use case behind the probe matrix: identical binary surfaces,
    # but the build raises the C++ standard floor 17 → 20. A per-binary diff is
    # NO_CHANGE; passing the probe matrices folds CXX_STANDARD_FLOOR_RAISED into
    # the verdict, surfacing the per-consumer source break (API_BREAK, exit 2).
    from abicheck.probe_harness import (
        MatrixSnapshot,
        ProbeResult,
        write_matrix_snapshot,
    )

    same = [_fn("a")]
    o = _save(_lib("1", list(same)), tmp_path / "o.json")
    n = _save(_lib("2", list(same)), tmp_path / "n.json")
    assert _cli("compare", o, n).exit_code == 0  # binary surface unchanged

    def _matrix(version: str, std: int) -> MatrixSnapshot:
        return MatrixSnapshot(
            library="libfoo",
            version=version,
            spec_name="t",
            cxx_stds={"a": std},
            results=[
                ProbeResult(
                    configuration_id="a",
                    probe_id="p0",
                    snapshot=_lib(version, list(same)),
                )
            ],
        )

    om, nm = str(tmp_path / "om.json"), str(tmp_path / "nm.json")
    write_matrix_snapshot(_matrix("1", 17), om)
    write_matrix_snapshot(_matrix("2", 20), nm)
    res = _cli(
        "compare",
        o,
        n,
        "--format",
        "json",
        "--probe-matrix-old",
        om,
        "--probe-matrix-new",
        nm,
    )
    assert res.exit_code == 2
    doc = json.loads(res.stdout)
    assert doc["verdict"] == "API_BREAK"
    assert any(c["kind"] == "cxx_standard_floor_raised" for c in doc["changes"])


def test_sc_linux_elf_baseline(tmp_path: Path) -> None:
    # The validated baseline platform: an explicit ELF compare (platform="elf",
    # SONAME-versioned) where a public symbol is removed → binary break (exit 4)
    # with a major-bump recommendation, as a consumer would hit at load time.
    from abicheck.elf_metadata import ElfMetadata

    elf = ElfMetadata(soname="libfoo.so.1")
    old = _lib("1", [_fn("a"), _fn("b")], platform="elf", elf=elf)
    new = _lib("2", [_fn("a")], platform="elf", elf=ElfMetadata(soname="libfoo.so.1"))
    res = _compare(tmp_path, old, new, "--format", "json")
    assert res.exit_code == 4
    doc = json.loads(res.output)
    assert doc["verdict"] == "BREAKING"
    assert doc["release_recommendation"]["version_bump"] == "major"
