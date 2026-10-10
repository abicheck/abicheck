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

"""ADR-062 A1.6: ``project capture-variants`` and variant pairing in the
stored release comparison.

The capture half is checked over an exhaustive small domain: two variants,
each ``required`` or not, each in one of four outcomes (captured, no input,
missing path, extractor failure) -- 64 combinations, against an oracle
written here from the plan's stated rule ("a required variant that cannot be
captured writes nothing; an optional one is simply absent"). The pairing
half is checked over packages a real capture run wrote, through the real
``compare`` CLI.
"""

from __future__ import annotations

import itertools
import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from abicheck.cli import main
from abicheck.compare.variant_pairing import pair_variant_views
from abicheck.model import AbiSnapshot, Function, Visibility
from abicheck.model.bundle_variants import parse_bundle_variants
from abicheck.model.dwarf_facts import AdvancedDwarfMetadata, ToolchainInfo
from abicheck.model.variant_pairing import (
    PAIRED,
    UNMATCHED_NEW,
    UNMATCHED_OLD,
    VariantView,
)
from abicheck.project_snapshot_store import (
    DirectoryObjectStore,
    read_manifest_summary,
    read_variant_ref,
)
from abicheck.serialization import save_snapshot
from abicheck.storage.bundle_facts_package import read_bundle_facts_package
from abicheck.workflows.bundle_variants_capture import (
    VariantCaptureError,
    VariantCaptureInput,
    capture_variants,
    captured_coordinates,
    plan_variant_capture,
)
from abicheck.workflows.variant_pairing import package_variant_views
from tests._project_manifest_reader import read_project_manifest


def _snap(
    lib: str, fns: list[str], *, compiler: str = "GCC", version: str = "13.2.0"
) -> AbiSnapshot:
    snap = AbiSnapshot(
        library=lib,
        version="1",
        functions=[
            Function(
                name=f,
                mangled=f,
                return_type="int",
                visibility=Visibility.PUBLIC,
                is_extern_c=True,
            )
            for f in fns
        ],
        from_headers=True,
    )
    snap.declarations.debug_advanced = AdvancedDwarfMetadata(
        has_dwarf=True,
        target_arch="x86_64",
        toolchain=ToolchainInfo(
            producer_string=f"{compiler} {version} -O2",
            compiler=compiler,
            version=version,
        ),
    )
    snap.platform = "elf"
    return snap


def _write_inputs(root: Path, fns: list[str], **kw: str) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    save_snapshot(_snap("liba.so.1", fns, **kw), root / "liba.so.1.json")
    save_snapshot(_snap("libb.so.1", ["b"], **kw), root / "libb.so.1.json")
    return root


_CONFIG = """\
bundle_variants:
  x86:
    target_triple: x86_64-linux-gnu
    compiler_family: {x86_compiler}
    feature_toggles: {{simd: avx2, threads: true}}
  arm:
    target_triple: aarch64-linux-gnu
    compiler_family: gcc
    required: false
"""


def _config(
    tmp_path: Path, name: str = ".abicheck.yml", x86_compiler: str = "clang"
) -> Path:
    path = tmp_path / name
    path.write_text(_CONFIG.format(x86_compiler=x86_compiler), encoding="utf-8")
    return path


def _run(*args: str) -> tuple[int, str]:
    result = CliRunner().invoke(main, list(args), catch_exceptions=False)
    return result.exit_code, result.output


def _stub_dump(path: Path, _item: VariantCaptureInput) -> AbiSnapshot:
    from abicheck.serialization import load_snapshot

    return load_snapshot(path)


# ── The exhaustive plan/capture domain ─────────────────────────────────────

_OUTCOMES = ("ok", "no_input", "missing_path", "dump_fails")


