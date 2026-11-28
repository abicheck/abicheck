# SPDX-License-Identifier: Apache-2.0
"""Check the oneDAL witness joins, with an opt-in real SDK replay."""

from __future__ import annotations

import csv
import gzip
import json
import os
import re
import shutil
import subprocess
import sys
from itertools import permutations
from pathlib import Path

import pytest

from abiforge.run import onedal_arguments, onedal_status_return, onedal_table_ownership


def test_product_receipts_cover_provider_matched_signatures():
    product = onedal_table_ownership.PRODUCT
    with gzip.open(product / "ledger.csv.gz", "rt") as handle:
        ledger = [r for r in csv.DictReader(handle) if r["providers"]]
    arguments = {r["symbol"] for r in ledger if r["kind"] == "by_value_arg0"}
    evidence = json.loads((product / "argument-abi.json").read_text())
    assert evidence["checker"]["mutant"]["arguments"] == {
        symbol: ["p0:nontrivial", "p0:trivial"] for symbol in arguments
    }
    assert evidence["client_exits"] == {
        symbol: {"old": 0, "control": 0, "mutant": 41} for symbol in arguments
    }
    status = json.loads((product / "status-return-abi.json").read_text())
    assert status["client_exits"] == {
        name: {"release": 0, "old": 0, "control": 0, "mutant": 42}
        for name in ("compute", "table", "virtual")
    }
    assert status["checker"]["control"]["verdict"] == "NO_CHANGE"
    assert status["checker"]["mutant"]["status_return"] == [
        ["memory (sret)", "regs(INTEGER)"]
    ]
    tables = {
        r["symbol"]
        for r in ledger
        if r["kind"] == "by_value_return"
        and r["detail"] == "data_management::NumericTablePtr"
    }
    with (product / "table-ownership.csv").open() as handle:
        runs = list(csv.DictReader(handle))
    assert {(r["symbol"], r["package"]) for r in runs} == {
        (s, v) for s in tables for v in ("dal-2025.9.0", "dal-2025.11.0")
    }
    assert all(
        {k: int(r[k]) for k in onedal_table_ownership.MODES}
        == onedal_table_ownership.MODES
        for r in runs
    )


@pytest.mark.integration
@pytest.mark.skipif(
    not os.getenv("ABIFORGE_ONEDAL_OLD") or not os.getenv("ABIFORGE_ONEDAL_NEW"),
    reason="requires the pinned oneDAL CPU development packages",
)
@pytest.mark.parametrize("campaign", ["status", "arguments", "tables"])
def test_installed_cpu_sdk(tmp_path, campaign):
    old = Path(os.environ["ABIFORGE_ONEDAL_OLD"]).resolve()
    new = Path(os.environ["ABIFORGE_ONEDAL_NEW"]).resolve()
    if campaign == "status":
        assert onedal_status_return.run(old, tmp_path / campaign)
    elif campaign == "arguments":
        assert onedal_arguments.run(old, tmp_path / campaign, check=True)
    else:
        assert onedal_table_ownership.run(old, new, tmp_path / campaign)


def test_release_campaigns_reject_ambiguous_inputs(tmp_path):
    from abiforge.run import onedal_loadability

    old = tmp_path / "old/install"
    new = tmp_path / "new/install"
    with pytest.raises(ValueError, match="distinct package names"):
        onedal_table_ownership.run(old, new, tmp_path / "ownership")
    (tmp_path / "stale").touch()
    with pytest.raises(ValueError, match="empty output"):
        onedal_loadability.run(old, tmp_path)


def test_export_identity_includes_compatibility_versions(monkeypatch):
    from abiforge.run import onedal_exports

    monkeypatch.setattr(
        onedal_exports.subprocess,
        "check_output",
        lambda *a, **kw: "foo@VER_1 T 0\nbar@@VER_2 T 1\nbaz T 2\n",
    )
    assert onedal_exports.symbols("provider.so") == {"foo", "bar", "baz"}


