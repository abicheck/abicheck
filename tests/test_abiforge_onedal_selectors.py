"""Reconcile pinned runtime, relocation, and header/export evidence by symbol."""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from abiforge.run.onedal_covariance_parameters import CAMPAIGNS

PRODUCT = Path(__file__).resolve().parents[1] / "research/onedal-coverage/product"


def receipt(name):
    return json.loads((PRODUCT / name).read_text())


@pytest.mark.parametrize(
    ("release", "abi"),
    [("dal-2025.9.0", 3), ("dal-2025.11.0", 3), ("dal-2026.2.0", 4)],
)
def test_selector_receipts(release, abi):
    calls = set()
    inventory = set()
    for campaign, (pattern, expected_count) in CAMPAIGNS.items():
        result = receipt(f"{campaign.replace('_', '-')}-parameters.json")[release]
        control = (
            "unimplemented_exception_control_exit"
            if campaign == "logistic_regression"
            else "numerical_control_exit"
        )
        assert result[control] == 0
        reached = result["called_selectors"]
        missed = result.get("unobserved_selectors", [])
        names = set(reached + missed)
        assert len(reached) + len(missed) == len(names) == expected_count
        assert all(re.search(pattern, name) for name in names)
        assert names.isdisjoint(inventory)
        if "bound_selectors" in result:
            assert set(result["bound_selectors"]) == set(reached)
        inventory.update(names)
        calls.update(reached)
    assert (len(calls), len(inventory)) == (18, 40)

    forest = receipt("decision-forest-parameters.json")[release]
    helpers = set(forest["called_helpers"])
    assert helpers == set(forest["bound_helpers"])
    assert len(helpers) == 2 and helpers.isdisjoint(calls)
    matrix = receipt("parameter-helper-scope.json")
    providers = matrix[release]
    for frontend, provider in providers.items():
        candidates = provider["candidate_releases"]
        assert set(candidates) == set(matrix) - {release}
        for target, candidate in candidates.items():
            target_abi = 4 if target == "dal-2026.2.0" else 3
            counterpart = matrix[target][frontend.rsplit(".", 1)[0] + f".{target_abi}"]
            assert candidate == {
                "provider": counterpart["provider"],
                "missing_symbols": [],
            }
    cpu_required = set(providers[f"libonedal.so.{abi}"]["required_names"])
    assert cpu_required == calls | helpers
    gpu_refs = receipt("gpu-selector-relocations.json")[release][
        f"libonedal_dpc.so.{abi}"
    ]
    dpc_required = set(providers[f"libonedal_dpc.so.{abi}"]["required_names"])
    assert dpc_required == cpu_required | set(gpu_refs["relocation_types_by_selector"])

    relocations = receipt("selector-relocations.json")[release]
    for frontend in ("libonedal", "libonedal_dpc"):
        record = relocations[f"{frontend}.so.{abi}"]
        assert record["relocation_types_by_selector"] == {
            name: ["R_X86_64_JUMP_SLOT"] for name in calls
        }
        assert set(record["selectors_without_named_relocation"]) == inventory - calls


@pytest.mark.skipif(
    shutil.which("c++filt") is None, reason="requires an Itanium demangler"
)
@pytest.mark.parametrize(
    ("release", "abi"), [("dal-2025.9.0", 3), ("dal-2025.11.0", 3), ("dal-2026.2.0", 4)]
)
def test_selector_names_match_header_inventory(release, abi):
    record = receipt("selector-relocations.json")[release][f"libonedal.so.{abi}"]
    names = sorted(
        [
            *record["relocation_types_by_selector"],
            *record["selectors_without_named_relocation"],
        ]
    )
    output = subprocess.check_output(
        ["c++filt"], input="\n".join(names) + "\n", text=True
    )
    signatures = set(output.splitlines())
    headers = receipt("parameter-header-exports.json")[release]
    for library in ("libonedal_parameters", "libonedal_parameters_dpc"):
        provider = f"{library}.so.{abi}"
        assert signatures == {
            signature
            for header in headers.values()
            for signature in header["providers"][provider]
        }


@pytest.mark.parametrize(
    ("release", "abi"),
    [("dal-2025.9.0", 3), ("dal-2025.11.0", 3), ("dal-2026.2.0", 4)],
)
def test_gpu_selector_frontend_relocations(release, abi):
    report = receipt("gpu-selector-relocations.json")[release]
    cpu = report[f"libonedal.so.{abi}"]
    dpc = report[f"libonedal_dpc.so.{abi}"]
    exports = set(cpu["selectors_without_named_relocation"])
    references = dpc["relocation_types_by_selector"]
    unreferenced = set(dpc["selectors_without_named_relocation"])
    assert not cpu["relocation_types_by_selector"]
    assert len(exports) == 42 and len(references) == 30 and len(unreferenced) == 12
    assert set(references) | unreferenced == exports
    assert all(kinds == ["R_X86_64_JUMP_SLOT"] for kinds in references.values())
    assert all("_parameters_gpuI" in name for name in exports)
    assert all(
        "3pca" in name
        and ("partial_train_input" in name or "partial_train_result" in name)
        for name in unreferenced
    )


