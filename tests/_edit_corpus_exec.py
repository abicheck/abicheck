"""Execution oracle: what real consumers do when the library changes under them.

For a cell ``(op, seed, config)`` with old library ``old`` and new library
``new`` (both ``libedit.so``, same SONAME):

1. **Binary truth.** Every consumer shape (``_edit_corpus_consumers``) is
   compiled against the *old* header with the consumer toolchain and flags,
   linked against ``old``, and run with ``LD_LIBRARY_PATH`` pointing at
   ``old`` (twice: a shape whose two old runs differ is nondeterministic and
   is an oracle error) and then at ``new``, with ``LD_BIND_NOW=1`` so a
   missing symbol fails at load. A different exit status, signal, stdout or
   loader diagnostic is a break.
2. **Layout truth.** A probe compiled with the producer flags prints
   ``sizeof``/``offsetof``/enumerator values for every record and enum the
   header declares (parsed from the seed's role texts), for the old and the
   new header; a key both sides have with a different value is a break.
3. **Source truth.** Every shape is compiled against the *new* header
   (``-fsyntax-only``); a shape that no longer compiles is a source break.

None of this reads abicheck's output. The result is an ``ExecTruth``.

Only (1) and (3) observe a consumer. (2) is a surrogate: a changed ``sizeof``
or offset is what an ABI break is made of, but no consumer run showed it, so
``ExecTruth.layout_only`` marks a binary break that rests on it alone and
every report says so. Execution needs an ELF loader (Linux); elsewhere the
oracle reports itself unavailable instead of "no break".
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from tests._edit_corpus_config import Config, Flags
from tests._edit_corpus_consumers import CONSUMERS
from tests._edit_corpus_expect import ExecTruth
from tests._edit_corpus_harness import Built, CompileError, compile_cmd
from tests._edit_corpus_seeds import RoleText, Seed
from tests._edit_corpus_toolchains import compiler_crashed, execution_unavailable

_RUN_TIMEOUT = 20
_COMPILE_TIMEOUT = 300


def _compile(cmd: list[str], what: str) -> subprocess.CompletedProcess[str]:
    """Run a compiler command; a hang is a failure naming the command, never a skip."""
    try:
        return subprocess.run(
            cmd, capture_output=True, text=True, check=False, timeout=_COMPILE_TIMEOUT
        )
    except subprocess.TimeoutExpired as exc:
        raise CompileError(
            f"{what} did not finish in {_COMPILE_TIMEOUT}s: {' '.join(cmd)}"
        ) from exc


@dataclass(frozen=True)
class RunResult:
    rc: int
    stdout: str
    stderr: str

    def key(self) -> tuple[int, str, str]:
        return (self.rc, self.stdout, self.stderr)


def _ext(lang: str) -> str:
    return "cpp" if lang == "c++" else "c"


def _run(exe: Path, lib_dir: Path) -> RunResult:
    env = dict(os.environ)
    # Prepend: a toolchain runtime found through an inherited path stays found.
    inherited = env.get("LD_LIBRARY_PATH")
    env["LD_LIBRARY_PATH"] = (
        os.pathsep.join((str(lib_dir), inherited)) if inherited else str(lib_dir)
    )
    env["LD_BIND_NOW"] = "1"
    try:
        proc = subprocess.run(
            [str(exe)],
            capture_output=True,
            text=True,
            env=env,
            timeout=_RUN_TIMEOUT,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return RunResult(-999, "", "timeout")
    # Loader diagnostics name the library path; old and new paths differ.
    stderr = proc.stderr.replace(str(lib_dir), "<lib>")
    return RunResult(proc.returncode, proc.stdout, stderr)


# --- consumer builds (old header) ------------------------------------------


@dataclass(frozen=True)
class ConsumerSet:
    exes: dict[str, Path]
    baseline: dict[str, RunResult]


def build_consumers(
    seed: Seed, config: Config, old: Built, out_dir: Path
) -> ConsumerSet:
    """Compile each shape against the old header and link it to the old library."""
    out_dir.mkdir(parents=True, exist_ok=True)
    exes: dict[str, Path] = {}
    baseline: dict[str, RunResult] = {}
    for shape, text in CONSUMERS[seed.name].items():
        src = out_dir / f"{shape}.{_ext(seed.lang)}"
        src.write_text(text, encoding="utf-8")
        exe = out_dir / shape
        cmd = compile_cmd(config.consumer_tc, config, config.consumer_fl, debug=False)
        cmd += [
            "-w",
            "-I",
            str(old.dir),
            "-o",
            str(exe),
            str(src),
            "-L",
            str(old.dir),
            "-ledit",
        ]
        proc = _compile(cmd, f"consumer {shape}")
        if proc.returncode != 0:
            raise CompileError(
                f"consumer {shape} does not build against the OLD header: {' '.join(cmd)}\n{proc.stderr}"
            )
        first, second = _run(exe, old.dir), _run(exe, old.dir)
        if first.key() != second.key():
            raise AssertionError(
                f"consumer {shape} is nondeterministic against the old library: "
                f"{first} vs {second}"
            )
        if first.rc != 0:
            raise AssertionError(
                f"consumer {shape} fails against the OLD library: {first}"
            )
        exes[shape] = exe
        baseline[shape] = first
    return ConsumerSet(exes, baseline)


# --- layout probe ----------------------------------------------------------

_STRUCT = re.compile(r"struct\s+(\w+)\s*\{([^{}]*)\}\s*;")
_ENUM = re.compile(r"enum\s+(class\s+)?(\w+)\s*(?::\s*[\w ]+)?\{([^{}]*)\}\s*;")


def _field_names(body: str) -> list[str]:
    names = []
    for decl in body.split(";"):
        decl = re.sub(r"\[\[[^\]]*\]\]", "", decl)
        decl = re.sub(r"\[[^\]]*\]", "", decl).strip()
        m = re.search(r"(\w+)\s*$", decl)
        if m and decl.split() and len(decl.split()) >= 2:
            names.append(m.group(1))
    return names


def probe_expressions(roles: dict[str, RoleText], lang: str) -> dict[str, str]:
    """key -> C expression for every record/enum the header roles declare."""
    exprs: dict[str, str] = {}
    for role, (header, _) in sorted(roles.items()):
        if not header or "template" in header:
            continue  # a template has no layout until instantiated
        for m in _STRUCT.finditer(header):
            name = m.group(1)
            tname = f"struct {name}"
            exprs[f"sizeof({name})"] = f"sizeof({tname})"
            # GNU __alignof__ works in every C and C++ mode gcc and clang accept.
            exprs[f"alignof({name})"] = f"__alignof__({tname})"
            for f in _field_names(m.group(2)):
                exprs[f"offsetof({name},{f})"] = f"offsetof({tname}, {f})"
        for m in _ENUM.finditer(header):
            scoped, name, body = bool(m.group(1)), m.group(2), m.group(3)
            tname = name if lang == "c++" else f"enum {name}"
            exprs[f"sizeof(enum {name})"] = f"sizeof({tname})"
            for member in body.split(","):
                member = member.split("=")[0].strip()
                if member:
                    ref = f"{name}::{member}" if scoped else member
                    exprs[f"{name}::{member}"] = f"(long long)({ref})"
        if role == "shape.open":
            exprs["sizeof(Shape)"] = "sizeof(Shape)"
    return exprs


def _probe_source(exprs: dict[str, str]) -> str:
    lines = [
        "#include <stdio.h>",
        "#include <stddef.h>",
        '#include "edit_seed.h"',
        "int main(void) {",
    ]
    for key, expr in exprs.items():
        lines.append(f'  printf("%s=%lld\\n", "{key}", (long long)({expr}));')
    lines += ["  return 0;", "}"]
    return "\n".join(lines) + "\n"


def run_probe(
    seed: Seed,
    config: Config,
    flags: Flags,
    header_dir: Path,
    roles: dict[str, RoleText],
    out: Path,
) -> dict[str, str]:
    """Compile and run the layout probe against the header in *header_dir*."""
    out.parent.mkdir(parents=True, exist_ok=True)
    src = out.with_suffix(f".{_ext(seed.lang)}")
    src.write_text(_probe_source(probe_expressions(roles, seed.lang)), encoding="utf-8")
    cmd = compile_cmd(config.producer, config, flags, debug=False)
    cmd += ["-w", "-I", str(header_dir), "-o", str(out), str(src)]
    proc = _compile(cmd, "layout probe")
    if proc.returncode != 0:
        raise CompileError(
            f"layout probe does not build: {' '.join(cmd)}\n{proc.stderr}"
        )
    res = subprocess.run(
        [str(out)], capture_output=True, text=True, check=False, timeout=_RUN_TIMEOUT
    )
    if res.returncode != 0:
        raise AssertionError(f"layout probe failed: {res}")
    return dict(line.split("=", 1) for line in res.stdout.splitlines() if "=" in line)


# --- the oracle ------------------------------------------------------------


class ExecutionOracle:
    """Caches old-side consumers and probes per (seed, config)."""

    def __init__(self, root: Path) -> None:
        self._root = root
        self._consumers: dict[tuple[str, str], ConsumerSet] = {}
        self._probes: dict[tuple[str, str], dict[str, str]] = {}

    def _old(
        self, seed: Seed, config: Config, old: Built
    ) -> tuple[ConsumerSet, dict[str, str]]:
        key = (seed.name, config.id)
        if key not in self._consumers or key not in self._probes:
            base = self._root / f"{seed.name}-{config.id}"
            consumers = build_consumers(seed, config, old, base / "consumers")
            probe = run_probe(
                seed,
                config,
                config.flags,
                old.dir,
                dict(seed.roles),
                base / "probe-old",
            )
            # Publish both only once both succeeded, so a failure is retried.
            self._consumers[key], self._probes[key] = consumers, probe
        return self._consumers[key], self._probes[key]

    def truth(
        self,
        seed: Seed,
        config: Config,
        old: Built,
        new: Built,
        new_roles: dict[str, RoleText],
        new_flags: Flags,
        tag: str,
    ) -> ExecTruth:
        reason = execution_unavailable(config)
        if reason:
            return ExecTruth(available=False, reason=reason)
        consumers, old_probe = self._old(seed, config, old)
        signals: list[str] = []
        binary_break = source_break = False
        for shape, exe in consumers.exes.items():
            got = _run(exe, new.dir)
            want = consumers.baseline[shape]
            if got.key() != want.key():
                binary_break = True
                signals.append(f"run {shape}: {_describe(want, got)}")
        roles = {**dict(seed.roles), **new_roles}
        new_probe = run_probe(
            seed,
            config,
            new_flags,
            new.dir,
            roles,
            self._root / f"{seed.name}-{config.id}" / f"probe-{tag}",
        )
        for k in sorted(set(old_probe) & set(new_probe)):
            if old_probe[k] != new_probe[k]:
                binary_break = True
                signals.append(f"layout {k}: {old_probe[k]} -> {new_probe[k]}")
        for shape, exe in consumers.exes.items():
            src = exe.with_suffix(f".{_ext(seed.lang)}")
            cmd = compile_cmd(
                config.consumer_tc, config, config.consumer_fl, debug=False
            )
            cmd += ["-w", "-fsyntax-only", "-I", str(new.dir), str(src)]
            proc = _compile(cmd, f"new-header compile of {shape}")
            if compiler_crashed(proc):
                # A crashed or killed compiler says nothing about the source.
                raise CompileError(
                    f"new-header compile of {shape} crashed (exit {proc.returncode}): "
                    f"{' '.join(cmd)}\n--- {src.name} ---\n"
                    f"{src.read_text(encoding='utf-8')}--- stderr ---\n{proc.stderr}"
                )
            if proc.returncode != 0:
                source_break = True
                err = next(
                    (ln for ln in proc.stderr.splitlines() if "error" in ln),
                    proc.stderr[:120],
                )
                signals.append(f"source {shape}: {err.split(': ', 1)[-1].strip()}")
        return ExecTruth(
            available=True,
            binary_break=binary_break,
            source_break=source_break,
            signals=tuple(signals),
            layout_only=binary_layout_only(binary_break, signals),
        )


def binary_layout_only(
    binary_break: bool, signals: list[str] | tuple[str, ...]
) -> bool:
    """A binary break that no consumer *run* showed rests on the layout probe alone.

    A new-header compile failure is a source signal: it says nothing about
    whether an already-built consumer still runs, so it cannot make a layout
    difference an observed binary break.
    """
    return binary_break and not any(sig.startswith("run ") for sig in signals)


def _describe(want: RunResult, got: RunResult) -> str:
    if got.rc != want.rc:
        m = re.search(
            r"(undefined symbol: \S+|pure virtual method called|Segmentation fault)",
            got.stderr,
        )
        return f"exit {want.rc} -> {got.rc}" + (f" ({m.group(1)})" if m else "")
    if got.stdout != want.stdout:
        old_lines, new_lines = want.stdout.splitlines(), got.stdout.splitlines()
        diff = [f"{a} -> {b}" for a, b in zip(old_lines, new_lines) if a != b]
        if len(old_lines) != len(new_lines):
            diff.append(f"{len(old_lines)} -> {len(new_lines)} lines")
        return "output " + "; ".join(diff[:3])
    lines = got.stderr.strip().splitlines()
    if lines:
        return "loader diagnostics: " + lines[-1][:160]
    gone = (want.stderr.strip().splitlines() or ["(blank)"])[-1][:160]
    return "loader diagnostics gone: " + gone
