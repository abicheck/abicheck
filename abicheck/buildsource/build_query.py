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

**Make fallback.** Make projects do not normally emit ``compile_commands.json``.
For zero-config source scans, abicheck now runs a fixed GNU Make dry-run query
(``make -B -n -k``) and scrapes the transcript through the reduced-confidence
Make adapter. This is less authoritative than a real compile DB and can still
execute recursive/``+`` recipes on some Makefiles, but it keeps Make/EPICS-style
projects useful by default.

Security boundary (the ADR-032 D5 intent, refined): the command run here is
**constructed by abicheck**, never taken from a tree-local ``.abicheck.yml`` —
so a malicious checkout cannot inject an arbitrary command through auto-discovery
(an arbitrary ``build.query`` string still requires an explicit, operator-trusted
``--config``). The residual trust is inherent to *analysing* a build at all:
``cmake`` configure, ``bazel`` loading, and Make dry-run still evaluate the
project's own build scripts. If you pointed abicheck at a source tree to analyse
it from source, you already trust it enough to query it. Pre-built artifact
scanning (``compare`` on two ``.so`` files) never reaches this path.

Detection + command construction are pure (unit-testable without a toolchain);
only :func:`run_inferred_build_query` touches the filesystem / subprocess.
"""

from __future__ import annotations

import contextlib
import functools
import hashlib
import os
import shutil
import stat as _stat
import subprocess
import tempfile
import time
from collections.abc import Callable, Iterable
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

#: Poll interval while waiting on the inferred cmake build-dir lock (seconds).
_BUILD_DIR_LOCK_POLL_S = 0.2


def _private_tmp_root() -> Path | None:
    """A per-user, owner-only (``0700``) temp subdir, created/validated securely.

    The inferred cmake build dir lives at a *deterministic* (predictable) path so
    its compile-unit ``directory`` / ``-I`` strings stay stable across runs. On a
    world-shared ``/tmp`` that predictability is a symlink-attack vector: another
    local user could pre-create ``/tmp/abicheck-cmake-<hash>`` as a symlink to a
    victim-writable dir, and ``mkdir(..., exist_ok=True)`` would follow it so
    ``cmake -B`` writes there (Codex P2). Nesting the build dir inside a ``0700``
    dir owned by the current user closes that: only the owner can create entries
    inside it, so no other user can plant the deterministic path or its lock file.

    Returns the validated root, or ``None`` if a safe one cannot be established
    (the caller then refuses to run the inferred query rather than use a
    predictable shared path). On platforms without POSIX uids/perms (Windows) the
    system temp dir is already per-user, so it is returned as-is.
    """
    tmp = Path(tempfile.gettempdir())
    try:
        uid = os.getuid()
    except AttributeError:
        # Windows: %LOCALAPPDATA%\Temp is already per-user — the shared-/tmp
        # symlink attack does not apply.
        return tmp
    root = tmp / f"abicheck-{uid}"
    try:
        try:
            os.mkdir(root, 0o700)
        except FileExistsError:
            pass
        st = os.lstat(root)  # lstat: a symlink fails the S_ISDIR check below
        if (
            not _stat.S_ISDIR(st.st_mode)  # real directory, not a symlink/file
            or st.st_uid != uid  # owned by us
            or (st.st_mode & 0o077)  # no group/other access
        ):
            return None
        return root
    except OSError:
        return None


def _inferred_cmake_build_base(sources: Path) -> Path | None:
    """Deterministic, owner-private base path for the inferred cmake build dir.

    ``None`` when no secure per-user temp root can be established (see
    :func:`_private_tmp_root`). *sources* must already be resolved.
    """
    root = _private_tmp_root()
    if root is None:
        return None
    return root / f"cmake-{hashlib.sha256(str(sources).encode()).hexdigest()[:16]}"


def _noop_release() -> None:
    """Release thunk for the unique-dir fallback (nothing to unlock)."""


def _claim_inferred_build_dir(
    base: Path, timeout: float
) -> tuple[Path, Callable[[], None]]:
    """Take exclusive ownership of the deterministic cmake build dir for this run.

    *base* is deterministic per resolved source tree, which keeps each compile
    unit's ``directory`` / generated-header ``-I`` strings — and thus the L4 replay
    cache key — identical across *sequential* scans. But two *concurrent* scans of
    the same checkout would otherwise configure into and tear down one shared
    mutable tree: parallel ``cmake -B`` runs corrupt each other's
    ``compile_commands.json``, and whichever finishes first ``rmtree``\\ s the dir
    out from under the other's clang replay cwd before L4 finishes (Codex P2).

    Two cross-platform locking strategies serialize them without sacrificing cache
    stability — the second scan ends up reusing the *same* deterministic path:

    * **POSIX** — an advisory ``flock`` held for the dir's whole lifetime. The
      second scan waits (up to *timeout*) for the first to fully finish, then takes
      it. ``flock`` is released by the OS if the holder dies, so a crash never
      leaves a stale lock. If the wait exceeds *timeout* (a peer mid-replay on a
      large tree), fall back to a unique sibling dir rather than block forever.
    * **No ``fcntl`` (e.g. Windows)** — an ``O_CREAT|O_EXCL`` marker file claim:
      the first scan creates it, removes it on cleanup, so *sequential* scans
      re-claim the same path (cache stability preserved). A concurrent scan sees
      the marker and falls back to a unique sibling dir. A crashed scan can leave a
      stale marker → later scans fall back until temp is cleared — a one-off cache
      miss, never corruption.

    Returns ``(build_dir, release)``: *release* unlocks/unlinks the claim (a no-op
    for the unique-dir fallback). The caller removes *build_dir* and then calls
    *release*.
    """
    lock_path = base.with_name(base.name + ".lock")
    try:
        import fcntl
    except ImportError:
        fcntl = None  # type: ignore[assignment]

    if fcntl is not None:
        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        waited = 0.0
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                if waited >= timeout:
                    os.close(fd)
                    # Peer still holds it — don't block the scan indefinitely.
                    return Path(
                        tempfile.mkdtemp(prefix=f"{base.name}-", dir=base.parent)
                    ), _noop_release
                time.sleep(_BUILD_DIR_LOCK_POLL_S)
                waited += _BUILD_DIR_LOCK_POLL_S
                continue

            def _unlock(_fd: int = fd) -> None:
                try:
                    fcntl.flock(_fd, fcntl.LOCK_UN)
                finally:
                    try:
                        os.close(_fd)
                    except OSError:
                        pass

            # The `.lock` file is left behind (tiny, reused next run); unlinking it
            # would race a concurrent waiter that already has it open.
            return base, _unlock

    # No advisory locking: exclusive-create marker claim.
    try:
        fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
    except FileExistsError:
        return Path(
            tempfile.mkdtemp(prefix=f"{base.name}-", dir=base.parent)
        ), _noop_release

    def _unlink(_fd: int = fd, _marker: Path = lock_path) -> None:
        try:
            os.close(_fd)
        finally:
            try:
                os.unlink(_marker)  # let the next sequential scan re-claim base
            except OSError:
                pass

    return base, _unlink


def _release_inferred_build_dir(build_dir: Path, release: Callable[[], None]) -> None:
    """Remove the inferred cmake build dir, then release its claim."""
    shutil.rmtree(build_dir, ignore_errors=True)
    release()


def drain_build_dir_cleanups(cleanups: Iterable[Callable[[], None]]) -> None:
    """Run every inferred-build-dir cleanup thunk, best-effort.

    Each thunk removes a temp cmake build dir and releases its lock. A thunk *can*
    still raise (e.g. ``flock(LOCK_UN)`` on a churned fd), so each call is wrapped in
    ``suppress`` — one failing thunk must not abort the remaining cleanups (which
    would leak the other dirs/locks) nor, when run from a caller's ``finally``,
    replace an in-flight exception with a stray ``OSError``. The single drain site
    shared by ``inline.collect_inline_pack``, ``cli_scan`` and ``service_scan``.
    """
    for cleanup in cleanups:
        with contextlib.suppress(Exception):
            cleanup()


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
    system: str,
    sources: Path,
    build_dir: Path | None = None,
    *,
    make_launcher: str = "make",
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
        # Force recipes to print even when the tree is already built.  Keep going
        # after dry-run errors so a partial transcript can still yield L3 facts.
        return [make_launcher, "-B", "-n", "-k"]
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


def _is_gnu_make_launcher(tool: str) -> bool:
    """Return whether *tool* appears to be GNU Make.

    The transcript scraper expects GNU Make's dry-run semantics and directory
    messages. BSD/non-GNU make implementations can accept different flags or
    print different transcript forms, so skip them cleanly.
    """
    try:
        proc = subprocess.run(  # noqa: S603 - fixed tool plus --version, shell=False
            [tool, "--version"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return proc.returncode == 0 and "GNU Make" in (proc.stdout or "")


def _select_gnu_make_launcher(which: Callable[[str], str | None]) -> str | None:
    """Pick a GNU Make launcher, preferring ``gmake`` when present."""
    for tool in ("gmake", "make"):
        if which(tool) is not None and _is_gnu_make_launcher(tool):
            return tool
    return None


def run_inferred_build_query(
    sources: Path | None,
    merged: BuildEvidence,
    extractors: list[ExtractorRecord],
    *,
    timeout: float = INFERRED_QUERY_TIMEOUT_S,
    which: Callable[[str], str | None] = shutil.which,
    cleanup: list[Callable[[], None]] | None = None,
) -> Path | None:
    """Detect the build system and run abicheck's own query to produce L3.

    Always returns ``None``: the cmake / bazel evidence is ingested and merged
    directly into *merged* (cmake configures into an out-of-tree temp dir whose
    ``compile_commands.json`` is parsed), and a diagnostic ``ExtractorRecord`` is
    appended for every outcome (ok / partial / skipped / failed). Never raises: a
    missing tool, non-zero exit, timeout, or unparseable output degrades to a
    diagnostic so the scan continues with whatever evidence is available.

    The cmake temp build dir must outlive L4 replay (clang runs with each compile
    unit's ``directory`` — the build dir — as cwd), so when *cleanup* is given a
    removal+unlock thunk is appended to it for the caller to invoke *after* replay;
    only when *cleanup* is ``None`` (standalone/unit-test use) is the dir removed
    and its lock released immediately. The lock (see
    :func:`_claim_inferred_build_dir`) is held for that whole lifetime.
    """
    system = detect_build_system(sources)
    if not system or sources is None:
        return None
    # Resolve to an absolute path first: the query runs with cwd=sources, so a
    # relative `--sources src` would otherwise make `cmake -S src` resolve to
    # `src/src`, and would anchor make/bazel relative paths to the process cwd
    # instead of the tree (Codex review). Absolute paths are cwd-independent.
    sources = sources.resolve()
    make_launcher = "make"
    if system == "make":
        selected = _select_gnu_make_launcher(which)
        if selected is None:
            extractors.append(
                ExtractorRecord(
                    name="build_query_auto",
                    status="skipped",
                    detail=(
                        "detected a Make project but no GNU Make launcher "
                        "(`gmake` or GNU `make`) is available; pass "
                        "--build-info / --compile-db instead"
                    ),
                )
            )
            return None
        make_launcher = selected
    # cmake configures into an OUT-OF-TREE build dir: never mutate the --sources
    # checkout (it may be read-only / shared) and keep generated output away from
    # every tree walker, so no walker needs to prune an in-tree build dir
    # (maintainer decision). cmake writes absolute source/-I paths into
    # compile_commands.json, so an out-of-tree build dir resolves fine.
    #
    # The path is DETERMINISTIC per resolved source tree (not a random mkdtemp):
    # cmake records the build dir in each compile unit's `directory` and in
    # generated-header `-I` paths, which feed the L4 replay cache key and
    # compile-unit IDs. A random path would churn both every run (perpetual cache
    # misses, non-reproducible compile-unit IDs); a stable per-tree path keeps them
    # identical run-to-run. It lives inside a per-user 0700 root
    # (`_inferred_cmake_build_base`) so the predictable path can't be pre-planted as
    # a symlink on a shared /tmp (Codex P2). `_claim_inferred_build_dir` takes an
    # exclusive lock on it so concurrent scans of the same checkout serialize
    # instead of sharing one mutable tree (Codex P2); under contention it falls back
    # to a unique dir. Removed (and unlocked) after L4 via `cleanup`.
    build_dir: Path | None = None
    release: Callable[[], None] = _noop_release
    if system == "cmake":
        base = _inferred_cmake_build_base(sources)
        if base is None:
            # No owner-private temp dir could be established — refuse to configure
            # into a predictable shared path (symlink-attack safe; Codex P2).
            extractors.append(
                ExtractorRecord(
                    name="build_query_auto",
                    status="skipped",
                    detail=(
                        "could not create a private temp directory for the cmake "
                        "build; pass --build-info / --compile-db instead"
                    ),
                )
            )
            return None
        build_dir, release = _claim_inferred_build_dir(base, timeout)
        build_dir.mkdir(parents=True, exist_ok=True)
    try:
        cmd = inferred_query_command(
            system, sources, build_dir=build_dir, make_launcher=make_launcher
        )
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
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT if system == "make" else subprocess.PIPE,
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
        if proc.returncode != 0 and system == "make":
            try:
                _ingest_query_output(
                    system,
                    sources,
                    proc.stdout or "",
                    merged,
                    extractors,
                    build_dir=build_dir,
                    query_returncode=proc.returncode,
                )
            except (OSError, ValueError, KeyError, TypeError) as exc:
                extractors.append(
                    ExtractorRecord(
                        name="build_query_auto",
                        status="failed",
                        detail=(
                            "auto make dry-run failed and its transcript could not "
                            f"be ingested: {exc}"
                        ),
                    )
                )
                merged.diagnostics.append(
                    f"build_query_auto: make ingest failed ({exc})"
                )
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
                system,
                sources,
                proc.stdout,
                merged,
                extractors,
                build_dir=build_dir,
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
                # Defer removal: L4 replay still needs this dir as clang's cwd, and
                # the lock must stay held until then so a concurrent same-tree scan
                # can't reuse or delete the dir mid-replay.
                cleanup.append(
                    functools.partial(_release_inferred_build_dir, build_dir, release)
                )
            else:
                _release_inferred_build_dir(build_dir, release)


def _ingest_query_output(
    system: str,
    sources: Path,
    stdout: str,
    merged: BuildEvidence,
    extractors: list[ExtractorRecord],
    build_dir: Path | None = None,
    query_returncode: int = 0,
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
    if system == "make":
        from .adapters.make import MakeAdapter

        ev = MakeAdapter(build_dir=sources, dry_run=stdout).collect()
        merged.merge(ev)
        units = len(ev.compile_units)
        status = (
            "ok"
            if units and query_returncode == 0
            else ("partial" if units else "failed")
        )
        rc_note = "" if query_returncode == 0 else f" (make exited {query_returncode})"
        extractors.append(
            ExtractorRecord(
                name="build_query_auto",
                status=status,
                detail=(
                    f"auto-ran `make -B -n -k`; {units} compile unit(s) from "
                    f"dry-run transcript{rc_note}; reduced confidence"
                    if units
                    else f"auto-ran `make -B -n -k`{rc_note} but found no compile units"
                ),
            )
        )
        return None
    return None  # pragma: no cover - defensive: only cmake/bazel reach ingestion