@pytest.mark.integration
@pytest.mark.skipif(
    not all(os.getenv(f"ABIFORGE_ONEDAL_{key}") for key in ("OLD", "NEW", "MAJOR")),
    reason="requires three pinned oneDAL Linux CPU packages",
)
def test_parameter_provider_consumer_scope(tmp_path):
    old, minor, major = (
        Path(os.environ[f"ABIFORGE_ONEDAL_{key}"]).resolve()
        for key in ("OLD", "NEW", "MAJOR")
    )
    source = (
        Path(__file__).resolve().parents[1]
        / "abiforge/fixtures/onedal/system_parameters.cpp"
    )
    client = tmp_path / "system_parameters"
    old_lib = old / "lib"
    provider = "libonedal_parameters.so.3"
    subprocess.run(
        [
            "g++",
            "-std=c++17",
            f"-I{old}/include",
            f"-I{old}/include/dal",
            source,
            f"-L{old_lib}",
            f"-Wl,--no-as-needed,-rpath-link,{old_lib}",
            f"-l:{provider}",
            "-l:libonedal.so.3",
            "-l:libonedal_core.so.3",
            "-l:libonedal_thread.so.3",
            "-l:libtbb.so.12",
            "-o",
            client,
        ],
        check=True,
    )
    for prefix, candidate, expected in (
        (minor, provider, "COMPATIBLE"),
        (major, "libonedal_parameters.so.4", "COMPATIBLE_WITH_RISK"),
    ):
        env = {**os.environ, "LD_LIBRARY_PATH": str(prefix / "lib"), "LD_BIND_NOW": "1"}
        env.pop("LD_PRELOAD", None)
        env["LC_ALL"] = "C"
        loaded = subprocess.run([client], env=env, capture_output=True, text=True)
        if prefix == major:
            assert loaded.returncode == 127
            assert f"{provider}: cannot open shared object file" in loaded.stderr
        else:
            assert loaded.returncode == 0, loaded.stderr
        report = tmp_path / f"{prefix.name}.json"
        frontend = old_lib / "libonedal.so.3"
        compared = subprocess.run(
            [
                sys.executable,
                "-m",
                "abicheck",
                "compare",
                old_lib / provider,
                prefix / "lib" / candidate,
                "--used-by",
                client,
                "--used-by",
                frontend,
                "-o",
                f"json={report}",
            ]
        )
        assert compared.returncode == (4 if prefix == major else 1)
        rows = json.loads(report.read_text())["used_by"]
        assert {
            row["app"]: (row["required_symbol_count"], row["verdict"]) for row in rows
        } == {str(client): (4, expected), str(frontend): (20, "COMPATIBLE")}
        assert all(
            not row["missing_symbols"] and not row["missing_versions"] for row in rows
        )


@pytest.mark.integration
@pytest.mark.skipif(
    not os.getenv("ABIFORGE_ONEDAL_MAJOR"), reason="requires the 2026.2 CPU SDK"
)
@pytest.mark.parametrize(
    "client",
    [
        "hdbscan",
        "hdbscan_result",
        "hdbscan_descriptor",
        "hdbscan_input",
        "hdbscan_input_exported",
        "hdbscan_result_exported",
        "hdbscan_rtti",
    ],
)
def test_hdbscan_cpu_clusters(tmp_path, client):
    from abiforge.run.onedal_covariance_parameters import build_client
    from abiforge.run.onedal_export_surface import symbol_names

    sdk = Path(os.environ["ABIFORGE_ONEDAL_MAJOR"]).resolve()
    fixture = (
        Path(__file__).resolve().parents[1] / f"abiforge/fixtures/onedal/{client}.cpp"
    )
    # PIC keeps the RTTI and vtables in the provider instead of ELF copy relocations.
    flags = ("-fPIC",) if client == "hdbscan_rtti" else ()
    exe = build_client(sdk, tmp_path, client, 4, fixture=fixture, link_inputs=flags)
    if client == "hdbscan_rtti":
        imports = symbol_names(exe, imports=True)
        for kind in ("compute_input", "compute_result"):
            assert any(
                "7hdbscan" in name and kind in name and "EEC2E" in name
                for name in imports
            )
        for prefix in ("_ZTI", "_ZTV"):
            assert (
                len([s for s in imports if s.startswith(prefix) and "7hdbscan" in s])
                == 2
            )
    if client.endswith("_exported"):
        setters = {
            name
            for name in symbol_names(exe, imports=True)
            if "7hdbscan" in name and "set_" in name
        }
        assert len(setters) == (1 if client == "hdbscan_input_exported" else 8)
        assert all("_impl" not in name for name in setters)
    run = subprocess.run(
        [exe],
        env={"LD_LIBRARY_PATH": str(sdk / "lib"), "LD_BIND_NOW": "1"},
        capture_output=True,
        text=True,
    )
    assert run.returncode == 0, run.stderr


