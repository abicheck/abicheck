# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Zero-config build-system inference for ``--sources`` (ADR-032 amendment).

The original design gated *all* build-system queries behind an explicit
``--allow-build-query`` flag plus a trusted ``.abicheck.yml`` ``build.query``
command. That made the common case ("just point me at the sources and figure out
the build") impossible without manual setup. This module restores the
zero-config promise: when a ``--sources`` tree is given and no compile DB already
exists, abicheck **detects the build system and runs a fixed, abicheck-authored
query command itself** — ``cmake`` configure (emits ``compile_commands.json``),
``make -n`` (dry-run transcript), or ``bazel aquery`` (action graph).

Security boundary (the ADR-032 D5 intent, refined): the command run here is
**constructed by abicheck**, never taken from a tree-local ``.abicheck.yml`` —
so a malicious checkout cannot inject an arbitrary command through auto-discovery
(an arbitrary ``build.query`` string still requires an explicit, operator-trusted
``--config``). The residual trust is inherent to building from source at all:
running a project's own ``cmake``/``make``/``bazel`` executes that project's
build scripts. If you pointed abicheck at a source tree to analyse it from
source, you already trust it enough to configure it. Pre-built artifact scanning
(``compare`` on two ``.so`` files) never reaches this path.

Detection + command construction are pure (unit-testable without a toolchain);
only :func:`run_inferred_build_query` touches the filesystem / subprocess.
"""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

from .build_evidence import BuildEvidence
from .model import ExtractorRecord
from .redaction import DEFAULT_REDACTION

#: Out-of-source build dir abicheck creates for a cmake configure (kept inside
#: the tree so a second run reuses it / auto-discovery finds it next time).
ABICHECK_BUILD_DIR = ".abicheck-build"

#: Wall-clock ceiling for an inferred query. A configure/dry-run/aquery is far
#: cheaper than a full build, but cmake configure of a large project (oneDNN,
#: hundreds of TUs) can take a minute, so this is more generous than a flag query.
INFERRED_QUERY_TIMEOUT_S = 600

#: Build-system marker files, checked most-specific first. CMake wins over Make
#: when both are present (a CMake project often ships a convenience Makefile that
#: just drives cmake), and Bazel's module/workspace markers are unambiguous.
_MARKERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("cmake", ("CMakeLists.txt",)),
    ("bazel", ("MODULE.bazel", "WORKSPACE.bazel", "WORKSPACE")),
    ("make", ("GNUmakefile", "makefile", "Makefile")),
)


def detect_build_system(sources: Path | None) -> str:
    """Return ``"cmake"`` / ``"bazel"`` / ``"make"`` for *sources*, else ``""``.

    Pure: inspects only the presence of marker files at the tree root.
    """
    if sources is None or not sources.is_dir():
        return ""
    for system, markers in _MARKERS:
        if any((sources / m).is_file() for m in markers):
            return system
    return ""


def inferred_query_command(system: str, sources: Path) -> list[str] | None:
    """The fixed, abicheck-authored query command for *system* (no user input).

    Returns ``None`` for an unknown system. The argv is never shell-interpreted
    and contains no value taken from the source tree beyond its path.
    """
    if system == "cmake":
        return [
            "cmake",
            "-S", str(sources),
            "-B", str(sources / ABICHECK_BUILD_DIR),
            "-DCMAKE_EXPORT_COMPILE_COMMANDS=ON",
        ]
    if system == "make":
        # Dry run only — never actually compiles. --always-make forces every
        # recipe to be printed so the transcript is complete on an up-to-date tree.
        return ["make", "-n", "--always-make"]
    if system == "bazel":
        # Action graph for all C++ compile actions; jsonproto feeds the adapter.
        # --include_param_files expands @...params so source paths and ABI flags
        # that Bazel spills to param files are present (mirrors BazelAdapter).
        return [
            "bazel", "aquery", "--output=jsonproto", "--include_param_files",
            "mnemonic(CppCompile, deps(//...))",
        ]
    return None


def run_inferred_build_query(
    sources: Path | None,
    merged: BuildEvidence,
    extractors: list[ExtractorRecord],
    *,
    timeout: float = INFERRED_QUERY_TIMEOUT_S,
    which: Callable[[str], str | None] = shutil.which,
) -> Path | None:
    """Detect the build system and run abicheck's own query to produce L3.

    Returns a ``compile_commands.json`` path (the cmake case, fed to the compile
    DB adapter by the caller) or ``None`` after merging :class:`BuildEvidence`
    directly into *merged* (the make/bazel adapter cases) — or ``None`` with a
    diagnostic ``ExtractorRecord`` when detection/execution produces nothing.
    Never raises: a missing tool, non-zero exit, or timeout degrades to a coverage
    diagnostic so the scan continues with whatever evidence is available.
    """
    system = detect_build_system(sources)
    if not system or sources is None:
        return None
    # Resolve to an absolute path first: the query runs with cwd=sources, so a
    # relative `--sources src` would otherwise make `cmake -S src` resolve to
    # `src/src`, and would anchor make/bazel relative paths to the process cwd
    # instead of the tree (Codex review). Absolute paths are cwd-independent.
    sources = sources.resolve()
    cmd = inferred_query_command(system, sources)
    if cmd is None:
        return None
    # Bazelisk is the common launcher when `bazel` itself isn't on PATH; mirror
    # the BazelAdapter's fallback so inferred Bazel queries still run (Codex/CR).
    if cmd[0] == "bazel" and which("bazel") is None and which("bazelisk") is not None:
        cmd[0] = "bazelisk"
    tool = cmd[0]
    if which(tool) is None:
        extractors.append(
            ExtractorRecord(
                name="build_query_auto",
                status="skipped",
                detail=(
                    f"detected a {system} project but `{tool}` is not installed; "
                    "install it or pass --build-info / --compile-db"
                ),
            )
        )
        return None
    try:
        proc = subprocess.run(  # noqa: S603 - fixed abicheck-authored argv, shell=False
            cmd,
            cwd=str(sources),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        extractors.append(
            ExtractorRecord(
                name="build_query_auto",
                status="failed",
                detail=f"auto {system} query failed to run ({tool}): {exc}",
            )
        )
        merged.diagnostics.append(f"build_query_auto: {exc}")
        return None
    if proc.returncode != 0:
        extractors.append(
            ExtractorRecord(
                name="build_query_auto",
                status="failed",
                detail=(
                    f"auto {system} query exited {proc.returncode}: "
                    f"{(proc.stderr or '').strip()[:200]}"
                ),
            )
        )
        merged.diagnostics.append(f"build_query_auto: {system} exited {proc.returncode}")
        return None
    return _ingest_query_output(system, sources, proc.stdout, merged, extractors)


def _ingest_query_output(
    system: str,
    sources: Path,
    stdout: str,
    merged: BuildEvidence,
    extractors: list[ExtractorRecord],
) -> Path | None:
    """Turn a successful query's output into a compile DB path or merged evidence."""
    if system == "cmake":
        db = sources / ABICHECK_BUILD_DIR / "compile_commands.json"
        if db.is_file():
            extractors.append(
                ExtractorRecord(
                    name="build_query_auto",
                    status="ok",
                    detail=(
                        "auto-ran `cmake` configure; compile DB at "
                        f"{DEFAULT_REDACTION.path(str(db))}"
                    ),
                )
            )
            return db
        extractors.append(
            ExtractorRecord(
                name="build_query_auto",
                status="partial",
                detail="cmake configure ran but produced no compile_commands.json",
            )
        )
        return None
    if system == "make":
        from .adapters.make import MakeAdapter

        # build_dir=sources anchors the transcript's relative compile commands
        # (e.g. `cc -Iinclude -c src/foo.c`) to the tree, not the process cwd.
        ev = MakeAdapter(dry_run=stdout, build_dir=sources).collect()
        merged.merge(ev)
        extractors.append(
            ExtractorRecord(
                name="build_query_auto",
                status="ok" if ev.compile_units else "partial",
                detail=(
                    f"auto-ran `make -n`; {len(ev.compile_units)} compile unit(s) "
                    "(reduced confidence — make dry-run transcript)"
                ),
            )
        )
        return None
    if system == "bazel":
        import tempfile

        from .adapters.bazel import BazelAdapter

        with tempfile.NamedTemporaryFile(
            "w", suffix=".aquery.json", delete=False
        ) as tf:
            tf.write(stdout)
            aq = Path(tf.name)
        try:
            # workspace=sources anchors the aquery's relative source/include
            # paths to the tree so source matching + L4 replay resolve.
            ev = BazelAdapter(aquery=aq, workspace=sources, allow_query=False).collect()
        finally:
            aq.unlink(missing_ok=True)
        merged.merge(ev)
        extractors.append(
            ExtractorRecord(
                name="build_query_auto",
                status="ok" if ev.compile_units else "partial",
                detail=f"auto-ran `bazel aquery`; {len(ev.compile_units)} compile unit(s)",
            )
        )
        return None
    return None
