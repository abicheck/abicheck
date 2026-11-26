# SPDX-License-Identifier: Apache-2.0
"""Withdraw a shared export from just one provider and replay both linked clients."""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from .onedal_export_surface import symbol_names


def run(owner, sibling, owner_client, sibling_client, symbol, out):
    if owner.name == sibling.name:
        raise ValueError("providers must have distinct library names")
    out.mkdir(parents=True, exist_ok=True)
    receipt = out / "receipt.json"
    receipt.unlink(missing_ok=True)
    rows = {}
    with tempfile.TemporaryDirectory(dir=out.resolve()) as temp:
        root = Path(temp)
        old, new = root / "old", root / "new"
        for image in (old, new):
            image.mkdir()
            for lib in (owner, sibling):
                shutil.copyfile(lib, image / lib.name)
                if symbol not in symbol_names(image / lib.name):
                    raise ValueError(f"{lib}: missing shared export {symbol}")
        rename = root / "rename"
        rename.write_text(f"{symbol} abiforge_retired_export\n")
        subprocess.run(
            ["patchelf", "--rename-dynamic-symbols", rename, new / owner.name],
            check=True,
        )
        if symbol_names(old / owner.name) - symbol_names(new / owner.name) != {symbol}:
            raise ValueError("withdrawal was not isolated")
        for label, image in (("control", old), ("withdrawal", new)):
            report = root / "report.json"
            compared = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "abicheck",
                    "compare",
                    old,
                    image,
                    "-o",
                    f"json={report}",
                ],
                capture_output=True,
            )
            data = json.loads(report.read_text())
            broken = label == "withdrawal"
            if compared.returncode != (4 if broken else 0) or data["verdict"] not in (
                {"BREAKING"} if broken else {"COMPATIBLE", "NO_CHANGE"}
            ):
                raise ValueError(
                    f"{label}: unexpected package verdict {data['verdict']} ({compared.returncode})"
                )
            row = {"package_verdict": data["verdict"], "cli_exit": compared.returncode}
            for lib, client in (
                (owner, owner_client.resolve()),
                (sibling, sibling_client.resolve()),
            ):
                env = {
                    **os.environ,
                    "LC_ALL": "C",
                    "LD_DEBUG": "bindings",
                    "LD_LIBRARY_PATH": os.pathsep.join(
                        [str(image), os.environ.get("LD_LIBRARY_PATH", "")]
                    ),
                }
                result = subprocess.run(
                    [client], env=env, capture_output=True, text=True, timeout=90
                )
                missing = broken and lib == owner
                if missing:
                    if (
                        result.returncode != 127
                        or f"undefined symbol: {symbol}" not in result.stderr
                    ):
                        raise ValueError(
                            f"{client}: expected unresolved {symbol}, exit {result.returncode}"
                        )
                else:
                    binding = f"binding file {client} [0] to {image / lib.name} [0]:"
                    if result.returncode or not any(
                        binding in line and f"`{symbol}'" in line
                        for line in result.stderr.splitlines()
                    ):
                        raise ValueError(
                            f"{client}: expected binding to {lib.name}, exit {result.returncode}"
                        )
                row[lib.name] = {
                    "exit": result.returncode,
                    "binding": None if missing else lib.name,
                }
            rows[label] = row
    receipt.write_text(json.dumps({"symbol": symbol, "cases": rows}, indent=2) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("owner", "sibling", "owner_client", "sibling_client"):
        parser.add_argument(name, type=Path)
    parser.add_argument("symbol")
    parser.add_argument("out", type=Path)
    run(**vars(parser.parse_args()))