@pytest.mark.integration
@pytest.mark.parametrize(
    ("source", "target"),
    tuple(permutations(("OLD", "NEW", "MAJOR"), 2)),
)
def test_frozen_cpu_client_provider_scope(tmp_path, source, target):
    from abiforge.run.onedal_covariance_parameters import CAMPAIGNS, build_client
    from abiforge.run.onedal_export_surface import symbol_names

    if not all(os.getenv(f"ABIFORGE_ONEDAL_{key}") for key in (source, target)):
        pytest.skip("requires both pinned oneDAL CPU development packages")
    baseline = Path(os.environ[f"ABIFORGE_ONEDAL_{source}"]).resolve()
    candidate = Path(os.environ[f"ABIFORGE_ONEDAL_{target}"]).resolve()
    source_major = 4 if source == "MAJOR" else 3
    target_major = 4 if target == "MAJOR" else 3
    crosses_major = source_major != target_major
    original = baseline / f"lib/libonedal.so.{source_major}"
    replacement = candidate / f"lib/libonedal.so.{target_major}"
    clients = {
        algorithm: build_client(baseline, tmp_path, algorithm, source_major)
        for algorithm in CAMPAIGNS
    }
    if source == "MAJOR":
        fixtures = Path(__file__).resolve().parents[1] / "abiforge/fixtures/onedal"
        for name in ("hdbscan", "hdbscan_result"):
            clients[name] = build_client(
                baseline, tmp_path, name, source_major, fixture=fixtures / f"{name}.cpp"
            )
        header = tmp_path / "hdbscan_header.cpp"
        header.write_text("#include <oneapi/dal/algo/hdbscan.hpp>\nint main() {}\n")
        clients["hdbscan_header"] = build_client(
            baseline, tmp_path, "hdbscan_header", source_major, fixture=header
        )
        clients["hdbscan_header_as_needed"] = build_client(
            baseline,
            tmp_path,
            "hdbscan_header_as_needed",
            source_major,
            fixture=header,
            as_needed=True,
        )
        tags = subprocess.check_output(
            ["readelf", "-d", clients["hdbscan_header_as_needed"]], text=True
        )
        needed = re.findall(r"\(NEEDED\).*?\[([^\]]+)\]", tags)
        assert [
            name for name in needed if name.startswith(("libonedal", "libtbb"))
        ] == [
            "libonedal.so.4",
            "libonedal_parameters.so.4",
            "libonedal_core.so.4",
            "libonedal_thread.so.4",
        ]
    expected = {}
    original_exports = symbol_names(original)
    replacement_exports = symbol_names(replacement)
    for algorithm, client in clients.items():
        for runtime in (baseline, candidate):
            loaded = subprocess.run(
                [client],
                env={"LD_LIBRARY_PATH": str(runtime / "lib"), "LD_BIND_NOW": "1"},
                capture_output=True,
                text=True,
            )
            if runtime == candidate and crosses_major:
                assert loaded.returncode == 127
                library = (
                    "libonedal"
                    if algorithm == "hdbscan_header_as_needed"
                    else "libonedal_parameters"
                )
                assert f"{library}.so.{source_major}: cannot open" in loaded.stderr
            else:
                assert loaded.returncode == 0, loaded.stderr
        required = symbol_names(client, imports=True) & original_exports
        missing = required - replacement_exports
        expected[algorithm] = (required, missing)

    if source == "MAJOR":
        required, missing = expected["hdbscan"]
        assert len(required) == 42
        assert len(missing) == 29
        assert missing == {name for name in required if "hdbscan" in name}
        result_required, result_missing = expected["hdbscan_result"]
        assert len(result_required) == 37
        assert result_missing == {name for name in result_required if "hdbscan" in name}
        assert len(result_missing) == 23
        assert len(result_missing - missing) == 9
        header_required, header_missing = expected["hdbscan_header"]
        option_getters = {
            name
            for name in missing
            if "7hdbscan6detail" in name and name.endswith("_idEv")
        }
        assert len(option_getters) == 6
        assert header_required == header_missing == option_getters
        assert expected["hdbscan_header_as_needed"] == expected["hdbscan_header"]

    required, missing = expected["covariance"]
    prefix = "_ZNK6oneapi3dal10covariance2v114compute_resultINS1_4task2v17computeEE"
    assert {
        prefix + suffix
        for suffix in ("14get_cov_matrixEv", "14get_cor_matrixEv", "9get_meansEv")
    } <= required
    covariance_destructor = (
        "_ZN6oneapi3dal10covariance2v113compute_inputINS1_4task2v17computeEED1Ev"
    )
    assert missing == ({covariance_destructor} if source == "MAJOR" else set())
    required, missing = expected["linear_regression"]
    assert len(required) == (51 if source == "MAJOR" else 49)
    regression_destructors = {
        f"_ZN6oneapi3dal17linear_regression2v1{input_type}INS1_4task2v110regressionEED1Ev"
        for input_type in ("11train_input", "19partial_train_input")
    }
    assert missing == (regression_destructors if source == "MAJOR" else set())

    batch_additions = {
        "_ZN6oneapi3dal3pca6detail21get_noise_variance_idEv",
        "_ZN6oneapi3dal3pca2v111train_inputINS1_4task2v113dim_reductionEED1Ev",
    }
    partial_destructor = (
        "_ZN6oneapi3dal3pca2v119partial_train_inputINS1_4task2v113dim_reductionEED1Ev"
    )
    for method, minor_count, additions in (
        ("pca_cov", 44, batch_additions | {partial_destructor}),
        ("pca_svd", 35, batch_additions),
        ("pca_precomputed", 35, batch_additions),
    ):
        required, missing = expected[method]
        expected_missing = additions if source == "MAJOR" else set()
        assert len(required) == minor_count + len(expected_missing)
        assert missing == expected_missing

    for algorithm in ("decision_forest", "logistic_regression"):
        required, missing = expected[algorithm]
        assert any(algorithm in symbol for symbol in required)
        assert not missing

    report = tmp_path / "consumers.json"
    compared = subprocess.run(
        [
            sys.executable,
            "-m",
            "abicheck",
            "compare",
            original,
            replacement,
            *(arg for client in clients.values() for arg in ("--used-by", client)),
            "-o",
            f"json={report}",
        ]
    )
    assert compared.returncode == (4 if crosses_major else 1)
    data = json.loads(report.read_text())
    assert data["verdict"] == ("BREAKING" if crosses_major else "UNCONFIRMED")
    assert len(data["used_by"]) == len(clients)
    consumers = {row["app"]: row for row in data["used_by"]}
    for algorithm, client in clients.items():
        consumer = consumers[str(client)]
        required, missing = expected[algorithm]
        assert consumer["required_symbol_count"] == len(required)
        expected_consumer = "COMPATIBLE_WITH_RISK" if crosses_major else "COMPATIBLE"
        assert consumer["verdict"] == ("BREAKING" if missing else expected_consumer)
        assert set(consumer["missing_symbols"]) == missing
        assert consumer["missing_versions"] == []


