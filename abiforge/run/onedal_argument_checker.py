# SPDX-License-Identifier: Apache-2.0
"""Run and score public L2 snapshot comparisons for the oneDAL argument campaign."""

from __future__ import annotations

import json
import os
import subprocess
import sys


def compare_arguments(prefix, out, client_source, symbols):
    env = {**os.environ, "ABICHECK_AST_FRONTEND": "clang"}
    cli = [sys.executable, "-m", "abicheck"]
    snapshots = {}
    for variant in ("old", "control", "mutant"):
        directory = out / variant
        header = directory / "api.hpp"
        header.write_bytes(client_source.read_bytes())
        snapshot = directory / "snapshot.json.gz"
        subprocess.run(
            [
                *cli,
                "dump",
                prefix / "lib/libonedal_core.so.3",
                "-H",
                header,
                "-I",
                directory,
                "-I",
                directory / "dal",
                "-I",
                prefix / "include",
                "-I",
                prefix / "include/dal",
                "-o",
                snapshot,
            ],
            env=env,
            check=True,
        )
        snapshots[variant] = snapshot
    findings = {}
    for variant in ("control", "mutant"):
        report = out / variant / "compare.json"
        report.unlink(missing_ok=True)
        proc = subprocess.run(
            [
                *cli,
                "compare",
                snapshots["old"],
                snapshots[variant],
                "-o",
                f"json={report}",
            ],
            env=env,
        )
        data = json.loads(report.read_text())
        if not any(
            item["layer"] == "L2" and item["status"] == "present"
            for item in data["layer_coverage"]
        ):
            raise RuntimeError(f"{variant}: missing L2 evidence")
        changes = [
            c
            for c in data["changes"]
            if c["symbol"] in symbols and c["kind"] == "value_abi_trait_changed"
        ]
        findings[variant] = {
            "verdict": data["verdict"],
            "exit": proc.returncode,
            "arguments": {
                c["symbol"]: [c["old_value"], c["new_value"]] for c in changes
            },
        }
    return findings
