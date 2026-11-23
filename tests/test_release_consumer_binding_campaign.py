import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from abicheck.frontends.cli.release_consumer import release_consumer_binding
from abiforge.run.provider_campaign import run


@pytest.mark.skipif(
    sys.platform != "linux" or not shutil.which("gcc"), reason="Linux ELF fixture"
)
def test_release_consumer_matches_old_binary(tmp_path):
    run(tmp_path)
    for image in ("old", "reachable", "missing", "wrong_version", "unreachable"):
        shutil.copy2(tmp_path / image / "client", tmp_path / image / "client2")
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
                "--release-consumer",
                "client2",
                "-o",
                "json=" + str(output),
            ]
        )
        assert result.returncode == 4
        report = json.loads(output.read_text())
        assert report["verdict"] == "BREAKING"
        binding = report["consumer_binding"]
        (second,) = binding["additional_consumers"]
        assert second["client"] == "client2"
        assert second["loadability"] == loadability
        assert binding["loadability"] == loadability
        (symbol,) = [row for row in binding["imports"] if row["symbol"] == "value"]
        assert symbol["version"] == "V1"
        assert symbol["old"]["provider"].endswith("/libapi.so")
        assert symbol["new"]["status"] == status

    image = tmp_path / "reachable"
    for dirname, source in (("good", "reachable"), ("bad", "wrong_version")):
        (image / dirname).mkdir()
        shutil.copy2(tmp_path / source / "lib/libowner.so", image / dirname)
    for order, expected in (
        (["good", "bad", "lib"], "pass"),
        (["bad", "good", "lib"], "fail"),
    ):
        actual = subprocess.run(
            [image / "client"],
            env={
                **os.environ,
                "LD_BIND_NOW": "1",
                "LD_LIBRARY_PATH": os.pathsep.join(str(image / d) for d in order),
            },
            capture_output=True,
        )
        report = release_consumer_binding(
            tmp_path / "old", image, Path("client"), ":".join("/" + d for d in order)
        )
        assert (actual.returncode == 0) == (expected == "pass")
        assert report["loadability"] == expected
        row = next(row for row in report["imports"] if row["symbol"] == "value")
        assert row["new"]["provider"] == str(image / order[0] / "libowner.so")
        assert row["new"]["search_reason"] == "ld_library_path"