@pytest.mark.integration
@pytest.mark.parametrize(("release", "major"), [("OLD", 3), ("NEW", 3), ("MAJOR", 4)])
def test_pca_header_noise_variance_import(tmp_path, release, major):
    from abiforge.run.onedal_covariance_parameters import build_client
    from abiforge.run.onedal_export_surface import symbol_names

    prefix = os.getenv(f"ABIFORGE_ONEDAL_{release}")
    if not prefix:
        pytest.skip("requires the pinned oneDAL CPU development package")
    source = tmp_path / "pca_header.cpp"
    source.write_text("#include <oneapi/dal/algo/pca.hpp>\nint main() {}\n")
    exe = build_client(
        Path(prefix).resolve(), tmp_path, "header", major, fixture=source
    )
    getter = "_ZN6oneapi3dal3pca6detail21get_noise_variance_idEv"
    assert (getter in symbol_names(exe, imports=True)) == (major == 4)


@pytest.mark.integration
@pytest.mark.parametrize(("release", "major"), [("OLD", 3), ("NEW", 3), ("MAJOR", 4)])
@pytest.mark.parametrize("linker", ["bfd", "lld"])
def test_as_needed_parameter_link_order(tmp_path, release, major, linker):
    from abiforge.run.onedal_export_surface import symbol_names

    prefix = os.getenv(f"ABIFORGE_ONEDAL_{release}")
    if not prefix:
        pytest.skip("requires the pinned oneDAL CPU development package")
    sdk = Path(prefix).resolve()
    lib = sdk / "lib"
    if not shutil.which(f"ld.{linker}"):
        pytest.skip(f"requires ld.{linker}")
    obj = tmp_path / "pca.o"
    subprocess.run(
        [
            "g++",
            "-std=c++17",
            f"-I{sdk}/include",
            f"-I{sdk}/include/dal",
            "-x",
            "c++",
            "-c",
            "-",
            "-o",
            obj,
        ],
        input="#include <oneapi/dal/algo/pca.hpp>\nint main() {}\n",
        text=True,
        check=True,
    )
    frontend = lib / f"libonedal.so.{major}"
    parameters = lib / f"libonedal_parameters.so.{major}"
    expected = symbol_names(frontend, imports=True) & symbol_names(parameters)
    assert len(expected) == 20
    exe = tmp_path / "client"
    for libraries, should_link in (
        ([parameters, frontend], linker == "lld"),
        ([frontend, parameters], True),
        (["-Wl,--start-group", parameters, frontend, "-Wl,--end-group"], True),
        (["-Wl,--no-as-needed", parameters, frontend], True),
    ):
        linked = subprocess.run(
            [
                "g++",
                f"-fuse-ld={linker}",
                obj,
                "-Wl,--as-needed,--no-demangle",
                f"-Wl,-rpath-link,{lib}",
                *libraries,
                lib / f"libonedal_core.so.{major}",
                lib / f"libonedal_thread.so.{major}",
                lib / "libtbb.so.12",
                "-o",
                exe,
            ],
            env={**os.environ, "LC_ALL": "C"},
            capture_output=True,
            text=True,
        )
        if not should_link:
            assert linked.returncode != 0
            missing = re.findall(r"undefined reference to `([^']+)'", linked.stderr)
            assert set(missing) == expected
        else:
            assert linked.returncode == 0, linked.stderr
            loaded = subprocess.run(
                [exe],
                env={"LD_LIBRARY_PATH": str(lib), "LD_BIND_NOW": "1"},
                capture_output=True,
                text=True,
            )
            if linker == "lld" and "-Wl,--no-as-needed" not in libraries:
                tags = subprocess.check_output(["readelf", "-d", exe], text=True)
                needed = re.findall(r"\(NEEDED\).*?\[([^\]]+)\]", tags)
                assert [name for name in needed if name.startswith("libonedal")] == [
                    frontend.name
                ]
                assert loaded.returncode == 127
                symbol = "_ZNK4daal15data_management10interface115CSRNumericTable19getSerializationTagEv"
                assert f"{frontend}: undefined symbol: {symbol}" in loaded.stderr
                if libraries == [parameters, frontend]:
                    core = lib / f"libonedal_core.so.{major}"
                    thread = lib / f"libonedal_thread.so.{major}"
                    tbb_spawn = "_ZN3tbb6detail2r15spawnERNS0_2d14taskERNS2_18task_group_contextE"
                    propose = "_ZN6oneapi3dal15decision_forest10parameters18propose_block_sizeERKNS0_7backend11context_cpuE"
                    assert symbol in symbol_names(core)
                    tbb = lib / "libtbb.so.12"
                    tbb_exports = symbol_names(tbb)
                    mutex = "_daal_unlock_mutex"
                    assert tbb_spawn in tbb_exports
                    assert mutex not in tbb_exports
                    assert mutex in symbol_names(thread)
                    assert mutex in symbol_names(frontend, imports=True)
                    assert propose in expected
                    for providers, unresolved in (
                        ([core], tbb_spawn),
                        ([core, tbb], propose),
                        ([core, parameters, tbb], mutex),
                        ([core, parameters, tbb, thread], None),
                        ([core, thread], propose),
                        ([core, thread, parameters], None),
                    ):
                        replay = subprocess.run(
                            [exe],
                            env={
                                "LD_LIBRARY_PATH": str(lib),
                                "LD_BIND_NOW": "1",
                                "LD_PRELOAD": ":".join(map(str, providers)),
                            },
                            capture_output=True,
                            text=True,
                        )
                        if unresolved:
                            assert replay.returncode == 127
                            assert (
                                f"{frontend}: undefined symbol: {unresolved}"
                                in replay.stderr
                            )
                        else:
                            assert replay.returncode == 0, replay.stderr
            else:
                assert loaded.returncode == 0, loaded.stderr


