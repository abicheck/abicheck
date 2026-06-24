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
*analysis* command itself** — ``cmake`` configure (emits ``compile_commands.json``)
or ``bazel aquery`` (action graph). Both are designed to *analyse* the build
without compiling it.

**Make is detected but never auto-run.** Unlike cmake configure / bazel aquery,
``make -n`` is not reliably side-effect-free: GNU make still executes recipe
lines prefixed with ``+`` or invoking ``$(MAKE)`` in dry-run mode, so
auto-running it on an untrusted checkout could execute arbitrary commands
(Codex P1). A Make project must instead supply a compile DB (e.g. ``bear --
make`` → ``--compile-db``) or a pre-collected Make transcript pack via
``--build-info`` — note the inline ``build.query`` path only ingests an emitted
``compile_commands.json``, so it cannot turn a bare ``make -n`` transcript into
L3 evidence.

Security boundary (the ADR-032 D5 intent, refined): the command run here is
**constructed by abicheck**, never taken from a tree-local ``.abicheck.yml`` —
so a malicious checkout cannot inject an arbitrary command through auto-discovery
(an arbitrary ``build.query`` string still requires an explicit, operator-trusted
``--config``). The residual trust is inherent to *analysing* a build at all:
``cmake`` configure and ``bazel`` loading still evaluate the project's own build
scripts. If you pointed abicheck at a source tree to analyse it from source, you
already trust it enough to configure it. Pre-built artifact scanning (``compare``
on two ``.so`` files) never reaches this path.

Detection + command construction are pure (unit-testable without a toolchain);
only :func:`run_inferred_build_query` touches the filesystem / subprocess.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path

from .build_evidence import BuildEvidence
from .model import ExtractorRecord

#: Out-of-source build dir name. abicheck no longer writes a configure tree into
#: the ``--sources`` checkout (it uses an out-of-tree temp dir, see
#: :func:`run_inferred_build_query`); this name is retained only so the tree
#: walkers still prune a stray ``.abicheck-build`` left by an older abicheck
#: version or a user convention.
ABICHECK_BUILD_DIR = ".abicheck-build"

#: Directory segments pruned from the tree walkers (``-H`` header-directory
#: globbing and the S2 lexical pre-scan): VCS metadata plus ``ABICHECK_BUILD_DIR``.
#: Inferred cmake now configures out-of-tree, so abicheck no longer creates an
#: in-tree build dir — but pruning the name is kept as cheap defence against a
#: stray ``.abicheck-build`` from an older abicheck version or a user convention,
#: whose generated headers (config.h / version.h) would otherwise inflate the L2
#: surface. Single source of truth shared by ``service_scan.expand_header_inputs``,
#: ``cli_resolve._expand_header_inputs``, and ``pattern_scan``.
PRUNED_HEADER_DIR_SEGMENTS: frozenset[str] = frozenset(
    {".git", ".hg", ".svn", ABICHECK_BUILD_DIR}
)

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


def inferred_query_command(
    system: str, sources: Path, build_dir: Path | None = None
) -> list[str] | None:
    """The fixed, abicheck-authored query command for *system* (no user input).

    Returns ``None`` for an unknown system. The argv is never shell-interpreted
    and contains no value taken from the source tree beyond its path. *build_dir*
    is the cmake configure output (``-B``); :func:`run_inferred_build_query`
    passes an out-of-tree temp dir so nothing is written under *sources*. Falls
    back to ``sources / ABICHECK_BUILD_DIR`` only when called standalone without
    one.
    """
    if system == "cmake":
        out = build_dir if build_dir is not None else sources / ABICHECK_BUILD_DIR
        return [
            "cmake",
            "-S",
            str(sources),
            "-B",
            str(out),
            "-DCMAKE_EXPORT_COMPILE_COMMANDS=ON",
        ]
    if system == "make":
        # No auto-run for Make: `make -n` is NOT reliably side-effect-free — GNU
        # make still executes recipe lines prefixed with `+` or invoking
        # `$(MAKE)` even in dry-run mode, so auto-running it on an untrusted
        # checkout could execute arbitrary commands (Codex P1). Make projects must
        # opt in explicitly via `--build-query "make -n …"` (operator-trusted) or
        # a pre-captured transcript. cmake configure / bazel aquery are analysis
        # commands designed not to build, so they stay auto.
        return None
    if system == "bazel":
        # Action graph for the compile AND link/archive actions the BazelAdapter
        # ingests — link actions carry version_script/soname facts, so a
        # compile-only query would drop LINK_EXPORT_POLICY_CHANGED on the inferred
        # path (review). The mnemonic regex is derived from the adapter's own
        # mnemonic sets so the two cannot drift; anchored alternation so a
        # mnemonic is matched whole, not as a substring. --include_param_files
        # expands @...params so source paths and ABI flags Bazel spills to param
        # files are present (mirrors BazelAdapter).
        from .adapters.bazel import _COMPILE_MNEMONICS, _LINK_MNEMONICS

        mnemonics = "|".join(sorted(_COMPILE_MNEMONICS | _LINK_MNEMONICS))
        return [
            "bazel",
            "aquery",
            "--output=jsonproto",
            "--include_param_files",
            f"mnemonic('^({mnemonics})$', deps(//...))",
        ]
    return None


