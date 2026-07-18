# Copyright 2026 Nikolay Petrov
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

"""Old-consumer/new-library runtime execution probe — ADR-044 P2 item 2.

Answers a question :mod:`abicheck.appcompat`'s purely static undefined-symbol
check cannot: "does this *compiled* consumer binary still load and run
against the new library, right now, on this machine?" Opt-in
(``--verify-runtime``, alongside ``--used-by``), and explicitly a
*corroborating* signal alongside the static scanner, never a replacement for
it (ADR-044 P2 item 2) — a real consumer's binary is executed with
``LD_BIND_NOW=1`` (forces eager symbol resolution at load time, matching
what a production deployment with `-z now` would see) so a missing symbol
fails immediately and loudly instead of lazily on first call.

Deliberately narrow: the only failure mode this probe recognizes is glibc's
own ``symbol lookup error: ... undefined symbol: X`` message on stderr — the
dynamic linker's unambiguous signal that eager binding could not resolve a
real symbol. Other runtime failures (a layout mismatch causing a crash deep
inside the app's own logic, a segfault from unrelated causes, the app's own
business-logic exit code) are explicitly **not** interpreted here: an
app-supplied nonzero exit code is common and meaningless on its own, so
treating it as a regression would be noisy and unreliable. This keeps the
probe's one claim ("the dynamic linker itself refused to resolve a symbol")
airtight rather than trying to infer more than the evidence supports.

Linux-only: ``LD_BIND_NOW``/``LD_LIBRARY_PATH`` are glibc/ELF mechanisms with
no reliable equivalent on macOS (SIP strips ``DYLD_*`` env vars for many
binaries) or Windows (no env-var-driven early-bind/preload for PE loading).
Skips with a reason on any other platform — mirrors :mod:`abicheck.bundle`'s
ELF-only degrade path — never raises.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

_SYMBOL_LOOKUP_ERROR_RE = re.compile(
    # A versioned symbol lookup failure appends ", version X" after the bare
    # name (e.g. "undefined symbol: foo, version FOO_1.0") -- [^,\s]+ (not
    # \S+) stops before the comma so the captured symbol matches the real
    # import/export name, not "foo," (Codex review).
    r"symbol lookup error:.*undefined symbol:\s*([^,\s]+)"
)

#: glibc's ld.so emits this exact, version-stable substring when the dynamic
#: linker itself fails to load the binary for any reason *other* than an
#: undefined symbol (most commonly a missing dependency: "error while
#: loading shared libraries: libfoo.so.2: cannot open shared object file").
#: Recognized as ok=False (Codex review, fresh evidence): without this, a
#: probe run whose stderr doesn't match the symbol-lookup regex falls
#: through to ok=True regardless of exit code, so an OLD run that never
#: actually loaded (e.g. missing an unrelated dependency) would still let
#: regressed_symbol fire on the NEW run's real undefined-symbol failure --
#: an app's own nonzero exit code stays deliberately uninterpreted (common
#: and meaningless on its own), but an explicit loader-failure message is
#: not that; it is the same class of unambiguous linker statement as the
#: symbol-lookup-error case just above.
_LOADER_ERROR_RE = re.compile(r"error while loading shared libraries")

#: Tail of captured stderr kept on a probe outcome — enough for a human to
#: see the failure, small enough not to bloat a JSON/SARIF report.
_STDERR_TAIL_CHARS = 2000

#: Default wall-clock budget for one consumer-binary execution attempt.
DEFAULT_TIMEOUT = 10.0


@dataclass
class RuntimeProbeOutcome:
    """One side's (old or new library) execution attempt."""

    ok: bool
    missing_symbol: str | None = None
    stderr_tail: str = ""
    timed_out: bool = False


@dataclass
class RuntimeProbeResult:
    """Result of probing one consumer binary against the old and new library."""

    app_path: str
    attempted: bool
    skipped_reason: str | None = None
    old: RuntimeProbeOutcome | None = None
    new: RuntimeProbeOutcome | None = None

    @property
    def regressed_symbol(self) -> str | None:
        """The specific symbol whose resolution regressed old→new, if any.

        Only set when the app ran cleanly against the old library
        (``old.ok``) but the dynamic linker itself named a missing symbol
        against the new one — the one shape this probe treats as
        attributable to the library change, not an unrelated environment
        factor.
        """
        if self.old is not None and self.old.ok and self.new is not None:
            return self.new.missing_symbol
        return None


def _load_name(lib_path: Path) -> str:
    """The name the dynamic linker will actually look up for *lib_path*.

    Reads the target's ``DT_SONAME`` when present -- that's the string glibc
    matches against ``LD_LIBRARY_PATH`` entries, and it can differ from the
    file's own on-disk name (e.g. ``libfoo.so.1.2.3`` embedding SONAME
    ``libfoo.so.1``). Falls back to the file's own basename when there is no
    SONAME, parsing fails (``parse_elf_metadata`` degrades to an empty
    ``ElfMetadata`` rather than raising), or -- defensively -- the SONAME
    contains a path separator, which would otherwise escape the staged
    directory in :func:`_run_once`.
    """
    from .elf_metadata import parse_elf_metadata

    soname = parse_elf_metadata(lib_path).soname
    if not soname or "/" in soname:
        return lib_path.name
    return soname