@pytest.mark.integration
def test_thread_provider_consumer_requirements(tmp_path):
    from abiforge.run.onedal_export_surface import symbol_names

    releases = {}
    requirements = {}
    for release, major in (("OLD", 3), ("NEW", 3), ("MAJOR", 4)):
        prefix = os.getenv(f"ABIFORGE_ONEDAL_{release}")
        if not prefix:
            pytest.skip("requires three pinned oneDAL Linux packages")
        lib = Path(prefix).resolve() / "lib"
        provider = lib / f"libonedal_thread.so.{major}"
        exports = symbol_names(provider)
        releases[release] = provider, exports
        consumers = {}
        for suffix in ("", "_dpc", "_core"):
            client = lib / f"libonedal{suffix}.so.{major}"
            consumers[client] = symbol_names(client, imports=True) & exports
        requirements[release] = consumers

    old, minor, major = (
        list(requirements[key].values()) for key in ("OLD", "NEW", "MAJOR")
    )
    assert old == minor
    assert old[0] == old[1] == major[0] == major[1]
    assert len(old[0]) == 23 and "_daal_unlock_mutex" in old[0]
    assert len(old[2]) == 35
    assert major[2] == old[2] - {"_daal_threader_for_blocked"}
    assert len(major[2]) == 34
    for source, target in permutations(releases, 2):
        original, _ = releases[source]
        replacement, exports = releases[target]
        clients = requirements[source]
        report = tmp_path / f"{source}-{target}.json"
        compared = subprocess.run(
            [
                sys.executable,
                "-m",
                "abicheck",
                "compare",
                original,
                replacement,
                *(arg for client in clients for arg in ("--used-by", client)),
                "-o",
                f"json={report}",
            ]
        )
        crosses_major = "MAJOR" in (source, target)
        data = json.loads(report.read_text())
        assert compared.returncode == (4 if crosses_major else 0)
        assert data["verdict"] == ("BREAKING" if crosses_major else "COMPATIBLE")
        rows = {Path(row["app"]): row for row in data["used_by"]}
        assert rows.keys() == clients.keys()
        for client, required in clients.items():
            assert rows[client]["verdict"] == "COMPATIBLE"
            assert required <= exports
            assert rows[client]["required_symbol_count"] == len(required)
            assert (
                rows[client]["missing_symbols"]
                == rows[client]["missing_versions"]
                == []
            )


