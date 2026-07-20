"""Contract tests for the checksum-pinned CastXML Superbuild installer."""
from __future__ import annotations

import os
import platform
import re
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "action" / "install-castxml.sh"
WORKFLOWS = (
    ROOT / ".github/workflows/ci.yml",
    ROOT / ".github/workflows/examples-validation.yml",
    ROOT / ".github/workflows/examples-validation-nightly.yml",
    ROOT / ".github/workflows/publish.yml",
    ROOT / ".github/workflows/realworld-validation.yml",
)


def _host_asset() -> str | None:
    if platform.system() != "Linux" or not Path("/etc/os-release").exists():
        return None
    values = {}
    for line in Path("/etc/os-release").read_text(encoding="utf-8").splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            values[key] = value.strip('"')
    machine = platform.machine()
    key = (values.get("ID"), values.get("VERSION_ID"), machine)
    return {
        ("ubuntu", "22.04", "x86_64"): "castxml-ubuntu-22.04-x86_64",
        ("ubuntu", "22.04", "aarch64"): "castxml-ubuntu-22.04-arm-aarch64",
        ("ubuntu", "24.04", "x86_64"): "castxml-ubuntu-24.04-x86_64",
        ("ubuntu", "24.04", "aarch64"): "castxml-ubuntu-24.04-arm-aarch64",
    }.get(key)


def test_installer_pins_release_versions_and_four_asset_digests() -> None:
    text = INSTALLER.read_text(encoding="utf-8")
    assert 'CASTXML_TAG="v2026.01.30"' in text
    assert 'EXPECTED_CASTXML_VERSION="0.6.20260105-g9864b1e"' in text
    assert 'EXPECTED_BUNDLED_CLANG_VERSION="21.1.8"' in text
    digests = re.findall(r'sha256="([0-9a-f]+)"', text)
    assert len(digests) == 4
    assert all(len(digest) == 64 for digest in digests)
    assert len(set(digests)) == 4


def test_installer_verifies_before_extracting_and_persists_path() -> None:
    text = INSTALLER.read_text(encoding="utf-8")
    assert text.index("sha256sum --check --strict") < text.index("tar -xzf")
    assert '>> "$GITHUB_PATH"' in text
    assert "--strip-components=1" in text


def test_linux_workflow_jobs_using_installer_pin_supported_runner() -> None:
    for path in WORKFLOWS:
        workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
        for name, job in workflow.get("jobs", {}).items():
            steps = job.get("steps", [])
            if not any(
                "action/install-castxml.sh" in str(step.get("run", ""))
                for step in steps
            ):
                continue
            runs_on = job.get("runs-on")
            if isinstance(runs_on, str) and "matrix.os" not in runs_on:
                assert runs_on in {"ubuntu-22.04", "ubuntu-24.04", "ubuntu-24.04-arm"}, (
                    path.name,
                    name,
                    runs_on,
                )
                continue
            matrix_os = job.get("strategy", {}).get("matrix", {}).get("os", [])
            linux_runners = [value for value in matrix_os if str(value).startswith("ubuntu")]
            assert linux_runners
            assert set(linux_runners) <= {"ubuntu-22.04", "ubuntu-24.04"}


def test_composite_installer_keeps_unsupported_linux_best_effort() -> None:
    text = (ROOT / "action/install-deps.sh").read_text(encoding="utf-8")
    assert 'packages+=(castxml)' in text
    assert '. "$(dirname "$0")/install-castxml.sh"' in text
    assert "No pinned CastXML Superbuild" in text


def test_cached_install_validates_versions_and_persists_path(tmp_path: Path) -> None:
    asset = _host_asset()
    if asset is None:
        pytest.skip("behavioral installer test needs a supported Ubuntu runner")
    castxml = tmp_path / "install" / "v2026.01.30" / asset / "bin" / "castxml"
    castxml.parent.mkdir(parents=True)
    castxml.write_text(
        "#!/bin/sh\n"
        "echo 'castxml version 0.6.20260105-g9864b1e'\n"
        "echo 'clang version 21.1.8'\n",
        encoding="utf-8",
    )
    castxml.chmod(0o755)
    github_path = tmp_path / "github-path"
    env = {
        **os.environ,
        "ABICHECK_CASTXML_INSTALL_ROOT": str(tmp_path / "install"),
        "GITHUB_PATH": str(github_path),
    }
    result = subprocess.run(
        ["bash", str(INSTALLER)], capture_output=True, text=True, env=env, check=False
    )
    assert result.returncode == 0, result.stderr
    assert github_path.read_text(encoding="utf-8").strip() == str(castxml.parent)
    assert "Selected CastXML" in result.stdout


def test_local_archive_checksum_rejection_is_fail_closed(tmp_path: Path) -> None:
    if _host_asset() is None:
        pytest.skip("behavioral installer test needs a supported Ubuntu runner")
    bad_archive = tmp_path / "untrusted.tar.gz"
    bad_archive.write_bytes(b"not the pinned release")
    env = {
        **os.environ,
        "ABICHECK_CASTXML_ARCHIVE": str(bad_archive),
        "ABICHECK_CASTXML_INSTALL_ROOT": str(tmp_path / "install"),
    }
    result = subprocess.run(
        ["bash", str(INSTALLER)], capture_output=True, text=True, env=env, check=False
    )
    assert result.returncode != 0
    assert "FAILED" in result.stdout + result.stderr