def _run_once(app_path: Path, lib_path: Path, timeout: float) -> RuntimeProbeOutcome:
    env = dict(os.environ)
    env["LD_BIND_NOW"] = "1"
    lib_path = lib_path.resolve()
    lib_dir = str(lib_path.parent)
    existing = env.get("LD_LIBRARY_PATH", "")
    try:
        with tempfile.TemporaryDirectory(prefix="abicheck-runtime-probe-") as stage_dir:
            # old_lib and new_lib may be different files sitting in the *same*
            # directory (e.g. versioned build artifacts) -- pointing
            # LD_LIBRARY_PATH at that shared directory for both runs would let
            # the dynamic loader resolve to whichever candidate happens to
            # match the requested name first, silently ignoring which of
            # old/new this specific run was supposed to test (Codex review).
            # Staging the intended file alone, under its own load name, in a
            # directory nothing else occupies, and listing that directory
            # *first* forces the loader to resolve to exactly this file.
            # lib_dir is still appended after it so any other same-directory
            # runtime dependency the library itself needs remains resolvable.
            staged = Path(stage_dir) / _load_name(lib_path)
            staged.symlink_to(lib_path)
            search_path = f"{stage_dir}:{lib_dir}"
            env["LD_LIBRARY_PATH"] = f"{search_path}:{existing}" if existing else search_path
            proc = subprocess.run(
                # A bare relative name with no directory component (e.g. Path("app")
                # from a cwd-relative --used-by arg) would otherwise be searched for
                # on PATH instead of the current directory, like a shell would do
                # for an unqualified command (Codex review) -- resolve first, same
                # as lib_path above.
                [str(app_path.resolve())],
                env=env,
                capture_output=True,
                text=True,
                # A real executable's stderr is arbitrary bytes, not guaranteed
                # to be valid UTF-8 (or the locale's encoding) -- without this,
                # decoding raises UnicodeDecodeError *after* the child exits,
                # escaping this best-effort helper and aborting the whole
                # compare instead of returning a RuntimeProbeOutcome (Codex
                # review). Malformed bytes are replaced, not dropped, so the
                # symbol-lookup-error regex still matches valid ASCII segments
                # around them.
                errors="replace",
                timeout=timeout,
                check=False,
            )
    except subprocess.TimeoutExpired:
        return RuntimeProbeOutcome(ok=False, timed_out=True)
    except OSError as exc:
        return RuntimeProbeOutcome(ok=False, stderr_tail=str(exc))
    stderr = proc.stderr or ""
    match = _SYMBOL_LOOKUP_ERROR_RE.search(stderr)
    if match:
        return RuntimeProbeOutcome(
            ok=False,
            missing_symbol=match.group(1),
            stderr_tail=stderr[-_STDERR_TAIL_CHARS:],
        )
    if _LOADER_ERROR_RE.search(stderr):
        return RuntimeProbeOutcome(ok=False, stderr_tail=stderr[-_STDERR_TAIL_CHARS:])
    return RuntimeProbeOutcome(ok=True, stderr_tail=stderr[-_STDERR_TAIL_CHARS:])


def run_runtime_probe(
    app_path: Path,
    old_lib: Path,
    new_lib: Path,
    *,
    timeout: float = DEFAULT_TIMEOUT,
) -> RuntimeProbeResult:
    """Run *app_path* once against *old_lib* and once against *new_lib*.

    Both runs set ``LD_BIND_NOW=1`` and stage the respective library into
    its own isolated directory listed first on ``LD_LIBRARY_PATH`` (its
    parent directory is still appended after, for any other same-directory
    runtime dependency) -- this way each run resolves to exactly the
    intended file even when *old_lib* and *new_lib* are different files in
    the same directory. Never raises: an unsupported platform,
    a non-executable *app_path*, a timeout, or any OS-level failure to spawn
    the process all degrade to a result the caller can inspect, exactly
    like :mod:`abicheck.appcompat`'s own best-effort parsing.
    """
    if not sys.platform.startswith("linux"):
        return RuntimeProbeResult(
            app_path=str(app_path),
            attempted=False,
            skipped_reason=(
                "runtime execution probe needs LD_BIND_NOW/LD_LIBRARY_PATH "
                f"(Linux/glibc-only); not supported on {sys.platform!r}"
            ),
        )
    if not os.access(app_path, os.X_OK):
        return RuntimeProbeResult(
            app_path=str(app_path),
            attempted=False,
            skipped_reason=f"{app_path} is not executable",
        )
    old_outcome = _run_once(app_path, old_lib, timeout)
    new_outcome = _run_once(app_path, new_lib, timeout)
    return RuntimeProbeResult(
        app_path=str(app_path),
        attempted=True,
        old=old_outcome,
        new=new_outcome,
    )