@pytest.mark.integration
def test_core_provider_consumer_requirements(tmp_path):
    from abiforge.checkers.abicheck import (
        consumer_missing_symbol_probe,
        consumer_required_names,
    )
    from abiforge.run.onedal_export_surface import symbol_names

    providers = {}
    for release, major in (("OLD", 3), ("NEW", 3), ("MAJOR", 4)):
        prefix = os.getenv(f"ABIFORGE_ONEDAL_{release}")
        if not prefix:
            pytest.skip("requires three pinned oneDAL Linux packages")
        provider = Path(prefix).resolve() / "lib" / f"libonedal_core.so.{major}"
        providers[release] = provider, symbol_names(provider)

    missing_counts = {
        ("OLD", "NEW"): 0,
        ("NEW", "OLD"): 1,
        ("OLD", "MAJOR"): 499,
        ("NEW", "MAJOR"): 499,
        ("MAJOR", "OLD"): 406,
        ("MAJOR", "NEW"): 405,
    }
    dpc_only = {}
    for source, (baseline, exports) in providers.items():
        cpu_count = {"OLD": 688, "NEW": 689, "MAJOR": 595}[source]
        cpu = baseline.with_name(baseline.name.replace("onedal_core", "onedal"))
        cpu_imports = symbol_names(cpu, imports=True) & exports
        for library, count in (
            ("onedal", cpu_count),
            ("onedal_dpc", cpu_count + 10),
            ("onedal_thread", 0),
        ):
            client = baseline.with_name(baseline.name.replace("onedal_core", library))
            required = symbol_names(client, imports=True) & exports
            assert len(required) == count
            assert consumer_required_names(client, baseline) == required
            if library == "onedal_dpc":
                assert cpu_imports <= required
                dpc_only[source] = required - cpu_imports
            probe = consumer_missing_symbol_probe(client, baseline)
            for target, (candidate, candidate_exports) in providers.items():
                if target == source:
                    continue
                missing = required - candidate_exports
                assert len(missing) == (missing_counts[source, target] if count else 0)
                assert set(probe(candidate)) == missing
                if missing and (source, target) == ("NEW", "OLD"):
                    assert missing == {
                        "_ZN4daal8services10interface111Environment9getStatusEv"
                    }

    names = dpc_only["OLD"]
    assert names == dpc_only["NEW"] == dpc_only["MAJOR"]
    assert len(names) == 10
    assert "_ZTIN4daal10algorithms6dtrees8internal4TreeE" in names
    # Link strong address references to both function and RTTI symbols.
    obj = tmp_path / "tree-references.o"
    assembly = ".data\n" + "".join(f".quad {name}\n" for name in sorted(names))
    subprocess.run(
        ["as", "--64", "-o", obj, "-"], input=assembly, text=True, check=True
    )
    imports = subprocess.check_output(["nm", "-u", "-P", obj], text=True)
    assert {line.split()[0] for line in imports.splitlines()} == names
    image = tmp_path / "tree.so"
    reader = tmp_path / "tree-rtti"
    fixture = (
        Path(__file__).resolve().parents[1] / "abiforge/fixtures/onedal/tree_rtti.cpp"
    )
    subprocess.run(["g++", "-std=c++17", fixture, "-ldl", "-o", reader], check=True)
    assert subprocess.run([reader], env={}).returncode == 1
    for provider, _ in providers.values():
        subprocess.run(
            ["ld", "-shared", "-z", "defs", "-o", image, obj, provider], check=True
        )
        for runtime, _ in providers.values():
            thread = runtime.with_name(
                runtime.name.replace("onedal_core", "onedal_thread")
            )
            loaded = subprocess.run(
                [reader],
                env={
                    "LD_LIBRARY_PATH": str(runtime.parent),
                    "LD_PRELOAD": f"{thread}:{image}",
                    "LD_BIND_NOW": "1",
                    "LD_DEBUG": "bindings",
                    "LC_ALL": "C",
                },
                capture_output=True,
                text=True,
            )
            if provider.name != runtime.name:
                assert loaded.returncode == 127
                assert (
                    f"{provider.name}: cannot open shared object file" in loaded.stderr
                )
                continue
            assert loaded.returncode == 0, loaded.stderr
            bindings = re.findall(
                rf"binding file {re.escape(str(image))} \[0\] to {re.escape(str(runtime))} "
                r"\[0\]: normal symbol `([^']+)'",
                loaded.stderr,
            )
            assert set(bindings) == names


