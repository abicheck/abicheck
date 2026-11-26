# SPDX-License-Identifier: Apache-2.0
"""Withdraw one SDK-bound export at a time from copies of the real release DSOs."""

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from .onedal_export_surface import symbol_names
from .onedal_release_matrix import PRODUCT


def command(client):
    if client.name == "spmd":
        return ["mpiexec", "-n", "2", client]
    if client.name == "release_client":
        return [client, "collection"]
    return [client, client.parent / "data.csv"]


def client_env(client, image, lib):
    return {
        **{k: v for k, v in os.environ.items() if k != "LD_BIND_NOW"},
        "LC_ALL": "C",
        "ONEAPI_DEVICE_SELECTOR": "opencl:cpu",
        "LD_LIBRARY_PATH": os.pathsep.join(
            [
                str(image),
                str(lib),
                str(client.parent / "old"),
                os.environ.get("LD_LIBRARY_PATH", ""),
            ]
        ),
    }


def select_targets(targets, selection):
    witnesses = {}
    if selection:
        subset = {}
        for witness in json.loads(selection.read_text()):
            provider, symbol = witness["provider"], witness["symbol"]
            if symbol not in targets[provider]:
                raise ValueError(f"{provider}: unexpected witness {symbol}")
            witnesses[provider, symbol] = witness
            subset.setdefault(provider, []).append(symbol)
        if not subset:
            raise ValueError("empty witness selection")
        targets = subset
    return targets, witnesses


def check_controls(provider, target, image, used, controls):
    for control in controls:
        target.symlink_to(control / provider)
        for client in {c for c, _ in used.values()}:
            env = client_env(client, image, control)
            trace = subprocess.check_output(["ldd", "-r", client], env=env, text=True)
            if "undefined symbol:" in trace or f"{provider} => {target} (" not in trace:
                raise ValueError(f"{client}: baseline provider was not selected")
            subprocess.run(
                command(client),
                env=env,
                check=True,
                timeout=120,
                stdout=subprocess.DEVNULL,
            )
        target.unlink()


def run(sdk, minor, clients, out, surface_path, selection=None):
    surface = json.loads(surface_path.read_text())
    targets = surface["sdk_inventory"]["client_imports_outside_sdk"]
    targets, witnesses = select_targets(targets, selection)
    lib = sdk.resolve() / "lib"
    out.mkdir(parents=True, exist_ok=True)
    rows = []
    with tempfile.TemporaryDirectory(dir=out) as work:
        image = Path(work).resolve()
        for provider, names in targets.items():
            if not names:
                continue
            original = lib / provider
            exported = symbol_names(original)
            target = image / provider
            used = {}
            for symbol in names:
                # An executable known to import the exact symbol in a needed DSO.
                candidates = surface["providers"][provider]["imports_outside_sdk"][
                    symbol
                ]
                label = witnesses.get((provider, symbol), {}).get(
                    "client", candidates[0]
                )
                if label not in candidates:
                    raise ValueError(f"{label}: no recorded import of {symbol}")
                client = (clients / label).resolve()
                if symbol not in symbol_names(client, imports=True):
                    raise ValueError(f"{label} no longer imports {symbol}")
                used[symbol] = client, label
            check_controls(
                provider, target, image, used, (lib, minor.resolve() / "lib")
            )
            mapping = image / "renames"
            aliases = {name: "abiforge_withdrawn_" + name for name in names}

            def rename(entries):
                mapping.write_text(
                    "".join(f"{key} {aliases[key]}\n" for key in entries)
                )
                subprocess.run(
                    [
                        "patchelf",
                        "--rename-dynamic-symbols",
                        mapping,
                        "--output",
                        target,
                        original,
                    ],
                    check=True,
                )

            for name, (client, label) in used.items():
                rename([name])
                current = symbol_names(target)
                if exported - current != {name} or current - exported != {
                    aliases[name]
                }:
                    raise ValueError(f"non-isolated mutation of {provider}: {name}")
                env = client_env(client, image, lib)
                trace = subprocess.check_output(
                    ["ldd", "-r", client], env=env, text=True, stderr=subprocess.STDOUT
                )
                unresolved = [
                    line.split("undefined symbol:", 1)[1].split()
                    for line in trace.splitlines()
                    if "undefined symbol:" in line
                ]
                if [name, f"({client})"] not in unresolved:
                    raise ValueError(f"{label}: missing relocation witness for {name}")
                result = subprocess.run(
                    command(client),
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=120,
                )
                if result.returncode != 0 and (
                    result.returncode != 127
                    or f"undefined symbol: {name}" not in result.stderr
                ):
                    raise ValueError(
                        f"{label}: {name}: {result.returncode}: {result.stderr}"
                    )
                if (
                    selection
                    and result.returncode != witnesses[provider, name]["withdrawn"]
                ):
                    raise ValueError(f"{label}: changed workload outcome for {name}")
                rows.append(
                    {
                        "provider": provider,
                        "symbol": name,
                        "client": label,
                        "baseline_exit": 0,
                        "minor_exit": 0,
                        "unresolved_relocation": True,
                        "withdrawn": result.returncode,
                    }
                )
                target.unlink()
            # The public checker must identify every withdrawal, including ELF data.
            rename(names)
            report = image / "compare.json"
            proc = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "abicheck",
                    "compare",
                    original,
                    target,
                    "-o",
                    f"json={report}",
                ],
                capture_output=True,
            )
            data = json.loads(report.read_text())
            removed = {c["symbol"] for c in data["changes"] if "removed" in c["kind"]}
            if (
                proc.returncode != 4
                or data["verdict"] != "BREAKING"
                or not set(names) <= removed
            ):
                raise ValueError(
                    f"{provider}: missing checker withdrawals: {set(names) - removed}"
                )
            target.unlink()
    (out / "receipt.json").write_text(json.dumps(rows, indent=2) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for arg in ("sdk", "minor", "clients", "out"):
        parser.add_argument(arg, type=Path)
    parser.add_argument("--surface", type=Path, default=PRODUCT / "export-surface.json")
    parser.add_argument(
        "--selection", type=Path, help="replay only the selected scenario witnesses"
    )
    args = parser.parse_args()
    run(args.sdk, args.minor, args.clients, args.out, args.surface, args.selection)
