import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from _abicheck_subprocess import abicheck_command

from abicheck.workflows.release_consumer import release_consumer_binding
from abiforge.run.provider_campaign import run


@pytest.mark.skipif(
    sys.platform != "linux" or not shutil.which("gcc"), reason="Linux ELF fixture"
)
def test_release_consumer_matches_old_binary(tmp_path):
    run(tmp_path)
    independent = tmp_path / "independent.c"
    independent.write_text("int main(void) { return 0; }")
    subprocess.run(["gcc", independent, "-o", tmp_path / "client2"], check=True)
    for image in ("old", "reachable", "missing", "wrong_version", "unreachable"):
        shutil.copy2(tmp_path / "client2", tmp_path / image / "client2")
    for case, loadability, status in [
        ("reachable", "pass", "resolved_ok"),
        ("missing", "fail", "missing"),
        ("wrong_version", "fail", "version_mismatch"),
        ("unreachable", "fail", "missing"),
    ]:
        output = tmp_path / case / "release.json"
        result = subprocess.run(
            abicheck_command(
                "compare",
                str(tmp_path / "old"),
                str(tmp_path / case),
                "--used-by",
                str(tmp_path / "old/client"),
                "--used-by",
                str(tmp_path / "old/client2"),
                "-o",
                "markdown=" + str(tmp_path / "binding.md"),
                "-o",
                "json=" + str(output),
            )
        )
        if case == "missing":
            markdown = (tmp_path / "binding.md").read_text()
            assert "Unresolved dependency" in markdown and "libowner.so" in markdown
        assert result.returncode == 4
        report = json.loads(output.read_text())
        assert report["verdict"] == "BREAKING"
        binding = report["consumer_binding"]
        (second,) = binding["additional_consumers"]
        assert second["client"] == "client2"
        assert second["loadability"] == "pass"
        assert all(row["symbol"] != "value" for row in second["imports"])
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
            tmp_path / "old",
            image,
            tmp_path / "old/client",
            ":".join("/" + d for d in order),
        )
        assert (actual.returncode == 0) == (expected == "pass")
        assert report["loadability"] == expected
        row = next(row for row in report["imports"] if row["symbol"] == "value")
        assert row["new"]["provider"] == str(image / order[0] / "libowner.so")
        assert row["new"]["search_reason"] == "ld_library_path"

    # Keep the release inventory unambiguous. Only the configured directory
    # can supply the V2-only owner to this V1 client.
    for dirname in ("good", "lib"):
        (image / dirname / "libowner.so").unlink()
    config = tmp_path / "project.yml"
    config.write_text("deps:\n  ld_library_path: /bad:/lib\n")
    output = tmp_path / "configured.json"
    result = subprocess.run(
        abicheck_command(
            "compare",
            tmp_path / "old",
            image,
            "--used-by",
            tmp_path / "old/client",
            "--config",
            config,
            "-o",
            "json=" + str(output),
        )
    )
    assert result.returncode == 4
    report = json.loads(output.read_text())["consumer_binding"]
    row = next(row for row in report["imports"] if row["symbol"] == "value")
    assert row["new"]["status"] == "version_mismatch"
    assert row["new"]["provider"] == str(image / "bad/libowner.so")
    assert row["new"]["search_reason"] == "ld_library_path"


def test_release_consumer_rejects_invalid_client(tmp_path):
    from abicheck.errors import ReleaseOperandUsageError

    old, new = tmp_path / "old", tmp_path / "new"
    for root in (old, new):
        root.mkdir()
    for path in (tmp_path / "absolute", Path("../escape")):
        with pytest.raises(ReleaseOperandUsageError, match="inside the old"):
            release_consumer_binding(old, new, path)
    with pytest.raises(ReleaseOperandUsageError, match="inside both"):
        release_consumer_binding(old, new, old / "client")
    for root in (old, new):
        (root / "client").write_bytes(b"text")
    with pytest.raises(ReleaseOperandUsageError, match="must be ELF"):
        release_consumer_binding(old, new, old / "client")
    for root in (old, new):
        (root / "client").write_bytes(b"\x7fELF" + root.name.encode())
    with pytest.raises(ReleaseOperandUsageError, match="identical old consumer"):
        release_consumer_binding(old, new, old / "client")
