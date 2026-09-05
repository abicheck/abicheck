"""conftest.py — pytest configuration for abicheck tests."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

try:
    import filelock  # explicit dev dependency; used for xdist cmake locking
except ImportError:
    filelock = None  # type: ignore[assignment]


def _hypothesis_profile_for_mutmut() -> None:
    """Adjust Hypothesis for the way mutmut runs this suite.

    Two settings, for two different consequences of that driver:

    `differing_executors` is the one that actually aborted the lane, and the
    traceback took three attempts to obtain because the lane ran with
    `--tb=no`. mutmut runs the whole suite several times inside one process
    (stats, then a clean baseline, then each mutant), so a `@given` method is
    invoked again with a fresh pytest instance and Hypothesis reports it as
    called "from multiple different executors". The check exists to warn that
    replay from its example database may not reproduce — a real concern for a
    developer, and not one here, where each phase is a fresh measurement and
    nothing replays across them. It is suppressed only under mutmut; an
    ordinary run still gets the warning.

    `deadline` is precautionary rather than observed: every call to a mutated
    function goes through a dispatcher, so the code is deliberately slower
    than production and a 200ms per-example limit would fail property tests
    for a reason unrelated to the property.

    Keyed on the variable's *presence*: mutmut sets `MUTANT_UNDER_TEST` to the
    empty string for the clean run and to a mutant name otherwise, so a
    truthiness check would miss exactly the phase this was seen in.
    """
    if "MUTANT_UNDER_TEST" not in os.environ:
        return
    try:
        from hypothesis import HealthCheck, settings
    except ImportError:  # hypothesis is a dev dependency, not a hard one
        return
    settings.register_profile(
        "mutmut",
        deadline=None,
        suppress_health_check=[
            HealthCheck.too_slow,
            HealthCheck.differing_executors,
        ],
    )
    settings.load_profile("mutmut")


_hypothesis_profile_for_mutmut()


@pytest.fixture(autouse=True)
def _isolate_snapshot_cache(tmp_path_factory: pytest.TempPathFactory, monkeypatch):
    """Redirect the whole-snapshot cache (``snapshot_cache.py``) to a fresh
    per-test directory instead of the real ``~/.cache/abi_check/snapshots/``.

    Without this, two tests that happen to dump byte-identical synthetic
    fixture binaries with the same (empty) headers/version/lang would collide
    on the same cache key and one could silently serve the other's cached
    snapshot — a test-isolation hazard, not a real-world one (this only
    matters because unrelated tests share one persistent on-disk cache
    directory across the whole run).
    """
    from abicheck import snapshot_cache

    # Test infrastructure intentionally supports distro CastXML builds whose
    # bundled Clang cannot parse the host headers. Keep that portability opt-in
    # explicit; dedicated fallback-policy tests remove this variable and prove
    # the production default remains fail-closed.
    monkeypatch.setenv("ABICHECK_ALLOW_AST_FALLBACK", "1")

    monkeypatch.setattr(
        snapshot_cache, "_CACHE_DIR", tmp_path_factory.mktemp("snapshot_cache")
    )


@pytest.fixture(autouse=True)
def _isolate_ast_memo() -> Iterator[None]:
    """Clear the in-process clang-AST memo slot (``dumper_cache._ast_memo_slot``,
    G31 Phase C AST reuse) before and after every test.

    Several fixture headers across ``test_dumper_clang.py`` share identical
    trivial content (e.g. ``int foo(void);``), so two unrelated tests can
    compute the same content-addressed cache key — without this, a memo
    entry left behind by one test could serve a stale/wrong result to a
    later test expecting a genuine cache miss (the same hazard
    ``_isolate_snapshot_cache`` above already guards against for the
    whole-snapshot disk cache). The pytest main thread is reused across
    tests, so a ContextVar set by one test is otherwise still visible to
    the next.
    """
    from abicheck import dumper_cache

    dumper_cache._ast_memo_slot.set(None)
    yield
    dumper_cache._ast_memo_slot.set(None)


@pytest.fixture
def source_tree_with_compile_db(tmp_path: Path) -> Path:
    """A minimal source tree with a compile_commands.json for L3/L4 scan tests.

    The compile DB makes L3 resolve cleanly (no stderr "no compile_commands.json"
    note that would otherwise prepend to JSON stdout), so an `s5` scan reaches the
    L4 replay path. Returns the source dir. Shared so multiple suites can drive a
    `scan --sources` run without re-deriving the tree (ADR-035 P3 tests).
    """
    src = tmp_path / "src"
    src.mkdir()
    (src / "foo.cpp").write_text("int foo() { return 0; }\n", encoding="utf-8")
    (src / "compile_commands.json").write_text(
        json.dumps(
            [
                {
                    "directory": str(src),
                    "file": "foo.cpp",
                    "arguments": ["c++", "-c", "foo.cpp"],
                }
            ]
        ),
        encoding="utf-8",
    )
    return src


_WIDGET_HEADER = """
#pragma once
struct Widget {
    int x;
#ifdef WIDGET_EXTRA
    int y;
#endif
};
int touch(Widget* w);
"""

_WIDGET_SOURCE = """
#include "widget.h"
int touch(Widget* w) { return w->x; }
"""


def _have_tool(tool: str) -> bool:
    return shutil.which(tool) is not None


@pytest.fixture
def widget_lib(tmp_path: Path) -> tuple[Path, Path, Path]:
    """Build a real .so (compiled *with* WIDGET_EXTRA, so its real ABI has the
    extra field) plus its public header and a compile_commands.json recording
    that same real -DWIDGET_EXTRA=1 (+ two ABI-relevant flags).

    Shared by ``tests/test_header_compile_context.py``'s P0.3
    ``@pytest.mark.integration`` end-to-end suite (real clang/g++ required),
    per AGENTS.md's guidance that a real-compiler fixture like this belongs
    in ``conftest.py``/``tests/fixtures/`` rather than inline in a test file.
    """
    if not (_have_tool("clang") and _have_tool("g++")):
        pytest.skip("clang and g++ are required for this P0.3 integration test")
    header = tmp_path / "widget.h"
    header.write_text(_WIDGET_HEADER, encoding="utf-8")
    src = tmp_path / "widget.cpp"
    src.write_text(_WIDGET_SOURCE, encoding="utf-8")
    so = tmp_path / "libwidget.so"
    subprocess.run(
        [
            "g++",
            "-shared",
            "-fPIC",
            "-fno-omit-frame-pointer",
            "-DWIDGET_EXTRA=1",
            "-o",
            str(so),
            str(src),
            f"-I{tmp_path}",
        ],
        check=True,
        capture_output=True,
    )
    (tmp_path / "compile_commands.json").write_text(
        json.dumps(
            [
                {
                    "directory": str(tmp_path),
                    "file": str(src),
                    "arguments": [
                        "g++",
                        "-c",
                        str(src),
                        "-o",
                        "widget.o",
                        "-DWIDGET_EXTRA=1",
                        "-fPIC",
                        "-fno-omit-frame-pointer",
                        "-std=c++17",
                    ],
                }
            ]
        ),
        encoding="utf-8",
    )
    return so, header, tmp_path


_C_WIDGET_HEADER = """
#pragma once
struct Widget {
    int x;
    int y;
};
int touch(struct Widget* w);
"""

_C_WIDGET_SOURCE = """
#include "widget.h"
int touch(struct Widget* w) { return w->x; }
"""


@pytest.fixture
def c_widget_lib(tmp_path: Path) -> tuple[Path, Path, Path]:
    """Build a real, plain-C .so plus its public header and a
    compile_commands.json recording a real ``gcc -std=c17`` compile of it.

    Shared by ``tests/test_header_compile_context.py``'s P0.3
    ``@pytest.mark.integration`` end-to-end suite (real clang/gcc required)
    for ``discussion_r3787398644``'s own repro shape: a matched C compile
    unit (``standard="c17"``) with the caller explicitly forcing a C++
    parse of the same header via ``DumpRequest(lang="c++",
    lang_explicit=True)``.
    """
    if not (_have_tool("clang") and _have_tool("gcc")):
        pytest.skip("clang and gcc are required for this P0.3 integration test")
    header = tmp_path / "widget.h"
    header.write_text(_C_WIDGET_HEADER, encoding="utf-8")
    src = tmp_path / "widget.c"
    src.write_text(_C_WIDGET_SOURCE, encoding="utf-8")
    so = tmp_path / "libwidget.so"
    subprocess.run(
        [
            "gcc",
            "-shared",
            "-fPIC",
            "-std=c17",
            "-o",
            str(so),
            str(src),
            f"-I{tmp_path}",
        ],
        check=True,
        capture_output=True,
    )
    (tmp_path / "compile_commands.json").write_text(
        json.dumps(
            [
                {
                    "directory": str(tmp_path),
                    "file": str(src),
                    "arguments": [
                        "gcc",
                        "-c",
                        str(src),
                        "-o",
                        "widget.o",
                        "-fPIC",
                        "-std=c17",
                    ],
                }
            ]
        ),
        encoding="utf-8",
    )
    return so, header, tmp_path


def pytest_addoption(parser: pytest.Parser) -> None:
    """Register --update-goldens CLI option for golden-output tests."""
    parser.addoption(
        "--update-goldens",
        action="store_true",
        default=False,
        help="Re-generate golden output files in tests/golden/ instead of comparing.",
    )


def _materialize_generated_skill_trees() -> None:
    """Regenerate `.agents/skills/`, `.claude/skills/`, `.gemini/skills/` from
    `skills-src/` before the suite runs.

    Those three trees are build output (2026-08-21 ADR-058 amendment — see
    `scripts/gen_agent_skills.py`'s module docstring), no longer committed,
    but several tests (`test_skill_eval_pack.py`, `test_ai_readiness.py`,
    `test_gen_harbor_tasks.py`, ...) read them directly off disk at runtime
    as the real installed artifact a consumer would see, not through a
    render-to-tempdir fixture of their own. A clean checkout has none of
    them, so without this hook every one of those tests fails on a plain
    `pytest tests/` — not just in whichever CI job happens to run
    `gen_agent_skills.py --check` first. Deterministic and reruns cheaply
    (see that script's docstring: pure-Python, sub-second, no network), so
    doing it unconditionally here rather than only when the trees are
    missing keeps them from silently drifting stale mid-session too.

    Under pytest-xdist every worker calls `pytest_configure` independently;
    a file lock keyed on skills-src's own module serializes them so no two
    workers race `write_trees()`'s own rm-then-rewrite against each other.
    """
    scripts_dir = Path(__file__).resolve().parent.parent / "scripts"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    try:
        import gen_agent_skills as gen
    except Exception:  # pragma: no cover - defensive; let tests surface the real error
        return

    lock_path = scripts_dir.parent / ".pytest_cache" / "gen_agent_skills.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    def _write() -> None:
        try:
            rendered = gen.render_all()
        except gen.SkillGenerationError:
            return  # a skills-src authoring error; let the real gen/AI-readiness checks report it
        gen.write_trees(rendered)

    if filelock is not None:
        try:
            with filelock.FileLock(str(lock_path), timeout=120):
                _write()
        except filelock.Timeout:
            pass  # another worker is already materializing; proceed, it'll be done shortly
    else:
        _write()


def pytest_configure(config: pytest.Config) -> None:
    _materialize_generated_skill_trees()
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
            return (
                "gcc (MinGW) not found in PATH (required for Windows integration tests)"
            )
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

# Optional per-phase duration capture for test-time tracking. When
# ``ABICHECK_DURATIONS_JSON`` is set, every (nodeid, phase, duration) tuple is
# collected and written out at session end (see pytest_sessionfinish). Under
# xdist the controller receives every worker's forwarded reports, so it alone
# holds the complete picture — workers don't write. Zero cost when the env var
# is unset.
_PHASE_DURATIONS: list[dict[str, object]] = []


def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    """Count tests that actually executed, and (optionally) record durations."""
    global _EXECUTED_TESTS
    if report.when == "call" and report.outcome in ("passed", "failed"):
        _EXECUTED_TESTS += 1
    if os.environ.get("ABICHECK_DURATIONS_JSON"):
        _PHASE_DURATIONS.append(
            {
                "nodeid": report.nodeid,
                "when": report.when,
                "duration": float(report.duration),
            }
        )


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """Fail the session if fewer tests ran than ``ABICHECK_MIN_EXECUTED`` demands.

    Skipped on xdist workers (the controller aggregates every worker's reports
    and is the one that owns the final exit status).
    """
    is_worker = hasattr(session.config, "workerinput")

    # Test-time tracking: write the captured per-phase durations once, from the
    # controller (it has aggregated every worker's reports under xdist). The CI
    # job renders the slowest entries into the run summary and uploads the file
    # as an artifact for trend tracking.
    durations_path = os.environ.get("ABICHECK_DURATIONS_JSON")
    if durations_path and not is_worker:
        try:
            Path(durations_path).write_text(
                json.dumps(_PHASE_DURATIONS), encoding="utf-8"
            )
        except OSError:
            pass

    if is_worker:
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
    # The CMake project root is catalog/ (catalog/CMakeLists.txt globs
    # cases/case*), not examples/ -- Phase 4 of the examples/catalog split.
    catalog_dir = Path(__file__).parent.parent / "catalog"
    cmake = shutil.which("cmake")
    if not cmake:
        return False
    try:
        r = subprocess.run(
            [
                cmake,
                "-S",
                str(catalog_dir),
                "-B",
                str(build_dir),
                "-DCMAKE_BUILD_TYPE=Debug",
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
    except subprocess.TimeoutExpired:
        return False
    return r.returncode == 0


@pytest.fixture(scope="session")
def shared_cmake_build_dir(tmp_path_factory: pytest.TempPathFactory) -> Path | None:
    """Session-scoped CMake build directory for integration tests.

    Configures the catalog/ CMakeLists.txt **once** per session so that
    individual tests only need to run ``cmake --build`` for their specific
    targets.  On Windows this avoids ~30 redundant cmake-configure passes
    (each one re-parses all 63 example CMakeLists).

    When running under pytest-xdist, a file lock ensures only the first
    worker runs the expensive cmake configure; other workers wait and
    reuse the same build directory.
    """
    catalog_dir = Path(__file__).parent.parent / "catalog"
    cmake_lists = catalog_dir / "CMakeLists.txt"
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


@pytest.fixture
def compile_db(tmp_path: Path) -> Path:
    """A minimal ``compile_commands.json`` supplying L3 build metadata (no compiler).

    Shared across scan tests: lets a pinned deep level collect L3 so auto-strict
    (ADR-037 D5 — a pinned depth with no source input is an error) does not fire in
    tests whose intent is level reporting / seed resolution, not collection.
    """
    src = tmp_path / "u.c"
    src.write_text("int u(void){return 0;}\n", encoding="utf-8")
    cdb = tmp_path / "compile_commands.json"
    cdb.write_text(
        json.dumps(
            [{"directory": str(tmp_path), "file": str(src), "command": "cc -c u.c"}]
        ),
        encoding="utf-8",
    )
    return cdb


# A real CMake C++ library (source tree + compiled .so), shared by the scan-level
# integration suite — kept here per the "fixtures live in conftest.py" convention
# so future source-scan integration tests can reuse it. Builds once per session;
# only instantiated when a test requests it (no cost to other tests).
_CMAKE_CXX_FOO_H = """\
#ifndef FOO_H
#define FOO_H
namespace foo {
class Widget {
public:
  Widget();
  int value() const;
  void set_value(int v);
private:
  int v_;
};
int add(int a, int b);
}
#endif
"""

_CMAKE_CXX_FOO_CPP = """\
#include "foo.h"
namespace foo {
Widget::Widget() : v_(0) {}
int Widget::value() const { return v_; }
void Widget::set_value(int v) { v_ = v; }
int add(int a, int b) { return a + b; }
}
"""

_CMAKE_CXX_CMAKELISTS = """\
cmake_minimum_required(VERSION 3.10)
project(foo CXX)
add_library(foo SHARED foo.cpp)
target_include_directories(foo PUBLIC ${CMAKE_CURRENT_SOURCE_DIR}/include)
set_target_properties(foo PROPERTIES VERSION 1.0.0 SOVERSION 1)
"""


@pytest.fixture(scope="session")
def cmake_cxx_project(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A real CMake C++ library: source tree (CMakeLists + public header) plus a
    compiled ELF ``.so``. The compile DB is intentionally *absent* so scans over it
    exercise the zero-config cmake inference path. Requires gcc/g++."""
    root = tmp_path_factory.mktemp("cmake_cxx_project")
    (root / "include").mkdir()
    (root / "include" / "foo.h").write_text(_CMAKE_CXX_FOO_H, encoding="utf-8")
    (root / "foo.cpp").write_text(_CMAKE_CXX_FOO_CPP, encoding="utf-8")
    (root / "CMakeLists.txt").write_text(_CMAKE_CXX_CMAKELISTS, encoding="utf-8")
    so = root / "libfoo.so.1.0.0"
    subprocess.run(
        [
            "g++",
            "-shared",
            "-fPIC",
            f"-I{root / 'include'}",
            "-o",
            str(so),
            str(root / "foo.cpp"),
        ],
        check=True,
        capture_output=True,
    )
    (root / "libfoo.so").symlink_to(so.name)
    return root