@pytest.mark.integration
@pytest.mark.parametrize(
    ("source", "target"), tuple(permutations(("OLD", "NEW", "MAJOR"), 2))
)
def test_core_provider_cli(tmp_path, source, target):
    from abiforge.run.onedal_export_surface import symbol_names

    def provider(key):
        prefix = os.getenv(f"ABIFORGE_ONEDAL_{key}")
        if not prefix:
            pytest.skip(f"requires the pinned {key} oneDAL Linux package")
        major = 4 if key == "MAJOR" else 3
        return Path(prefix).resolve() / "lib" / f"libonedal_core.so.{major}"

    baseline, candidate = provider(source), provider(target)
    old_exports, new_exports = symbol_names(baseline), symbol_names(candidate)
    expected = {}
    for stem in ("onedal", "onedal_dpc", "onedal_thread"):
        client = baseline.with_name(baseline.name.replace("onedal_core", stem))
        required = symbol_names(client, imports=True) & old_exports
        missing = required - new_exports
        verdict = (
            "NO_CHANGE" if not required else ("BREAKING" if missing else "COMPATIBLE")
        )
        expected[client] = (len(required), missing, verdict)

    report = tmp_path / "core.json"
    compared = subprocess.run(
        [
            sys.executable,
            "-m",
            "abicheck",
            "compare",
            baseline,
            candidate,
            *(arg for client in expected for arg in ("--used-by", client)),
            "-o",
            f"json={report}",
        ],
        capture_output=True,
        text=True,
    )
    upgrade = (source, target) == ("OLD", "NEW")
    assert compared.returncode == (1 if upgrade else 4), compared.stderr
    assert "public-header scoping" in compared.stderr
    assert "Compatibility is UNCONFIRMED" not in compared.stderr
    data = json.loads(report.read_text())
    assert data["verdict"] == ("UNCONFIRMED" if upgrade else "BREAKING")
    if "MAJOR" not in (source, target):
        status_symbol = "_ZN4daal8services10interface111Environment9getStatusEv"
        (finding,) = [row for row in data["changes"] if row["symbol"] == status_symbol]
        assert finding["kind"] == ("func_added" if upgrade else "func_removed_elf_only")
        assert finding["compatibility_decision"] == (
            "COMPATIBLE" if upgrade else "BREAKING"
        )
        if not upgrade:
            assert (
                finding["contract_reason_code"]
                == "explicit_consumer_or_required_symbol_evidence"
            )
    assert {
        Path(row["app"]): (
            row["required_symbol_count"],
            set(row["missing_symbols"]),
            row["verdict"],
        )
        for row in data["used_by"]
    } == expected