@pytest.mark.parametrize("device", ["cpu", "gpu"])
def test_single_export_loader_results(device):
    scope = receipt(f"{device}-selector-scope.json")
    relocation_file = (
        "selector-relocations.json"
        if device == "cpu"
        else "gpu-selector-relocations.json"
    )
    relocations = receipt(relocation_file)
    assert scope.keys() == relocations.keys()
    for release, frontends in relocations.items():
        for frontend, record in frontends.items():
            if device == "gpu" and "_dpc" not in frontend:
                continue
            cases = scope[release] if device == "gpu" else scope[release][frontend]
            required = record["relocation_types_by_selector"]
            unused = record["selectors_without_named_relocation"]
            assert cases["referenced"]["single_removal_loader_exits"] == dict.fromkeys(
                required, 127
            )
            assert cases["unreferenced"][
                "single_removal_loader_exits"
            ] == dict.fromkeys(unused, 0)
            assert cases["referenced"]["single_removal_predicted_missing"] == sorted(
                required
            )
            assert cases["unreferenced"]["single_removal_predicted_missing"] == []


def test_frozen_cpu_client_downgrade():
    reverse = receipt("cpu-parameter-downgrade.json")
    assert reverse["frozen_client_sdk"] == "dal-2025.11.0"
    assert reverse["campaigns"].keys() == CAMPAIGNS.keys()
    for campaign, results in reverse["campaigns"].items():
        forward = receipt(f"{campaign.replace('_', '-')}-parameters.json")
        assert results == {
            release: forward[release] for release in ("dal-2025.9.0", "dal-2025.11.0")
        }


def test_frozen_parameter_clients_across_soname_majors():
    matrix = receipt("parameter-load-matrix.json")
    majors = {"dal-2025.9.0": 3, "dal-2025.11.0": 3, "dal-2026.2.0": 4}
    assert matrix.keys() == majors.keys()
    for sdk, campaigns in matrix.items():
        assert campaigns.keys() == CAMPAIGNS.keys()
        for client in campaigns.values():
            direct = client["direct_dependencies"]
            needed = client["needed"]
            assert [name for name in direct if name.startswith("libonedal")] == needed
            assert "libtbb.so.12" in direct
            assert f"libonedal_parameters.so.{majors[sdk]}" in needed
            assert client["runs"].keys() == majors.keys()
            for runtime, result in client["runs"].items():
                crosses_major = majors[sdk] != majors[runtime]
                assert result == {
                    "exit": 127 if crosses_major else 0,
                    "absent_dependencies": needed if crosses_major else [],
                }


def test_versioned_import_profiles_cover_all_frozen_clients():
    matrix = receipt("parameter-load-matrix.json")
    expected = {
        f"{sdk}/{algorithm}": row["versioned_import_count"]
        for sdk, clients in matrix.items()
        for algorithm, row in clients.items()
    }
    observed = {}
    for profile in receipt("parameter-import-profiles.json"):
        for client in profile["clients"]:
            assert client not in observed
            observed[client] = len(profile["imports"])
    assert observed == expected


def test_cross_major_cpu_frontend_imports():
    matrix = receipt("parameter-load-matrix.json")
    major = "dal-2026.2.0"
    earlier = set(matrix) - {major}
    for sdk, clients in matrix.items():
        for algorithm, client in clients.items():
            missing = client["missing_frontend_imports_by_candidate"]
            assert missing.keys() == matrix.keys()
            assert missing[major] == []
            names = {symbol for _, symbol in client["direct_onedal_bindings"]}
            for release in earlier:
                old_names = {
                    symbol
                    for baseline in matrix[release].values()
                    for _, symbol in baseline["direct_onedal_bindings"]
                }
                assert set(missing[release]) == names - old_names, (sdk, algorithm)


def test_new_frontend_imports_have_direct_call_witnesses():
    report = receipt("frontend-calls.json")
    matrix = receipt("parameter-load-matrix.json")[report["sdk"]]
    assert report["clients"].keys() == matrix.keys()
    for algorithm, client in report["clients"].items():
        absent = matrix[algorithm]["missing_frontend_imports_by_candidate"][
            report["earlier"]
        ]
        assert client["baseline_exit"] == 0
        assert client["wrapped_call_exits"] == dict.fromkeys(absent, 42)
        assert client["first_call_phase"] == {
            name: "before_main"
            if "get_noise_variance_id" in name
            else "after_main_entry"
            for name in absent
        }


def test_public_header_alone_introduces_pca_getter_reference():
    reports = receipt("header-imports.json")
    old = reports["dal-2025.9.0"]
    assert old == reports["dal-2025.11.0"]
    major = reports["dal-2026.2.0"]
    assert not major["empty"]["frontend_references"]
    for header in set(old) - {"pca"}:
        assert old[header] == major[header]
    getter = "_ZN6oneapi3dal3pca6detail21get_noise_variance_idEv"
    assert set(major["pca"]["frontend_references"]) == set(
        old["pca"]["frontend_references"]
    ) | {getter}
    for release, missing in major["pca"]["missing_by_release"].items():
        assert missing == ([] if release == "dal-2026.2.0" else [getter])


def test_installed_algorithm_header_inventory():
    rows = receipt("all-algorithm-header-imports.json")
    baseline = rows["dal-2025.9.0"]
    assert baseline == rows["dal-2025.11.0"]
    major = rows["dal-2026.2.0"]
    assert len(baseline) == 26  # 25 headers plus the empty control
    assert set(major) == set(baseline) | {"hdbscan"}
    assert all(
        "frontend_references" in row
        for headers in rows.values()
        for row in headers.values()
    )
    assert {name for name in baseline if baseline[name] != major[name]} == {"pca"}
    hdbscan = major["hdbscan"]
    names = hdbscan["frontend_references"]
    assert len(names) == len(set(names)) == 6
    for release, missing in hdbscan["missing_by_release"].items():
        assert missing == ([] if release == "dal-2026.2.0" else names)