@pytest.mark.parametrize(
    ("req_a", "out_a", "req_b", "out_b"),
    list(itertools.product((True, False), _OUTCOMES, (True, False), _OUTCOMES)),
)
def test_capture_outcome_matrix(
    tmp_path: Path, req_a: bool, out_a: str, req_b: bool, out_b: str
) -> None:
    config = parse_bundle_variants(
        {
            "va": {"target_triple": "t-a", "compiler_family": "gcc", "required": req_a},
            "vb": {"target_triple": "t-b", "compiler_family": "gcc", "required": req_b},
        }
    )
    inputs = []
    failing: set[Path] = set()
    for name, outcome in (("va", out_a), ("vb", out_b)):
        if outcome == "no_input":
            continue
        path = tmp_path / "in" / name
        if outcome != "missing_path":
            _write_inputs(path, ["f"])
        if outcome == "dump_fails":
            failing.add(path / "liba.so.1.json")
        inputs.append(VariantCaptureInput(name, path))

    def dump(path: Path, item: VariantCaptureInput) -> AbiSnapshot:
        if path in failing:
            raise RuntimeError("extractor exploded")
        return _stub_dump(path, item)

    # Independent oracle.
    states = {"va": (req_a, out_a), "vb": (req_b, out_b)}
    should_fail = any(req and out != "ok" for req, out in states.values()) or all(
        out != "ok" for _req, out in states.values()
    )
    expected_variants = {n for n, (_r, out) in states.items() if out == "ok"}
    expected_skipped = {
        n for n, (req, out) in states.items() if not req and out != "ok"
    }

    out_dir = tmp_path / "out" / "pkg"
    if should_fail:
        with pytest.raises(VariantCaptureError):
            plan = plan_variant_capture(config, inputs)
            capture_variants(plan, out_dir, dump=dump)
        assert not out_dir.exists()
        # No staging directory left behind either.
        assert (
            not (tmp_path / "out").exists() or list((tmp_path / "out").iterdir()) == []
        )
        return
    plan = plan_variant_capture(config, inputs)
    result = capture_variants(plan, out_dir, dump=dump)
    assert set(read_manifest_summary(out_dir).variant_ids) == expected_variants
    assert {s.name for s in result.skipped} == expected_skipped
    assert set(result.captured) == expected_variants
    assert [p.name for p in (tmp_path / "out").iterdir()] == ["pkg"]


def test_undeclared_or_duplicate_variant_input_is_a_plan_error(tmp_path: Path) -> None:
    config = parse_bundle_variants(
        {"x": {"target_triple": "t", "compiler_family": "g"}}
    )
    path = _write_inputs(tmp_path / "x", ["f"])
    with pytest.raises(VariantCaptureError, match="not declared"):
        plan_variant_capture(
            config, [VariantCaptureInput("x", path), VariantCaptureInput("y", path)]
        )
    with pytest.raises(VariantCaptureError, match="more than once"):
        plan_variant_capture(
            config, [VariantCaptureInput("x", path), VariantCaptureInput("x", path)]
        )


def test_refuses_a_non_empty_output(tmp_path: Path) -> None:
    config = parse_bundle_variants(
        {"x": {"target_triple": "t", "compiler_family": "g"}}
    )
    plan = plan_variant_capture(
        config, [VariantCaptureInput("x", _write_inputs(tmp_path / "x", ["f"]))]
    )
    out = tmp_path / "pkg"
    out.mkdir()
    (out / "keep.txt").write_text("mine")
    with pytest.raises(VariantCaptureError, match="not an empty directory"):
        capture_variants(plan, out, dump=_stub_dump)
    assert [p.name for p in out.iterdir()] == ["keep.txt"]


# ── Declared vs captured ───────────────────────────────────────────────────