def run_inferred_build_query(
    sources: Path | None,
    merged: BuildEvidence,
    extractors: list[ExtractorRecord],
    *,
    timeout: float = INFERRED_QUERY_TIMEOUT_S,
    which: Callable[[str], str | None] = shutil.which,
    cleanup: list[Path] | None = None,
) -> Path | None:
    """Detect the build system and run abicheck's own query to produce L3.

    Always returns ``None``: the cmake / bazel evidence is ingested and merged
    directly into *merged* (cmake configures into an out-of-tree temp dir whose
    ``compile_commands.json`` is parsed), and a diagnostic ``ExtractorRecord`` is
    appended for every outcome (ok / partial / skipped / failed). Never raises: a
    missing tool, non-zero exit, timeout, or unparseable output degrades to a
    diagnostic so the scan continues with whatever evidence is available.

    The cmake temp build dir must outlive L4 replay (clang runs with each compile
    unit's ``directory`` — the build dir — as cwd), so when *cleanup* is given the
    dir is appended to it for the caller to remove *after* replay; only when
    *cleanup* is ``None`` (standalone/unit-test use) is it removed immediately.
    """
    system = detect_build_system(sources)
    if not system or sources is None:
        return None
    # Resolve to an absolute path first: the query runs with cwd=sources, so a
    # relative `--sources src` would otherwise make `cmake -S src` resolve to
    # `src/src`, and would anchor make/bazel relative paths to the process cwd
    # instead of the tree (Codex review). Absolute paths are cwd-independent.
    sources = sources.resolve()
    if system == "make":
        # Detected but deliberately not auto-run (see inferred_query_command).
        extractors.append(
            ExtractorRecord(
                name="build_query_auto",
                status="skipped",
                detail=(
                    "detected a Make project, but `make -n` is not reliably "
                    "side-effect-free so it is not auto-run; provide a compile DB "
                    "(e.g. `bear -- make`, then --compile-db compile_commands.json) "
                    "or a pre-collected Make transcript pack via --build-info"
                ),
            )
        )
        return None
    # cmake configures into an OUT-OF-TREE temp dir: never mutate the --sources
    # checkout (it may be read-only / shared) and keep generated output away from
    # every tree walker, so no walker needs to prune an in-tree build dir
    # (maintainer decision). cmake writes absolute source/-I paths into
    # compile_commands.json, so an out-of-tree build dir resolves fine; the dir is
    # removed in the finally once its compile DB has been ingested.
    build_dir = (
        Path(tempfile.mkdtemp(prefix="abicheck-cmake-")) if system == "cmake" else None
    )
    try:
        cmd = inferred_query_command(system, sources, build_dir=build_dir)
        if (
            cmd is None
        ):  # pragma: no cover - defensive: detection only yields cmake/bazel here
            return None
        # Bazelisk is the common launcher when `bazel` isn't on PATH; mirror the
        # BazelAdapter's fallback so inferred Bazel queries still run (Codex/CR).
        if (
            cmd[0] == "bazel"
            and which("bazel") is None
            and which("bazelisk") is not None
        ):
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
            merged.diagnostics.append(
                f"build_query_auto: {system} exited {proc.returncode}"
            )
            return None
        # Ingestion parses tool output (the cmake compile DB, bazel aquery JSON) —
        # keep it inside the never-raises contract so a malformed payload or a
        # transient I/O error degrades to a diagnostic rather than aborting a
        # `dump --sources` run (review).
        try:
            return _ingest_query_output(
                system, sources, proc.stdout, merged, extractors, build_dir=build_dir
            )
        except (OSError, ValueError, KeyError, TypeError) as exc:
            extractors.append(
                ExtractorRecord(
                    name="build_query_auto",
                    status="failed",
                    detail=f"auto {system} query ran but its output could not be ingested: {exc}",
                )
            )
            merged.diagnostics.append(f"build_query_auto: ingest failed ({exc})")
            return None
    finally:
        if build_dir is not None:
            if cleanup is not None:
                # Defer removal: L4 replay still needs this dir as clang's cwd.
                cleanup.append(build_dir)
            else:
                shutil.rmtree(build_dir, ignore_errors=True)


def _ingest_query_output(
    system: str,
    sources: Path,
    stdout: str,
    merged: BuildEvidence,
    extractors: list[ExtractorRecord],
    build_dir: Path | None = None,
) -> Path | None:
    """Parse a successful query's output and merge it into *merged* (returns None).

    For cmake the compile DB is read from *build_dir* (the out-of-tree configure
    dir) and ingested via :class:`CompileDbAdapter`; for bazel the aquery JSON is
    parsed via :class:`BazelAdapter`. Both merge into *merged* and return ``None``
    (the caller no longer threads a compile-DB path).
    """
    if system == "cmake":
        db = (build_dir or (sources / ABICHECK_BUILD_DIR)) / "compile_commands.json"
        if not db.is_file():
            extractors.append(
                ExtractorRecord(
                    name="build_query_auto",
                    status="partial",
                    detail="cmake configure ran but produced no compile_commands.json",
                )
            )
            return None
        from .adapters.compile_db import CompileDbAdapter

        # cmake writes absolute source/-I paths, so the out-of-tree build dir is
        # fine; ingest here (the build dir is removed by the caller's finally).
        ev = CompileDbAdapter(db, build_system="cmake").collect()
        merged.merge(ev)
        extractors.append(
            ExtractorRecord(
                name="build_query_auto",
                status="ok" if ev.compile_units else "partial",
                detail=(
                    f"auto-ran `cmake` configure; {len(ev.compile_units)} compile unit(s)"
                    if ev.compile_units
                    else "cmake configure produced an empty compile_commands.json"
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
    return None  # pragma: no cover - defensive: only cmake/bazel reach ingestion
