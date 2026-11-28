# SPDX-License-Identifier: Apache-2.0
"""Exercise algorithm parameter selectors with a frozen Linux x86-64 client."""

import argparse
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

from .onedal_export_surface import symbol_names, validate_release_labels

CAMPAIGNS = {
    "logistic_regression": ("logistic_regression10parameters20train_parameters_cpu", 4),
    "decision_forest": (r"decision_forest10parameters20(?:infer|train)_parameters_cpu", 10),
    "pca_precomputed": (r"3pca10parameters20train_parameters_cpuI[fd]NS1_6method2v111precomputedE", 6),
    "pca_svd": (r"3pca10parameters20train_parameters_cpuI[fd]NS1_6method2v13svdE", 6),
    "pca_cov": (r"3pca10parameters20train_parameters_cpuI[fd]NS1_6method2v13covE", 6),
    "covariance": ("covariance10parameters22compute_parameters_cpu", 2),
    "linear_regression": ("linear_regression10parameters20train_parameters_cpu", 6),
}


def build_client(old, work, algorithm, abi_major, *, link_inputs=(), fixture=None, as_needed=False):
    is_pca = algorithm.startswith("pca_")
    fixture_name = "pca" if is_pca else algorithm
    fixture = fixture or (
        Path(__file__).resolve().parents[1]
        / f"fixtures/onedal/{fixture_name}_parameters.cpp"
    )
    exe = work / algorithm
    # With --as-needed the frontend must precede the provider of its parameter symbols.
    frontends = ["onedal", "onedal_parameters"] if as_needed else ["onedal_parameters", "onedal"]
    libraries = [*frontends, "onedal_core", "onedal_thread"]
    subprocess.run(
        [
            "g++",
            "-std=c++17",
            f"-I{old}/include",
            f"-I{old}/include/dal",
            *([f"-DPCA_METHOD={algorithm[4:]}"] if is_pca else []),
            fixture,
            *link_inputs,
            f"-L{old}/lib",
            "-Wl,--as-needed" if as_needed else "-Wl,--no-as-needed",
            f"-Wl,-rpath-link,{old}/lib",
            *(f"-l:lib{lib}.so.{abi_major}" for lib in libraries),
            "-l:libtbb.so.12",
            "-o",
            exe,
        ],
        check=True,
    )
    return exe


def audit(old, minor, work, algorithm="covariance", *, abi_major=3):
    selector_name, expected_count = CAMPAIGNS[algorithm]
    releases = [old] if minor is None else [old, minor]
    validate_release_labels(releases)
    is_pca = algorithm.startswith("pca_")
    exe = build_client(old, work, algorithm, abi_major)
    provider = f"libonedal_parameters.so.{abi_major}"
    selectors = sorted(
        name
        for name in symbol_names(old / "lib" / provider)
        if re.search(selector_name, name) and "EEclE" in name
    )
    if len(selectors) != expected_count:
        raise ValueError(f"expected {expected_count} exported {algorithm} CPU call operators")
    expected_calls = selectors
    if is_pca:
        expected_calls = [name for name in selectors if "11train_input" in name]
        if len(expected_calls) != 2:
            raise ValueError(f"expected two {algorithm} batch overloads")
    elif algorithm == "decision_forest":
        expected_calls = []
    results = {}
    for release in releases:
        lib = release.resolve() / "lib"
        env = {**os.environ, "LD_LIBRARY_PATH": str(lib), "LD_BIND_NOW": "1"}
        run = subprocess.run(
            [exe],
            env={**env, "LD_DEBUG": "bindings"},
            capture_output=True,
            text=True,
            check=True,
        )
        helpers = []
        if algorithm == "decision_forest":
            frontend = lib / f"libonedal.so.{abi_major}"
            shared = symbol_names(frontend, imports=True) & symbol_names(lib / provider)
            helpers = sorted(name for name in shared if "EEclE" not in name)
            if len(helpers) != 2:
                raise ValueError(f"unexpected parameter helper inventory in {release}")
        bound = []
        for name in selectors + helpers:
            binding = f"binding file {lib}/libonedal.so.{abi_major} [0] to {lib}/{provider} [0]: normal symbol `{name}'"
            if binding in run.stderr:
                bound.append(name)

        reached = []
        for name in selectors + helpers:
            # This replacement exits before reading arguments or returning a value.
            probe = f'#include <stdlib.h>\nvoid hit(void) __asm__("{name}");\nvoid hit(void) {{ _Exit(42); }}\n'
            shim = work / "selector.so"
            subprocess.run(
                ["gcc", "-x", "c", "-", "-fPIC", "-shared", "-o", shim],
                input=probe,
                text=True,
                check=True,
            )
            code = subprocess.run(
                [exe], env={**env, "LD_PRELOAD": str(shim)}
            ).returncode
            if code == 0:
                continue
            if code != 42:
                raise ValueError(
                    f"selector call not observed in {release}: {name} (exit {code})"
                )
            reached.append(name)
        helper_calls = [name for name in reached if name in helpers]
        helper_bindings = [name for name in bound if name in helpers]
        bound = [name for name in bound if name not in helpers]
        reached = [name for name in reached if name not in helpers]
        if bound != expected_calls or reached != expected_calls:
            raise ValueError(f"{algorithm} selector coverage changed in {release}: {bound=}, {reached=}")
        control_key = (
            "unimplemented_exception_control_exit"
            if algorithm == "logistic_regression"
            else "numerical_control_exit"
        )
        results[release.name] = {
            control_key: run.returncode,
            "called_selectors": reached,
        }
        if helpers:
            if helper_bindings != helpers or helper_calls != helpers:
                raise ValueError(f"decision forest helper coverage changed in {release}")
            results[release.name]["bound_helpers"] = helper_bindings
            results[release.name]["called_helpers"] = helper_calls
        if expected_calls != selectors:
            results[release.name]["bound_selectors"] = bound
            results[release.name]["unobserved_selectors"] = sorted(set(selectors) - set(reached))
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("old", type=Path, help="compile the frozen client against this .so.3 SDK")
    parser.add_argument("minor", type=Path, help="run the same binary against this second .so.3 package")
    parser.add_argument("--major", type=Path, help="also rebuild against a oneDAL .so.4 SDK")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--algorithm", choices=[*CAMPAIGNS, "all"], default="covariance")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as tmp:
        if args.major:
            validate_release_labels([args.old, args.minor, args.major])
        reports = {}
        algorithms = CAMPAIGNS if args.algorithm == "all" else [args.algorithm]
        for algorithm in algorithms:
            result = audit(args.old, args.minor, Path(tmp), algorithm)
            if args.major:
                result.update(audit(args.major, None, Path(tmp), algorithm, abi_major=4))
            reports[algorithm] = result
        if args.algorithm == "all":
            report = {"frozen_client_sdk": args.old.name, "campaigns": reports}
            if args.major:
                report["rebuilt_client_sdk"] = args.major.name
        else:
            report = reports[args.algorithm]
    args.out.write_text(json.dumps(report, indent=2) + "\n")