@pytest.mark.integration
@pytest.mark.parametrize(("release", "major"), [("OLD", 3), ("NEW", 3), ("MAJOR", 4)])
def test_core_tree_rtti_elf_layout(release, major):
    from abicheck.diff_elf_layout import _inheritance_shape
    from abicheck.elf_metadata import parse_elf_metadata

    prefix = os.getenv(f"ABIFORGE_ONEDAL_{release}")
    if not prefix:
        pytest.skip("requires the pinned oneDAL Linux package")
    core = Path(prefix) / "lib" / f"libonedal_core.so.{major}"
    tree = "_ZTIN4daal10algorithms6dtrees8internal4TreeE"
    base = "_ZTIN4daal4BaseE"
    ref_counter = "_ZTIN4daal8services10interface110RefCounterE"
    sizes = {
        s.name: s.size
        for s in parse_elf_metadata(core).symbols
        if s.name in (tree, base, ref_counter)
    }
    assert sizes == {tree: 24, base: 16, ref_counter: 40}
    assert (
        _inheritance_shape(sizes[ref_counter], 8)
        == "1 base class (__vmi_class_type_info)"
    )
    assert (
        _inheritance_shape(sizes[tree], 8) == "single base class (__si_class_type_info)"
    )
    assert _inheritance_shape(sizes[base], 8) == "no base class (__class_type_info)"

    symbols = subprocess.check_output(["nm", "-D", "--defined-only", core], text=True)
    addresses = {
        name: int(address, 16)
        for address, _, name in map(str.split, symbols.splitlines())
        if name in (tree, base, ref_counter)
    }
    targets = {
        addresses[ref_counter]: ("_ZTVN10__cxxabiv121__vmi_class_type_infoE", "10"),
        addresses[ref_counter] + 24: (
            "_ZTIN4daal8services10interface16AtomicIiEE",
            "0",
        ),
        addresses[tree]: ("_ZTVN10__cxxabiv120__si_class_type_infoE", "10"),
        addresses[tree] + 8: (tree.replace("_ZTI", "_ZTS"), "0"),
        addresses[tree] + 16: (base, "0"),
        addresses[base]: ("_ZTVN10__cxxabiv117__class_type_infoE", "10"),
        addresses[base] + 8: (base.replace("_ZTI", "_ZTS"), "0"),
    }
    relocations = subprocess.check_output(["readelf", "-rW", core], text=True)
    observed = {
        int(address, 16): (name, addend)
        for address, name, addend in re.findall(
            r"^([0-9a-f]+)\s+\S+\s+R_X86_64_64\s+\S+\s+(\S+) \+ ([0-9a-f]+)$",
            relocations,
            re.MULTILINE,
        )
        if int(address, 16) in targets
    }
    assert observed == targets


@pytest.mark.integration
def test_ref_counter_header_matches_core_rtti(tmp_path):
    packages = []
    for release, major in (("OLD", 3), ("NEW", 3), ("MAJOR", 4)):
        prefix = os.getenv(f"ABIFORGE_ONEDAL_{release}")
        if not prefix:
            pytest.skip("requires the three pinned Linux oneDAL SDKs")
        packages.append((Path(prefix).resolve(), major))

    source = (
        Path(__file__).resolve().parents[1]
        / "abiforge/fixtures/onedal/ref_counter_layout.cpp"
    )
    for sdk, major in packages:
        reader = tmp_path / "ref_counter_layout"
        subprocess.run(
            [
                "g++",
                "-std=c++17",
                f"-I{sdk}/include/dal",
                source,
                "-ldl",
                "-o",
                reader,
            ],
            check=True,
        )
        for runtime, runtime_major in packages:
            if runtime_major == major:
                lib = runtime / "lib"
                run = subprocess.run(
                    [reader, lib / f"libonedal_core.so.{major}"],
                    env={
                        "LD_LIBRARY_PATH": str(lib),
                        "LD_BIND_NOW": "1",
                        "LD_PRELOAD": str(lib / f"libonedal_thread.so.{major}"),
                    },
                    capture_output=True,
                    text=True,
                )
                assert run.returncode == 0, run.stderr
