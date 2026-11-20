"""Running an external ABI tool: one command, its exit status, time and raw output.

Every adapter of a third-party checker (``abidiff``, ``abicc``) and the
abicheck CLI adapter run their commands through :func:`run_tool`, so each
pair records the same facts for every tool: the exact argv, the exit status,
wall time, whether it timed out, and where its raw stdout/stderr were written.
A tool that is not installed is ``unavailable``; a timeout is never read as an
answer.
"""

from __future__ import annotations

import shutil
import subprocess
import time
from dataclasses import asdict, dataclass, field
from functools import cache
from pathlib import Path

#: Default per-command limit, seconds.
DEFAULT_TIMEOUT = 300.0


@dataclass
class Step:
    """One executed command."""

    argv: list[str]
    exit: int | None
    seconds: float
    timed_out: bool = False
    stdout: str = ""  # path of the raw stdout file, relative to the pair dir
    stderr_tail: str = ""

    def to_json(self) -> dict[str, object]:
        return asdict(self)


@dataclass
class ToolRun:
    """A mode's run on one pair: its steps and the raw report files it left."""

    mode: str
    steps: list[Step] = field(default_factory=list)
    reports: dict[str, str] = field(default_factory=dict)  # role -> path
    unavailable: str | None = None

    @property
    def timed_out(self) -> bool:
        return any(s.timed_out for s in self.steps)

    @property
    def seconds(self) -> float:
        return round(sum(s.seconds for s in self.steps), 3)

    def to_json(self) -> dict[str, object]:
        return {
            "mode": self.mode,
            "steps": [s.to_json() for s in self.steps],
            "reports": dict(self.reports),
            "unavailable": self.unavailable,
            "seconds": self.seconds,
            "timed_out": self.timed_out,
        }


def run_tool(
    argv: list[str],
    log: Path,
    *,
    cwd: Path | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> Step:
    """Run *argv*, writing its stdout to *log* (and stderr to ``log.stderr``)."""
    log.parent.mkdir(parents=True, exist_ok=True)
    t0 = time.perf_counter()
    try:
        proc = subprocess.run(  # noqa: S603 - argv is built by the adapters
            argv,
            capture_output=True,
            text=True,
            cwd=cwd,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        out = exc.stdout or b""
        log.write_bytes(out if isinstance(out, bytes) else out.encode())
        return Step(argv, None, round(time.perf_counter() - t0, 3), True, log.name)
    log.write_text(proc.stdout, encoding="utf-8")
    log.with_name(log.name + ".stderr").write_text(proc.stderr, encoding="utf-8")
    return Step(
        argv,
        proc.returncode,
        round(time.perf_counter() - t0, 3),
        False,
        log.name,
        proc.stderr[-1500:],
    )


@cache
def tool_version(name: str) -> str | None:
    """The tool's own version string, or None when it is not installed."""
    argv = {
        "abidiff": ["abidiff", "--version"],
        "abi-dumper": ["abi-dumper", "-dumpversion"],
        "abi-compliance-checker": ["abi-compliance-checker", "-dumpversion"],
        "gcc": ["gcc", "-dumpfullversion"],
    }[name]
    if not shutil.which(argv[0]):
        return None
    proc = subprocess.run(argv, capture_output=True, text=True, check=False)  # noqa: S603
    return (
        (proc.stdout or proc.stderr).strip().splitlines()[0]
        if proc.returncode == 0
        else None
    )


def missing(*names: str) -> str | None:
    """``"unavailable: <name> not installed"`` for the first absent tool, else None."""
    for name in names:
        if not shutil.which(name):
            return f"unavailable: {name} not installed"
    return None
