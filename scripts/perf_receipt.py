#!/usr/bin/env python3
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

"""The shared, versioned performance *receipt* the perf harnesses emit.

Three perf measurement levels already exist in this repository and they measure
genuinely different things:

1. ``benchmark_scaling.py`` — pure-Python synthetic scaling of the comparison,
   suppression, severity, serialization and reporting stages. No compiler.
2. ``check_header_graph_perf.py`` — the real L2 ``dump`` + header-graph attach,
   in-process, compiler-driven, on a synthetic header sweep.
3. ``check_l2_cli_perf.py`` — the whole ``abicheck`` CLI on a real small C++
   fixture, measured as a subprocess: interpreter startup, config resolution,
   input resolution, evidence extraction or load, comparison, and report
   writing.

Those stay separate -- collapsing a synthetic in-process microbenchmark and a
compiler-driven end-to-end CLI run into one "scenario" axis would invite
exactly the category error of quoting one level's number as the other's. What
they should *not* each reinvent is the surrounding measurement apparatus: run
identity, input digests, host/quota facts, phase accounting, native-invocation
counting, memory observation, and the JSON envelope all of it lands in. This
module owns that, once.

What it deliberately does **not** do:

* It does not claim an exact peak RSS. :class:`RssSample` is *sampled* at a
  recorded interval, so a spike shorter than that interval is missed, and
  shared pages (two castxml children sharing libc) are counted once per
  process. Every field that reports it says ``sampled_``, and the receipt
  carries the interval and the observation limits alongside the number.
  ``ru_maxrss`` is reported separately, under its own name, as what it
  actually is: a kernel high-water mark for one process subtree.
* It does not store secrets or the environment. The environment block is an
  explicit allowlist of facts (see :data:`_ENV_ALLOWLIST`), never a dump of
  ``os.environ``.
* It does not become a user-facing analysis mode. Nothing here is imported by
  ``abicheck/``; it is harness-side only, and the one product-side hook it
  relies on is the process's own observable behaviour (its children, its
  wall time, its output files) rather than an instrumentation API that would
  have to be maintained as a product surface.

Pure stdlib.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import resource
import shutil
import signal
import subprocess
import sys
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

#: Receipt schema. Bump on any field removal or meaning change; a purely
#: additive field does not need one, which is why consumers must tolerate
#: unknown keys.
RECEIPT_SCHEMA = "abicheck-perf-receipt/1"

#: The only environment variables recorded, and why each matters to a timing:
#: they change how much work runs or how it is scheduled. An allowlist rather
#: than a denylist because the failure mode of getting this wrong is committing
#: a token to a receipt artifact.
_ENV_ALLOWLIST = (
    "ABICHECK_AST_FRONTEND",
    "ABICHECK_PARALLEL_EXTRACTION",
    "COVERAGE_CORE",
    "OMP_NUM_THREADS",
    "PYTHONHASHSEED",
)

#: Tools whose invocation this harness counts. Each is a *native* process the
#: product may spawn; counting them is how "did this run really extract header
#: evidence" and "did a stored-snapshot path stay compiler-free" become
#: observations rather than assumptions.
SPIED_TOOLS = ("castxml", "clang", "clang++", "g++", "gcc", "cc", "c++", "llvm-config")

#: Invocation *kinds*, classified from the real argv the shim observed. A flat
#: per-tool count is not enough to answer the questions this harness asks:
#: ``castxml`` runs three times on a cold dump, and only *one* of those is a
#: header parse -- the other two are a ``--version`` probe and a
#: ``-dumpmachine`` query. A warm-cache run drops from three to two, and a
#: reader looking at the totals alone cannot tell whether the parse was served
#: from cache or whether a probe merely got skipped. Separating them is also a
#: direct requirement of keeping tool-version probes out of the
#: header-extraction count.
#:
#: The shapes below were read off real observed invocations on this fixture (a
#: ``castxml --castxml-output=1`` parse, a ``clang++ ... -Xclang
#: -ast-dump=json`` parse, a ``clang++ -M`` include pass, and the
#: ``--version``/``-dumpmachine``/``-E -dM`` probe family), not guessed from
#: the product's source -- which is the point of observing from outside.
INVOCATION_KINDS = ("header_extraction", "include_pass", "probe", "other")


#: The tools that can actually perform a header parse. ``tool`` is consulted
#: rather than trusted to argv alone so a *coincidental* argv match on some other
#: binary can never inflate the extraction count -- the one number the
#: stored-snapshot scenarios assert is zero. A no-op for every invocation
#: observed today (only castxml and clang++ parse), which is the point: it is a
#: guard, not a behaviour change.
_AST_CAPABLE_TOOLS = frozenset({"castxml", "clang", "clang++"})


def classify_invocation(tool: str, argv_text: str) -> str:
    """Which :data:`INVOCATION_KINDS` bucket an observed invocation falls in.

    Probes are matched first and deliberately: a ``--version`` run is a probe
    whatever tool made it, and mis-binning one as extraction would inflate
    exactly the count the stored-snapshot scenarios assert is zero.
    """
    args = argv_text.split()
    if any(
        a in ("--version", "-dumpversion", "-dumpmachine", "--print-prog-name")
        for a in args
    ):
        return "probe"
    # A bare preprocessor-macro/include-search dump with no input file of the
    # library's own is a toolchain interrogation, not work on the library.
    if "-dM" in args or ("-E" in args and "-v" in args):
        return "probe"
    if tool in _AST_CAPABLE_TOOLS and any(
        a.startswith(("--castxml-output", "-ast-dump")) for a in args
    ):
        return "header_extraction"
    if any(a in ("-M", "-MM", "-MD", "-MMD") for a in args):
        return "include_pass"
    return "other"


# ── host / run identity ───────────────────────────────────────────────────────
def _read_first_line(path: str) -> str | None:
    try:
        return Path(path).read_text(encoding="utf-8").strip().splitlines()[0]
    except (OSError, IndexError):
        return None


def cpu_quota() -> dict[str, Any]:
    """The CPU the measured process may actually use, not just ``cpu_count()``.

    A timing compared across two runs is only meaningful if both had the same
    amount of CPU, and on a container (which is where CI measures) the visible
    core count routinely overstates that. Reports both, plus the cgroup v2/v1
    quota when one is readable, with ``None`` and a stated reason rather than a
    fabricated number when it is not.
    """
    out: dict[str, Any] = {
        "os_cpu_count": os.cpu_count(),
        "affinity_count": None,
        "cgroup_quota_cpus": None,
        "unavailable_reason": None,
    }
    try:
        out["affinity_count"] = len(os.sched_getaffinity(0))
    except (AttributeError, OSError):
        pass
    v2 = _read_first_line("/sys/fs/cgroup/cpu.max")
    if v2:
        parts = v2.split()
        if len(parts) == 2 and parts[0] != "max":
            try:
                out["cgroup_quota_cpus"] = int(parts[0]) / int(parts[1])
            except (ValueError, ZeroDivisionError):
                out["unavailable_reason"] = f"unparseable cpu.max: {v2!r}"
        else:
            out["unavailable_reason"] = "cgroup v2 quota is unlimited ('max')"
    else:
        out["unavailable_reason"] = "no readable cgroup v2 cpu.max"
    return out


def memory_limit() -> dict[str, Any]:
    """The memory ceiling the measured process faces, with its source named.

    Deliberately reports the cgroup limit *and* total system memory as separate
    facts rather than picking one: which of the two actually binds depends on
    the host, and a receipt that silently reports whichever it found makes two
    runs look comparable when they were not.
    """
    out: dict[str, Any] = {
        "cgroup_limit_bytes": None,
        "system_total_bytes": None,
        "unavailable_reason": None,
    }
    v2 = _read_first_line("/sys/fs/cgroup/memory.max")
    if v2 and v2 != "max":
        try:
            out["cgroup_limit_bytes"] = int(v2)
        except ValueError:
            out["unavailable_reason"] = f"unparseable memory.max: {v2!r}"
    elif v2 == "max":
        out["unavailable_reason"] = "cgroup v2 memory limit is unlimited ('max')"
    else:
        out["unavailable_reason"] = "no readable cgroup v2 memory.max"
    try:
        for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
            if line.startswith("MemTotal:"):
                out["system_total_bytes"] = int(line.split()[1]) * 1024
                break
    except (OSError, ValueError, IndexError):
        pass
    return out


def _git(*args: str) -> str | None:
    try:
        return subprocess.run(
            ["git", *args],
            capture_output=True,
            text=True,
            timeout=20,
            check=True,
            cwd=Path(__file__).resolve().parent.parent,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None


def tool_version(tool: str) -> str | None:
    """The first line of *tool*'s own version output, or ``None``.

    Run once per receipt, outside every timed window, and counted separately
    from header extraction: a version probe is a ``castxml --version`` process,
    and folding it into the extraction count would overstate how many real
    extraction invocations a run performed -- which is precisely the number the
    stored-snapshot scenarios assert on.
    """
    path = shutil.which(tool)
    if path is None:
        return None
    for flag in ("--version", "-dumpversion"):
        try:
            proc = subprocess.run(
                [path, flag],
                capture_output=True,
                text=True,
                timeout=30,
                # A probe's non-zero exit is information, not an error: a tool
                # that rejects --version is simply reported as version-unknown.
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        text = (proc.stdout or proc.stderr).strip()
        if text:
            return text.splitlines()[0]
    return None


def run_identity(
    *, harness: str, extra: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Product/harness SHA, toolchain, interpreter, OS and resource facts."""
    identity: dict[str, Any] = {
        "harness": harness,
        "product_sha": _git("rev-parse", "HEAD"),
        "product_dirty": bool(_git("status", "--porcelain")),
        "python_version": platform.python_version(),
        "python_implementation": platform.python_implementation(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "processor": platform.processor() or None,
        "cpu_quota": cpu_quota(),
        "memory_limit": memory_limit(),
        "env": {k: os.environ[k] for k in _ENV_ALLOWLIST if k in os.environ},
        "toolchain": {t: tool_version(t) for t in ("g++", "clang++", "castxml")},
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    if extra:
        identity.update(extra)
    return identity


def digest_paths(paths: list[Path]) -> str:
    """A content digest over *paths*, stable across directory iteration order.

    Names are included alongside contents, so renaming a header changes the
    digest: a profile's identity has to cover which inputs were used, not only
    what was in them.
    """
    h = hashlib.sha256()
    for p in sorted(paths, key=str):
        h.update(p.name.encode())
        h.update(b"\0")
        try:
            h.update(p.read_bytes())
        except OSError:
            h.update(b"<unreadable>")
        h.update(b"\0")
    return h.hexdigest()[:32]


# ── native invocation counting ────────────────────────────────────────────────
class NativeInvocationSpy:
    """Counts real native tool invocations made by a measured subprocess.

    Implemented as a directory of tiny ``exec``-ing shims prepended to ``PATH``,
    one per :data:`SPIED_TOOLS` entry that actually resolves. Each shim appends
    its own name to a log and then ``exec``s the real binary, so it observes
    without changing what runs.

    Why this rather than in-process instrumentation: the questions that matter
    are "did this run really extract header evidence" and, more sharply, "did
    this stored-snapshot comparison spawn *no* compiler at all". A counter
    inside the product would answer both only as well as its own wiring; a
    count of processes actually executed answers them from outside, and can
    prove an absence. It is also what lets a harness detect the inverse of a
    slowdown: a run that got faster because it silently stopped extracting.

    Limits, stated because a reader will want them: a tool invoked by absolute
    path bypasses ``PATH`` and is not seen (abicheck resolves its tools through
    ``shutil.which``, so this is not the case today but could become so), and
    the shim adds one ``exec`` per invocation -- microseconds against a castxml
    parse, but it is not zero, which is why the spy is opt-in per run and the
    harness measures its own overhead with it off.
    """

    LOG_ENV = "ABICHECK_PERF_SPY_LOG"

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.log = directory / "invocations.log"
        self.shimmed: list[str] = []

    def install(self) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        self.log.write_text("", encoding="utf-8")
        for tool in SPIED_TOOLS:
            real = shutil.which(tool)
            if real is None:
                continue
            shim = self.directory / tool
            shim.write_text(
                "#!/bin/sh\n"
                # Append, never truncate: several tools run concurrently, and a
                # single-line append under O_APPEND is atomic enough for a count.
                #
                # The full argv is logged, not just the tool name: the
                # classification above needs it, and without it a cold-vs-warm
                # count delta cannot be attributed to a cached parse rather
                # than a skipped version probe. Tab-separated so a path
                # containing spaces cannot be mistaken for a field boundary.
                f'printf "%s\\t%s\\n" "{tool}" "$*" >> "${self.LOG_ENV}"\n'
                f'exec "{real}" "$@"\n',
                encoding="utf-8",
            )
            shim.chmod(0o755)
            self.shimmed.append(tool)

    def env(self, base: dict[str, str] | None = None) -> dict[str, str]:
        env = dict(base if base is not None else os.environ)
        env["PATH"] = f"{self.directory}{os.pathsep}{env.get('PATH', '')}"
        env[self.LOG_ENV] = str(self.log)
        return env

    def reset(self) -> None:
        self.log.write_text("", encoding="utf-8")

    def counts(self) -> dict[str, int]:
        """Invocations per tool name. Absent tools are omitted, not zeroed...

        ...except that a *shimmed* tool which was never invoked IS reported as
        ``0``. The distinction is the whole point: "castxml was available and
        ran zero times" is a positive finding about a stored-snapshot path,
        while "castxml is not installed" is a missing precondition. Collapsing
        them onto the same empty mapping is how a harness comes to report a
        compiler-free run it never actually proved.
        """
        counts = {tool: 0 for tool in self.shimmed}
        for tool, _ in self._records():
            counts[tool] = counts.get(tool, 0) + 1
        return counts

    def _records(self) -> list[tuple[str, str]]:
        try:
            lines = self.log.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        out = []
        for line in lines:
            tool, _, argv = line.partition("\t")
            if tool.strip():
                out.append((tool.strip(), argv))
        return out

    def kind_counts(self) -> dict[str, int]:
        """Invocations per :data:`INVOCATION_KINDS` bucket.

        Every bucket is present, including the zeros: ``header_extraction: 0``
        is a *finding* about a stored-snapshot run, and an omitted key would
        make it indistinguishable from a spy that was never installed.
        """
        counts = {kind: 0 for kind in INVOCATION_KINDS}
        for tool, argv in self._records():
            counts[classify_invocation(tool, argv)] += 1
        return counts

    def total(self) -> int:
        return sum(self.counts().values())

    def extraction_count(self) -> int:
        """Real header parses only -- no version probes, no include passes.

        This is the number every "did it really extract" / "did it stay
        compiler-free" assertion in the harness is written against.
        """
        return self.kind_counts()["header_extraction"]


# ── memory observation ────────────────────────────────────────────────────────
@dataclass(frozen=True)
class RssSample:
    """A *sampled* concurrent process-tree RSS observation. Not a peak.

    ``sampled_peak_tree_bytes`` is the largest simultaneous sum observed across
    the measured process and every descendant alive at one sampling instant.
    Read it as a lower bound on the real concurrent high-water mark:

    * a spike shorter than ``interval_seconds`` is missed entirely;
    * pages shared between processes (two castxml children sharing libc, or
      copy-on-write pages right after a fork) are counted once per process, so
      the figure can also *over*state real physical usage;
    * a descendant that exits between two samples is never seen.

    Both error directions are real, which is why this is deliberately not named
    ``peak_rss``. ``ru_maxrss_bytes`` is a separate, differently-derived
    number: the kernel's own high-water mark for the harness's waited-for
    children, which never misses a spike but also never reports two live
    children *simultaneously* -- it is a max over individual processes, not a
    sum over a tree. Reporting both, labelled, is the only honest option;
    picking one would hide the failure mode of the other.
    """

    sampled_peak_tree_bytes: int | None
    sample_count: int
    interval_seconds: float
    max_concurrent_processes: int
    ru_maxrss_bytes: int | None
    unavailable_reason: str | None = None


def _proc_rss_bytes(pid: int) -> int | None:
    try:
        fields = Path(f"/proc/{pid}/statm").read_text(encoding="utf-8").split()
        return int(fields[1]) * os.sysconf("SC_PAGE_SIZE")
    except (OSError, IndexError, ValueError):
        return None


def _descendants(root: int) -> list[int]:
    """*root* plus every live descendant, via one ``/proc`` scan.

    Builds the parent map from ``/proc/<pid>/stat`` and walks down from *root*,
    rather than reading ``children`` files recursively: one pass is cheaper, and
    it cannot partially observe a tree that is changing under it.
    """
    parents: dict[int, int] = {}
    try:
        entries = os.listdir("/proc")
    except OSError:
        return [root]
    for name in entries:
        if not name.isdigit():
            continue
        try:
            stat = Path(f"/proc/{name}/stat").read_text(encoding="utf-8")
            # The comm field may itself contain spaces and parentheses, so
            # split after the LAST ')' rather than on whitespace.
            ppid = int(stat[stat.rindex(")") + 1 :].split()[1])
        except (OSError, ValueError, IndexError):
            continue
        parents[int(name)] = ppid
    tree = [root]
    frontier = [root]
    while frontier:
        current = frontier.pop()
        kids = [pid for pid, ppid in parents.items() if ppid == current]
        tree.extend(kids)
        frontier.extend(kids)
    return tree


class TreeRssSampler:
    """Samples a subprocess tree's concurrent RSS from the parent process.

    Runs in a parent-side thread rather than inside the measured process: the
    measured process is the thing under test and must not be asked to account
    for itself, and a sampler that lived there could not see its own children
    exit. The parent's own RSS is deliberately excluded -- only the root pid
    handed to :meth:`start` and its descendants are summed.

    ``max_concurrent_processes`` is recorded because it is the check on whether
    the sampler ever actually observed the situation that makes tree sampling
    necessary: if a run that should fan out to two compilers never reports more
    than one live process, the number being reported is not a tree measurement
    at all, it is a single-process one wearing the name.
    """

    def __init__(self, interval_seconds: float = 0.02) -> None:
        self.interval = interval_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.peak = 0
        self.samples = 0
        self.max_processes = 0
        self.unavailable_reason: str | None = None
        if not Path("/proc").is_dir():
            self.unavailable_reason = "no /proc: tree RSS sampling is Linux-only"

    def start(self, root_pid: int) -> None:
        if self.unavailable_reason:
            return

        def loop() -> None:
            while not self._stop.is_set():
                pids = _descendants(root_pid)
                total = 0
                live = 0
                for pid in pids:
                    rss = _proc_rss_bytes(pid)
                    if rss is not None:
                        total += rss
                        live += 1
                if live:
                    self.samples += 1
                    self.peak = max(self.peak, total)
                    self.max_processes = max(self.max_processes, live)
                self._stop.wait(self.interval)

        self._thread = threading.Thread(target=loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            # Joined with a bounded timeout: a sampler thread that somehow
            # wedged must not hang the harness that is only observing it.
            self._thread.join(timeout=self.interval * 20 + 1.0)
            self._thread = None

    def result(self, *, ru_maxrss_bytes: int | None) -> RssSample:
        return RssSample(
            sampled_peak_tree_bytes=self.peak or None,
            sample_count=self.samples,
            interval_seconds=self.interval,
            max_concurrent_processes=self.max_processes,
            ru_maxrss_bytes=ru_maxrss_bytes,
            unavailable_reason=self.unavailable_reason
            or (None if self.samples else "process exited before the first sample"),
        )


# ── the measured subprocess run ───────────────────────────────────────────────
@dataclass
class CommandRun:
    """One measured subprocess execution and everything observed about it."""

    argv: list[str]
    exit_code: int | None
    wall_seconds: float
    user_cpu_seconds: float | None
    system_cpu_seconds: float | None
    cpu_scope: str
    timed_out: bool
    stdout: str
    stderr: str
    native_invocations: dict[str, int] = field(default_factory=dict)
    rss: RssSample | None = None

    def as_dict(self) -> dict[str, Any]:
        out = asdict(self)
        # stdout/stderr stay out of the receipt: they are large, can be
        # hundreds of KB of report text, and the receipt is committed as a CI
        # artifact. The harness keeps them in memory for validation.
        out.pop("stdout")
        out.pop("stderr")
        return out


def run_measured(
    argv: list[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    timeout: float = 600.0,
    sample_rss: bool = False,
    rss_interval: float = 0.02,
) -> CommandRun:
    """Run *argv* as a subprocess, timing and observing it.

    The timed window is the subprocess's own whole lifetime -- ``Popen`` to
    reaped exit. For a CLI harness that is the right boundary: interpreter
    startup, config discovery, input resolution, evidence work, comparison and
    report writing are all things a user waits for, and excluding any of them
    would report a number no user experiences. Fixture construction, artifact
    download and dependency installation happen outside, before this call.

    CPU time is read from ``RUSAGE_CHILDREN`` deltas and labelled
    ``cpu_scope="waited_children_delta"`` rather than presented as this
    command's CPU: the counter is cumulative over all of the harness's reaped
    children, so the delta is correct only because nothing else is reaped
    concurrently. Naming the scope is what keeps a later caller from
    parallelising the harness and silently turning the field into nonsense.

    On timeout the whole process *group* is terminated, then killed -- not just
    the direct child. A castxml grandchild outliving its parent would otherwise
    keep running, skewing every subsequent measurement on the same host, and
    the RSS sampler would keep attributing it to a tree that had already been
    abandoned.
    """
    sampler = TreeRssSampler(rss_interval) if sample_rss else None
    before = resource.getrusage(resource.RUSAGE_CHILDREN)
    start = time.perf_counter()
    proc = subprocess.Popen(
        argv,
        cwd=str(cwd) if cwd else None,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        # Own process group, so a timeout can reap grandchildren too.
        start_new_session=True,
    )
    if sampler is not None:
        sampler.start(proc.pid)
    timed_out = False
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(os.getpgid(proc.pid), sig)
            except (ProcessLookupError, PermissionError, OSError):
                break
            try:
                stdout, stderr = proc.communicate(timeout=10)
                break
            except subprocess.TimeoutExpired:
                continue
        else:
            stdout, stderr = "", ""
        if proc.returncode is None:
            proc.wait(timeout=10)
    wall = time.perf_counter() - start
    if sampler is not None:
        sampler.stop()
    after = resource.getrusage(resource.RUSAGE_CHILDREN)
    rss = None
    if sampler is not None:
        # ru_maxrss is in KiB on Linux.
        rss = sampler.result(ru_maxrss_bytes=after.ru_maxrss * 1024 or None)
    return CommandRun(
        argv=list(argv),
        exit_code=proc.returncode,
        wall_seconds=wall,
        user_cpu_seconds=after.ru_utime - before.ru_utime,
        system_cpu_seconds=after.ru_stime - before.ru_stime,
        cpu_scope="waited_children_delta",
        timed_out=timed_out,
        stdout=stdout or "",
        stderr=stderr or "",
        rss=rss,
    )


def build_receipt(
    *,
    harness: str,
    profile: str,
    identity_extra: dict[str, Any] | None = None,
    scenarios: list[dict[str, Any]],
    thresholds: dict[str, Any] | None = None,
    notes: list[str] | None = None,
) -> dict[str, Any]:
    """Assemble the versioned receipt envelope."""
    return {
        "schema": RECEIPT_SCHEMA,
        "profile": profile,
        "identity": run_identity(harness=harness, extra=identity_extra),
        "effective_thresholds": thresholds or {},
        "scenarios": scenarios,
        "notes": notes or [],
    }


def write_receipt(path: Path, receipt: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # allow_nan=False: a NaN/Infinity in a receipt is both non-standard JSON
    # and, for anything a gate later reads, a silent always-pass. Failing here
    # surfaces it at the point it was produced.
    path.write_text(
        json.dumps(receipt, indent=2, allow_nan=False, default=str) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":  # pragma: no cover - a convenience probe
    json.dump(run_identity(harness="perf_receipt-probe"), sys.stdout, indent=2)
    print()