def test_declared_and_captured_are_both_kept_when_they_disagree(tmp_path: Path) -> None:
    """The config says clang; the DWARF producer says GCC 13.2.0. Both maps
    survive to the package, neither overwriting the other, and the captured
    map carries the version the config never stated."""
    cfg = _config(tmp_path)
    x86 = _write_inputs(tmp_path / "in" / "x86", ["foo"])
    code, out = _run(
        "project",
        "capture-variants",
        "--config",
        str(cfg),
        "--variant",
        f"x86={x86}",
        "--package",
        str(tmp_path / "pkg"),
        "-o",
        "json=-",
    )
    assert code == 0, out
    ref = read_variant_ref(tmp_path / "pkg", "x86")
    assert ref.declared["compiler_family"] == "clang"
    assert ref.captured["compiler_family"] == "gcc"
    assert ref.captured["compiler_version"] == "13.2.0"
    assert "compiler_version" not in ref.declared
    assert ref.declared["simd"] == "avx2" and ref.declared["threads"] == "true"
    # The optional, uncaptured variant has no VariantRef at all -- not an
    # empty placeholder -- and the run said so.
    assert read_manifest_summary(tmp_path / "pkg").variant_ids == ("x86",)
    assert "optional variant 'arm' skipped" in out


def test_each_variant_reads_back_as_its_own_bundle(tmp_path: Path) -> None:
    """Artifact ids are namespaced by variant, so two variants sharing a
    library name coexist, and each reads back through the existing
    bundle-facts reader with its real library names."""
    cfg = _config(tmp_path)
    x86 = _write_inputs(tmp_path / "in" / "x86", ["foo"])
    arm = _write_inputs(tmp_path / "in" / "arm", ["foo", "bar"])
    code, out = _run(
        "project",
        "capture-variants",
        "--config",
        str(cfg),
        "--variant",
        f"x86={x86}",
        "--variant",
        f"arm={arm}",
        "--package",
        str(tmp_path / "pkg"),
    )
    assert code == 0, out
    manifest = read_project_manifest(tmp_path / "pkg")
    store = DirectoryObjectStore(tmp_path / "pkg")
    for variant, n_fns in (("x86", 1), ("arm", 2)):
        facts = read_bundle_facts_package(manifest, store=store, variant_id=variant)
        assert sorted(facts.per_library_snapshots) == ["liba.so", "libb.so"]
        assert (
            len(facts.per_library_snapshots["liba.so"].declarations.functions) == n_fns
        )


def test_captured_coordinates_state_only_observed_facts() -> None:
    bare = AbiSnapshot(library="l", version="1")
    assert captured_coordinates([bare]) == {}
    mixed = captured_coordinates(
        [_snap("a", []), _snap("b", [], compiler="clang", version="17.0.1")]
    )
    assert mixed["compiler_family"] == "clang,gcc"  # a mixed variant is itself the fact
    assert mixed["compiler_version"] == "13.2.0,17.0.1"
    assert mixed["target_arch"] == "x86_64"


def test_cli_required_variant_missing_is_usage_error_and_writes_nothing(
    tmp_path: Path,
) -> None:
    cfg = _config(tmp_path)
    arm = _write_inputs(tmp_path / "in" / "arm", ["foo"])
    code, out = _run(
        "project",
        "capture-variants",
        "--config",
        str(cfg),
        "--variant",
        f"arm={arm}",
        "--package",
        str(tmp_path / "pkg"),
    )
    assert code == 64
    assert "required variant 'x86'" in out
    assert not (tmp_path / "pkg").exists()


