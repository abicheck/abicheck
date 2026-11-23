import json
import shutil
import subprocess
import sys

import pytest

from abiforge.run.provider_campaign import run


@pytest.mark.skipif(
    sys.platform != "linux" or not shutil.which("gcc"), reason="Linux ELF fixture"
)
def test_release_consumer_matches_old_binary(tmp_path):
    run(tmp_path)
    for case, loadability, status in [
        ("reachable", "pass", "resolved_ok"),
        ("missing", "fail", "missing"),
        ("wrong_version", "fail", "version_mismatch"),
        ("unreachable", "fail", "missing"),
    ]:
        output = tmp_path / case / "release.json"
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "abicheck.cli",
                "compare",
                str(tmp_path / "old"),
                str(tmp_path / case),
                "--release-consumer",
                "client",
                "-o",
                "json=" + str(output),
            ]
        )
        assert result.returncode == 4
        report = json.loads(output.read_text())
        assert report["verdict"] == "BREAKING"
        binding = report["consumer_binding"]
        assert binding["loadability"] == loadability
        (symbol,) = [row for row in binding["imports"] if row["symbol"] == "value"]
        assert symbol["version"] == "V1"
        assert symbol["old"]["provider"].endswith("/libapi.so")
        assert symbol["new"]["status"] == status
