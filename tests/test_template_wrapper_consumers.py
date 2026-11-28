"""An SDK wrapper can be imported or instantiated in the downstream binary."""

import json
import os
import subprocess
import sys

import pytest

from tests._abicheck_subprocess import abicheck_command


@pytest.mark.integration
@pytest.mark.skipif(sys.platform != "linux", reason="uses the ELF dynamic loader")
def test_template_wrapper_and_implementation_have_distinct_consumers(tmp_path):
    from abiforge.run.template_wrapper_consumers import build_cases

    providers, clients, wrapper, implementation = build_cases(tmp_path)
    for case, library in providers.items():
        report = tmp_path / f"{case}.json"
        compared = subprocess.run(
            abicheck_command(
                "compare",
                providers["both"],
                library,
                *(arg for client in clients for arg in ("--used-by", client)),
                "-o",
                f"json={report}",
            )
        )
        data = json.loads(report.read_text())
        assert compared.returncode == (0 if case == "both" else 4)
        rows = {row["app"]: row for row in data["used_by"]}
        for client, required in clients.items():
            missing = (case == "wrapper_only" and required == implementation) or (
                case == "implementation_only" and required == wrapper
            )
            run = subprocess.run(
                [client],
                env={
                    **os.environ,
                    "LD_LIBRARY_PATH": str(library.parent),
                    "LD_BIND_NOW": "1",
                },
                capture_output=True,
                text=True,
            )
            assert run.returncode == (127 if missing else 0), run.stderr
            if missing:
                assert required in run.stderr
            row = rows[str(client)]
            assert row["required_symbol_count"] == 1
            assert row["missing_symbols"] == ([required] if missing else [])
            assert (row["verdict"] == "BREAKING") == missing