def test_cli_dry_run_writes_nothing(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    x86 = _write_inputs(tmp_path / "in" / "x86", ["foo"])
    code, out = _run(
        "project",
        "capture-variants",
        "--config",
        str(cfg),
        "--variant",
        f"x86={x86}",
        "--package",
        str(tmp_path / "pkg"),
        "--dry-run",
        "-o",
        "json=-",
    )
    assert code == 0, out
    payload = json.loads(out[out.index("{") :])
    assert payload["variants"]["x86"]["libraries"] == ["liba.so", "libb.so"]
    assert not (tmp_path / "pkg").exists()


@pytest.mark.parametrize("bad", ["x86", "=path", "x86=", "", "="])
def test_cli_malformed_assignment_is_usage_error(tmp_path: Path, bad: str) -> None:
    cfg = _config(tmp_path)
    code, out = _run(
        "project",
        "capture-variants",
        "--config",
        str(cfg),
        "--variant",
        bad,
        "--package",
        str(tmp_path / "pkg"),
    )
    assert code == 64
    assert "NAME=PATH" in out


# ── Pairing ────────────────────────────────────────────────────────────────


def _views(spec: dict[str, tuple[str, str]]) -> list[VariantView]:
    return [
        VariantView(
            vid, {"target_triple": triple}, {"compiler_version": ver}, required=True
        )
        for vid, (triple, ver) in spec.items()
    ]


def test_pairing_properties_over_all_small_id_sets() -> None:
    """Over every pair of subsets of four ids, in every order: each id
    appears once, the status matches set membership, and the result does
    not depend on input order."""
    ids = ["a", "b", "c", "d"]
    subsets = [s for n in range(len(ids) + 1) for s in itertools.combinations(ids, n)]
    for old_ids, new_ids in itertools.product(subsets, subsets):
        old = _views({i: ("t-" + i, "1") for i in old_ids})
        new = _views({i: ("t-" + i + ("x" if i == "a" else ""), "2") for i in new_ids})
        pairing = pair_variant_views(old, new)
        assert pair_variant_views(list(reversed(old)), list(reversed(new))) == pairing
        by_id = {p.variant_id: p for p in pairing.pairs}
        assert set(by_id) == set(old_ids) | set(new_ids)
        for vid, pair in by_id.items():
            expected = (
                PAIRED
                if vid in old_ids and vid in new_ids
                else UNMATCHED_OLD
                if vid in old_ids
                else UNMATCHED_NEW
            )
            assert pair.status == expected
            if expected == PAIRED:
                assert pair.variant_boundary_changed is (vid == "a")
                assert set(pair.captured_changes) == {"compiler_version"}
            else:
                assert pair.declared_changes == {} and "reason" in pair.to_dict()
        assert {p.variant_id for p in pairing.unmatched_required} == set(old_ids) ^ set(
            new_ids
        )


def test_pairing_rejects_a_duplicate_id() -> None:
    with pytest.raises(ValueError, match="twice"):
        pair_variant_views(_views({"a": ("t", "1")}) * 2, [])


def _capture(
    tmp_path: Path, tag: str, cfg: Path, variants: dict[str, list[str]], **kw: str
) -> Path:
    args = [
        "project",
        "capture-variants",
        "--config",
        str(cfg),
        "--package",
        str(tmp_path / tag),
    ]
    for name, fns in variants.items():
        args += [
            "--variant",
            f"{name}={_write_inputs(tmp_path / 'in' / tag / name, fns, **kw)}",
        ]
    code, out = _run(*args)
    assert code == 0, out
    return tmp_path / tag


def test_stored_comparison_reports_pairing_from_real_captures(tmp_path: Path) -> None:
    """Two packages written by real capture runs; the second changes the
    x86 variant's declared compiler family (a variant-boundary change) and
    drops the optional arm variant. The release JSON pairs them by id,
    reports the boundary change distinctly from the compiler-version bump,
    and reports the unmatched variant without calling it removed."""
    old_pkg = _capture(
        tmp_path,
        "old",
        _config(tmp_path, "old.yml"),
        {"x86": ["foo", "bar"], "arm": ["foo"]},
    )
    new_pkg = _capture(
        tmp_path,
        "new",
        _config(tmp_path, "new.yml", x86_compiler="gcc"),
        {"x86": ["foo"]},
        version="14.1.0",
    )
    # Views read off the real packages carry the recorded `required` flag.
    assert {v.variant_id: v.required for v in package_variant_views(old_pkg)} == {
        "arm": False,
        "x86": True,
    }

    report = tmp_path / "report.json"
    code, out = _run(
        "compare",
        str(old_pkg),
        str(new_pkg),
        "--variant",
        "old=x86",
        "-o",
        f"json={report}",
    )
    assert code == 4, out  # `bar` was removed from liba in the compared variant
    doc = json.loads(report.read_text())
    pairing = doc["comparison_scope"]["variant_pairing"]
    assert pairing["compared"] == {"old": "x86", "new": "x86"}
    pairs = {p["variant_id"]: p for p in pairing["pairs"]}
    assert pairs["x86"]["status"] == "paired"
    assert pairs["x86"]["variant_boundary_changed"] is True
    assert pairs["x86"]["declared_changes"] == {
        "compiler_family": {"old": "clang", "new": "gcc"}
    }
    assert pairs["x86"]["captured_changes"]["compiler_version"] == {
        "old": "13.2.0",
        "new": "14.1.0",
    }
    assert pairs["arm"]["status"] == "unmatched_old"
    assert pairs["arm"]["required"] is False
    assert "absence is not removal" in pairs["arm"]["reason"]
    assert pairing["variant_boundary_changes"] == ["x86"]
    assert pairing["unmatched_required"] == []
    # The unmatched variant never reads as a removed library.
    assert doc["comparison_scope"]["proven_removed"] == []
    # And the block conforms to the published schema.
    from schema_validation import jsonschema_available, validate_instance

    if jsonschema_available():
        schema_path = (
            Path(__file__).resolve().parents[1]
            / "abicheck/schemas/compare_report.schema.json"
        )
        defs = json.loads(schema_path.read_text(encoding="utf-8"))["$defs"]
        validate_instance(
            doc["comparison_scope"], {"$defs": defs, "$ref": "#/$defs/comparison_scope"}
        )


def test_unmatched_required_variant_is_reported_explicitly(tmp_path: Path) -> None:
    cfg = tmp_path / "two.yml"
    cfg.write_text(
        "bundle_variants:\n"
        "  x86: {target_triple: x86_64-linux-gnu, compiler_family: gcc}\n"
        "  arm: {target_triple: aarch64-linux-gnu, compiler_family: gcc}\n"
    )
    old_pkg = _capture(tmp_path, "old", cfg, {"x86": ["f"], "arm": ["f"]})
    only = tmp_path / "one.yml"
    only.write_text(
        "bundle_variants:\n  x86: {target_triple: x86_64-linux-gnu, compiler_family: gcc}\n"
    )
    new_pkg = _capture(tmp_path, "new", only, {"x86": ["f"]})
    report = tmp_path / "r.json"
    code, out = _run(
        "compare",
        str(old_pkg),
        str(new_pkg),
        "--variant",
        "old=x86",
        "-o",
        f"json={report}",
    )
    assert code == 0, out
    pairing = json.loads(report.read_text())["comparison_scope"]["variant_pairing"]
    assert pairing["unmatched_required"] == ["arm"]


def test_live_operand_has_no_variant_pairing(tmp_path: Path) -> None:
    old_pkg = _capture(tmp_path, "old", _config(tmp_path), {"x86": ["foo"]})
    live = _write_inputs(tmp_path / "live", ["foo"])
    report = tmp_path / "r.json"
    code, out = _run("compare", str(old_pkg), str(live), "-o", f"json={report}")
    assert code == 0, out
    assert "variant_pairing" not in json.loads(report.read_text())["comparison_scope"]


@pytest.mark.integration
def test_real_elf_capture_records_the_dwarf_producer(tmp_path: Path) -> None:
    """A real gcc-built, -g shared object: `captured` comes from the binary's
    own DW_AT_producer, not from the config."""
    import shutil
    import subprocess
    import sys

    # The assertions are about an ELF's DW_AT_producer: macOS's `gcc` is
    # Apple clang emitting Mach-O (plus a `.dSYM` bundle beside it) and
    # Windows' is MinGW emitting PE, so neither can produce the fixture.
    if not sys.platform.startswith("linux"):
        pytest.skip("needs a gcc that emits ELF (Linux)")
    gcc = shutil.which("gcc")
    if gcc is None:
        pytest.skip("gcc not available")
    src = tmp_path / "a.c"
    src.write_text("int foo(void) { return 1; }\n")
    lib_dir = tmp_path / "in"
    lib_dir.mkdir()
    subprocess.run(
        [gcc, "-g", "-shared", "-fPIC", "-o", str(lib_dir / "liba.so.1"), str(src)],
        check=True,
    )
    cfg = tmp_path / "c.yml"
    cfg.write_text(
        "bundle_variants:\n  x86: {target_triple: x86_64-linux-gnu, compiler_family: clang}\n"
    )
    code, out = _run(
        "project",
        "capture-variants",
        "--config",
        str(cfg),
        "--variant",
        f"x86={lib_dir}",
        "--package",
        str(tmp_path / "pkg"),
    )
    assert code == 0, out
    ref = read_variant_ref(tmp_path / "pkg", "x86")
    assert ref.declared["compiler_family"] == "clang"
    assert ref.captured["compiler_family"] == "gcc"
    assert ref.captured["binary_format"] == "elf"
    assert ref.captured.get("compiler_version")


def test_unrelated_baseline_variant_does_not_change_the_selected_comparison(
    tmp_path: Path,
) -> None:
    """Cardinality invariance: an extra OLD variant changes only the pairing
    block, never the selected variant's per-library outcome."""
    new_pkg = _capture(tmp_path, "new", _config(tmp_path), {"x86": ["foo"]})
    results = []
    for tag, variants in (
        ("one", {"x86": ["foo", "bar"]}),
        ("two", {"x86": ["foo", "bar"], "arm": ["zzz"]}),
    ):
        old_pkg = _capture(tmp_path, tag, _config(tmp_path), variants)
        report = tmp_path / f"{tag}.json"
        code, out = _run(
            "compare",
            str(old_pkg),
            str(new_pkg),
            "--variant",
            "old=x86",
            "-o",
            f"json={report}",
        )
        doc = json.loads(report.read_text())
        results.append(
            (code, [(lib["library"], lib["verdict"]) for lib in doc["libraries"]])
        )
    assert results[0] == results[1]
    assert results[0][0] == 4


# ── Generation stamping: only facts this build extracted get its generations ─


@pytest.mark.parametrize("fresh", [True, False])
def test_package_generations_name_the_build_that_extracted_the_facts(
    tmp_path: Path, fresh: bool
) -> None:
    from abicheck.storage import versioning

    inputs = _write_inputs(tmp_path / "x", ["f"])
    if fresh:
        # Binaries this build dumps (the stub reads the fixture's content).
        for p in list(inputs.iterdir()):
            p.rename(p.with_suffix(""))
    config = parse_bundle_variants(
        {"x": {"target_triple": "t", "compiler_family": "g"}}
    )
    plan = plan_variant_capture(config, [VariantCaptureInput("x", inputs)])
    out = tmp_path / "pkg"

    def _dump(path: Path, _item: VariantCaptureInput) -> AbiSnapshot:
        from abicheck.serialization import snapshot_from_dict

        return snapshot_from_dict(json.loads(path.read_text()))

    capture_variants(plan, out, dump=_dump)
    stored = json.loads((out / "manifest.json").read_text())["versions"]
    if fresh:
        assert stored["extractor_generation"] == versioning.EXTRACTOR_GENERATION
        assert stored["resolver_generation"] == versioning.RESOLVER_GENERATION
    else:
        assert stored.get("extractor_generation", 0) == 0
        assert stored.get("resolver_generation", 0) == 0
