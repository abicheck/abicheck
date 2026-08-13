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
    ROOT / ".github/workflows/performance.yml",
    ROOT / ".github/workflows/publish.yml",
    ROOT / ".github/workflows/realworld-validation.yml",
)
# Most jobs no longer call the installer script directly: they go through this
# composite, which wraps it in actions/cache. A job reaching the installer that
# way is bound by exactly the same runner allowlist, so the contract test below
# has to recognise both spellings -- matching only `run:` text silently skipped
# every migrated job (Codex review on PR #685).
CASTXML_COMPOSITE = "./.github/actions/setup-castxml"


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
                or str(step.get("uses", "")).strip() == CASTXML_COMPOSITE
                for step in steps
            ):
                continue
            runs_on = job.get("runs-on")
            if isinstance(runs_on, str) and "matrix.os" not in runs_on:
                assert runs_on in {
                    "ubuntu-22.04",
                    "ubuntu-24.04",
                    "ubuntu-24.04-arm",
                }, (
                    path.name,
                    name,
                    runs_on,
                )
                continue
            matrix_os = job.get("strategy", {}).get("matrix", {}).get("os", [])
            linux_runners = [
                value for value in matrix_os if str(value).startswith("ubuntu")
            ]
            assert linux_runners
            assert set(linux_runners) <= {"ubuntu-22.04", "ubuntu-24.04"}


def test_composite_installer_keeps_unsupported_linux_best_effort() -> None:
    text = (ROOT / "action/install-deps.sh").read_text(encoding="utf-8")
    assert "packages+=(castxml)" in text
    assert '. "$(dirname "$0")/install-castxml.sh"' in text
    assert "No pinned CastXML Superbuild" in text


@pytest.mark.skipif(os.name == "nt", reason="exercises a Linux shell installer")
def test_composite_installer_uses_distro_castxml_on_unsupported_arch(
    tmp_path: Path,
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    log = tmp_path / "sudo.log"
    (fake_bin / "uname").write_text(
        '#!/bin/sh\ncase "$1" in -s) echo Linux ;; -m) echo ppc64le ;; esac\n',
        encoding="utf-8",
    )
    (fake_bin / "apt-get").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    (fake_bin / "sudo").write_text(
        f"#!/bin/sh\nprintf '%s\\n' \"$*\" >> {log!s}\n",
        encoding="utf-8",
    )
    for command in ("uname", "apt-get", "sudo"):
        (fake_bin / command).chmod(0o755)

    result = subprocess.run(
        ["bash", str(ROOT / "action/install-deps.sh")],
        capture_output=True,
        text=True,
        env={**os.environ, "PATH": f"{fake_bin}:/usr/bin:/bin"},
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "castxml" in log.read_text(encoding="utf-8")
    assert "No pinned CastXML Superbuild" in result.stdout
    assert "ppc64le" in result.stdout


@pytest.mark.skipif(os.name == "nt", reason="exercises a Linux shell installer")
def test_composite_installer_minimal_ubuntu_remains_warning_only(
    tmp_path: Path,
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    uname = fake_bin / "uname"
    uname.write_text(
        '#!/bin/sh\ncase "$1" in -s) echo Linux ;; -m) echo x86_64 ;; esac\n',
        encoding="utf-8",
    )
    uname.chmod(0o755)

    result = subprocess.run(
        ["/bin/bash", str(ROOT / "action/install-deps.sh")],
        capture_output=True,
        text=True,
        env={**os.environ, "PATH": str(fake_bin)},
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "apt-get not found" in result.stdout
    assert "No pinned CastXML Superbuild" in result.stdout
    assert "curl is required" not in result.stderr


def test_existing_install_is_replaced_before_version_probe(tmp_path: Path) -> None:
    asset = _host_asset()
    if asset is None:
        pytest.skip("behavioral installer test needs a supported Ubuntu runner")
    castxml = tmp_path / "install" / "v2026.01.30" / asset / "bin" / "castxml"
    castxml.parent.mkdir(parents=True)
    poison_log = tmp_path / "poison.log"
    castxml.write_text(
        "#!/bin/sh\n"
        f"printf '%s\\n' poisoned >> {poison_log!s}\n"
        "echo 'castxml version 0.6.20260105-g9864b1e'\n"
        "echo 'clang version 21.1.8'\n",
        encoding="utf-8",
    )
    castxml.chmod(0o755)

    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    (fake_bin / "curl").write_text(
        "#!/bin/sh\n"
        "while [ $# -gt 0 ]; do\n"
        '  if [ "$1" = --output ]; then shift; output=$1; fi\n'
        "  shift || exit 0\n"
        "done\n"
        'printf archive > "$output"\n',
        encoding="utf-8",
    )
    (fake_bin / "sha256sum").write_text(
        "#!/bin/sh\ncat >/dev/null\nexit 0\n", encoding="utf-8"
    )
    (fake_bin / "tar").write_text(
        "#!/bin/sh\n"
        "while [ $# -gt 0 ]; do\n"
        '  if [ "$1" = -C ]; then shift; dest=$1; fi\n'
        "  shift || exit 0\n"
        "done\n"
        'mkdir -p "$dest/bin"\n'
        "cat > \"$dest/bin/castxml\" <<'EOF'\n"
        "#!/bin/sh\n"
        "echo 'castxml version 0.6.20260105-g9864b1e'\n"
        "echo 'clang version 21.1.8'\n"
        "EOF\n"
        'chmod +x "$dest/bin/castxml"\n',
        encoding="utf-8",
    )
    for command in ("curl", "sha256sum", "tar"):
        (fake_bin / command).chmod(0o755)

    github_path = tmp_path / "github-path"
    env = {
        **os.environ,
        "ABICHECK_CASTXML_INSTALL_ROOT": str(tmp_path / "install"),
        "GITHUB_PATH": str(github_path),
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
    }
    result = subprocess.run(
        ["bash", str(INSTALLER)], capture_output=True, text=True, env=env, check=False
    )
    assert result.returncode == 0, result.stderr
    assert not poison_log.exists()
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


def test_composite_castxml_action_invokes_the_pinned_installer() -> None:
    """The runner-allowlist contract above treats a job that uses the composite
    as a job that runs the installer. That equivalence is only true while the
    composite actually invokes it, so assert the linkage rather than assuming
    it -- otherwise the contract test would keep passing while checking
    nothing."""
    action = ROOT / ".github/actions/setup-castxml/action.yml"
    spec = yaml.safe_load(action.read_text(encoding="utf-8"))
    steps = spec["runs"]["steps"]
    assert any("action/install-castxml.sh" in str(step.get("run", "")) for step in steps), (
        "the composite must still run the pinned installer"
    )
    # The cache is keyed on the installer's own contents; if that stops being
    # true a re-pin would silently reuse the previous build's cache entry.
    assert any(
        "hashFiles('action/install-castxml.sh')" in str(step.get("with", {}).get("key", ""))
        for step in steps
    ), "the cache key must still be derived from the installer script"
