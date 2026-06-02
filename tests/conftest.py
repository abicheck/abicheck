"""conftest.py — pytest configuration for abicheck tests."""
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

try:
    import filelock  # explicit dev dependency; used for xdist cmake locking
except ImportError:
    filelock = None  # type: ignore[assignment]


def pytest_addoption(parser: pytest.Parser) -> None:
    """Register --update-goldens CLI option for golden-output tests."""
    parser.addoption(
        "--update-goldens",
        action="store_true",
        default=False,
        help="Re-generate golden output files in tests/golden/ instead of comparing.",
    )


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "integration: requires platform-specific compiler (gcc/g++ on Linux, clang on macOS, MinGW gcc on Windows)",
    )
    config.addinivalue_line(
        "markers",
        "abicc: requires abi-compliance-checker + gcc/g++ — ABICC parity tests",
    )
    config.addinivalue_line(
        "markers",
        "golden: golden-output regression test (use --update-goldens to refresh)",
    )
    config.addinivalue_line(
        "markers",
        "slow: marks tests as slow (deselect with '-m \"not slow\"')",
    )
    config.addinivalue_line(
        "markers",
        "msvc: requires the MSVC toolchain (cl.exe) — Windows PDB end-to-end tests",
    )


def _integration_skip_reason() -> str | None:
    """Return a skip reason if integration tests cannot run, or None if they can.

    Platform-specific requirements:
    - Linux: castxml + gcc + g++ (ELF integration tests)
    - macOS: clang (Mach-O integration tests; ships with Xcode CLT)
    - Windows: gcc from MinGW (PE/DLL integration tests)
    """
    if sys.platform == "darwin":
        if shutil.which("clang") is None:
            return "clang not found in PATH (required for macOS integration tests)"
        return None

    if sys.platform == "win32":
        if shutil.which("gcc") is None:
            return "gcc (MinGW) not found in PATH (required for Windows integration tests)"
        return None

    # Linux / other Unix: require castxml + gcc + g++ for ELF tests
    missing = [t for t in ("castxml", "gcc", "g++") if shutil.which(t) is None]
    if missing:
        return f"Required tools not found: {', '.join(missing)}"
    return None


# Marker → external tool that must be on PATH for that marker's tests to run.
# Add a row here to gate a new marker on tool availability — no copy-paste loop.
_MARKER_REQUIRED_TOOL: dict[str, str] = {
    "abicc": "abi-compliance-checker",
    "msvc": "cl",  # MSVC compiler driver (set up by the MSVC dev environment)
}


def pytest_collection_modifyitems(config: pytest.Config, items: list) -> None:
    reason = _integration_skip_reason()
    if reason:
        skip = pytest.mark.skip(reason=reason)
        for item in items:
            if "integration" in item.keywords:
                item.add_marker(skip)

    for marker, tool in _MARKER_REQUIRED_TOOL.items():
        if shutil.which(tool) is not None:
            continue
        skip = pytest.mark.skip(reason=f"{tool} not found in PATH")
        for item in items:
            if marker in item.keywords:
                item.add_marker(skip)


@pytest.fixture
def update_goldens(request: pytest.FixtureRequest) -> bool:
    """True when --update-goldens flag is passed."""
    return bool(request.config.getoption("--update-goldens"))


# ---------------------------------------------------------------------------
# Silent-skip guard
# ---------------------------------------------------------------------------
#
# Marker-gated lanes (abicc / libabigail / integration / msvc) self-skip when
# their external tool is missing — correct locally, but dangerous in CI: if the
# tool silently fails to install, every test in the lane skips and the lane goes
# *green with zero work done*. To close that hole, a lane can export
# ``ABICHECK_MIN_EXECUTED=<n>``; the session then fails unless at least <n>
# tests actually reached their call phase (passed or failed — skips don't count).

_EXECUTED_TESTS = 0


def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    """Count tests that actually executed (ran their call phase)."""
    global _EXECUTED_TESTS
    if report.when == "call" and report.outcome in ("passed", "failed"):
        _EXECUTED_TESTS += 1


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """Fail the session if fewer tests ran than ``ABICHECK_MIN_EXECUTED`` demands.

    Skipped on xdist workers (the controller aggregates every worker's reports
    and is the one that owns the final exit status).
    """
    if hasattr(session.config, "workerinput"):
        return  # this is an xdist worker; let the controller decide
    raw = os.environ.get("ABICHECK_MIN_EXECUTED")
    if not raw:
        return
    try:
        minimum = int(raw)
    except ValueError:
        return
    if _EXECUTED_TESTS < minimum:
        session.exitstatus = 1
        reporter = session.config.pluginmanager.get_plugin("terminalreporter")
        msg = (
            f"ABICHECK_MIN_EXECUTED={minimum} but only {_EXECUTED_TESTS} test(s) "
            "actually ran — the lane's external tool likely failed to install "
            "(tests silently skipped). Treating as a CI failure."
        )
        if reporter is not None:
            reporter.write_line("")
            reporter.write_line(msg, red=True, bold=True)
        else:  # pragma: no cover - terminalreporter always present in practice
            print(msg)


def _cmake_configure_once(build_dir: Path) -> bool:
    """Run cmake configure into *build_dir*.  Returns True on success."""
    examples_dir = Path(__file__).parent.parent / "examples"
    cmake = shutil.which("cmake")
    if not cmake:
        return False
    try:
        r = subprocess.run(
            [cmake, "-S", str(examples_dir), "-B", str(build_dir),
             "-DCMAKE_BUILD_TYPE=Debug"],
            capture_output=True, text=True, timeout=120,
        )
    except subprocess.TimeoutExpired:
        return False
    return r.returncode == 0


@pytest.fixture(scope="session")
def shared_cmake_build_dir(tmp_path_factory: pytest.TempPathFactory) -> Path | None:
    """Session-scoped CMake build directory for integration tests.

    Configures the examples/ CMakeLists.txt **once** per session so that
    individual tests only need to run ``cmake --build`` for their specific
    targets.  On Windows this avoids ~30 redundant cmake-configure passes
    (each one re-parses all 63 example CMakeLists).

    When running under pytest-xdist, a file lock ensures only the first
    worker runs the expensive cmake configure; other workers wait and
    reuse the same build directory.
    """
    examples_dir = Path(__file__).parent.parent / "examples"
    cmake_lists = examples_dir / "CMakeLists.txt"
    cmake = shutil.which("cmake")

    if not cmake or not cmake_lists.exists():
        return None

    # Under pytest-xdist, share a single build dir across all workers
    is_xdist = os.environ.get("PYTEST_XDIST_WORKER") is not None

    if is_xdist and filelock is not None:
        # All workers share the same root tmp dir; use a fixed name
        root_tmp = tmp_path_factory.getbasetemp().parent
        build_dir = root_tmp / "cmake_shared_build"
        lock_path = root_tmp / "cmake_shared_build.lock"
        done_flag = root_tmp / "cmake_shared_build.done"
        fail_flag = root_tmp / "cmake_shared_build.fail"

        try:
            with filelock.FileLock(str(lock_path), timeout=180):
                if fail_flag.exists():
                    return None
                if not done_flag.exists():
                    build_dir.mkdir(exist_ok=True)
                    if _cmake_configure_once(build_dir):
                        done_flag.write_text("ok")
                    else:
                        fail_flag.write_text("fail")
                        return None
        except filelock.Timeout:
            return None

        return build_dir

    # Sequential execution: one configure per session
    build_dir = tmp_path_factory.mktemp("cmake_build")
    if not _cmake_configure_once(build_dir):
        return None

    return build_dir
