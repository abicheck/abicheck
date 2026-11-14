"""Execution oracle: what real consumers do when the library changes under them.

For a cell ``(op, seed, config)`` with old library ``old`` and new library
``new`` (both ``libedit.so``, same SONAME, built once per ``config.build_id``),
steps (1) and (3) run once per consumer of ``config.consumer_set`` (its own
toolchain, standard and flags) against that one library pair, and each
(build, consumer) pair is reported as a ``PairTruth``; a pair the cell marks
infeasible or unavailable is reported with its reason and never runs:

1. **Binary truth.** Every consumer shape (``abiforge.run.consumers``) is
   compiled against the *old* header with the consumer's toolchain, standard
   and flags, linked against ``old``, and run with ``LD_LIBRARY_PATH`` pointing at
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
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from abiforge.model.config import Config, Flags, ResolvedConsumer
from abiforge.model.expect import ExecTruth, PairTruth
from abiforge.model.seeds import RoleText, Seed
from abiforge.run.consumers import CONSUMERS
from abiforge.run.harness import Built, CompileError, compile_cmd
from abiforge.run.toolchains import compiler_crashed, execution_unavailable

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
    seed: Seed, consumer: ResolvedConsumer, old: Built, out_dir: Path
) -> ConsumerSet:
    """Compile each shape against the old header and link it to the old library."""
    out_dir.mkdir(parents=True, exist_ok=True)
    exes: dict[str, Path] = {}
    baseline: dict[str, RunResult] = {}
    for shape, text in CONSUMERS[seed.name].items():
        src = out_dir / f"{shape}.{_ext(seed.lang)}"
        src.write_text(text, encoding="utf-8")
        exe = out_dir / shape
        cmd = compile_cmd(consumer.toolchain, consumer.std, consumer.flags, debug=False)
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
                f"consumer {shape} [{consumer.id}] does not build against the OLD header: "
                f"{' '.join(cmd)}\n{proc.stderr}"
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


# __attribute__((...)) with one level of nested parentheses (aligned(8)).
_ATTRIBUTE = re.compile(r"__attribute__\s*\(\((?:[^()]|\([^()]*\))*\)\)")
_FUNC_POINTER = re.compile(r"\(\s*\*\s*(\w+)\s*\)\s*\(")


def _field_names(body: str) -> list[str]:
    """The data members ``offsetof`` can name in one record body.

    Attributes (``__attribute__((...))``, ``[[...]]``) and alignment specifiers
    (``alignas(...)``, ``_Alignas(...)``) are stripped first, so a specified
    field stays a field. A function-pointer member (``int (*cb)(int)``) is a
    field named by its declarator; any other ``(`` left is a member function.
    A bit-field and a static data member are skipped (``offsetof`` is
    ill-formed on both); an access label (``private:``) is not a declaration,
    and a default member initializer (``= 4``, ``{4}``) is not the name. A
    field under a ``protected:``/``private:`` label is skipped too: outside
    the class ``offsetof`` cannot name it (a ``struct`` body starts public).
    """
    names = []
    public = True
    for decl in body.split(";"):
        decl = _ATTRIBUTE.sub("", decl)
        decl = re.sub(r"\[\[.*?\]\]", "", decl)
        decl = re.sub(r"\b(?:alignas|_Alignas)\s*\([^()]*\)", "", decl)
        labels = re.findall(r"\b(public|protected|private)\s*:(?!:)", decl)
        if labels:
            public = labels[-1] == "public"
        decl = re.sub(r"\b(?:public|protected|private)\s*:(?!:)", "", decl)
        if not public:
            continue
        if re.search(r"\bstatic\b", decl):
            continue  # a static data member: not part of the layout
        pointer = _FUNC_POINTER.search(decl)
        if pointer:
            names.append(pointer.group(1))
            continue
        if "(" in decl:
            continue  # a member function, not a field
        if re.search(r"(?<!:):(?!:)", decl):
            continue  # a bit-field: offsetof is ill-formed on it
        decl = re.sub(r"\s*(?:=.*|\{[^{}]*\})$", "", decl.strip(), flags=re.S)
        decl = re.sub(r"\[[^\]]*\]", "", decl).strip()
        m = re.search(r"(\w+)\s*$", decl)
        if m and decl.split() and len(decl.split()) >= 2:
            names.append(m.group(1))
    return names


def _namespace_at(header: str, pos: int) -> str:
    """The ``ns::`` qualification (C++ ``namespace`` blocks) open at *pos*."""
    stack: list[str | None] = []
    for m in re.finditer(r"\bnamespace\s+(\w+)\s*\{|\{|\}", header[:pos]):
        if m.group(1):
            stack.append(m.group(1))
        elif m.group(0) == "{":
            stack.append(None)
        elif stack:
            stack.pop()
    return "".join(f"{n}::" for n in stack if n)


def probe_expressions(roles: dict[str, RoleText], lang: str) -> dict[str, str]:
    """key -> C expression for every record/enum the header roles declare."""
    exprs: dict[str, str] = {}
    for role, (header, _) in sorted(roles.items()):
        if not header or "template" in header:
            continue  # a template has no layout until instantiated
        for m in _STRUCT.finditer(header):
            name = _namespace_at(header, m.start()) + m.group(1)
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
    cmd = compile_cmd(config.producer, config.std, flags, debug=False)
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


# A pair the cell decided not to run: (consumer, status, reason).
Blocked = tuple[ResolvedConsumer, str, str]


class ExecutionOracle:
    """Caches old-side consumers per (seed, build, consumer) and probes per (seed, build)."""

    def __init__(self, root: Path) -> None:
        self._root = root
        self._consumers: dict[tuple[str, str, str], ConsumerSet] = {}
        self._probes: dict[tuple[str, str], dict[str, str]] = {}

    def _build_dir(self, seed: Seed, config: Config) -> Path:
        return self._root / f"{seed.name}-{config.build_id}"

    def _old(
        self, seed: Seed, config: Config, old: Built, consumer: ResolvedConsumer
    ) -> tuple[ConsumerSet, dict[str, str]]:
        ckey = (seed.name, config.build_id, consumer.id)
        pkey = (seed.name, config.build_id)
        base = self._build_dir(seed, config)
        if ckey not in self._consumers or pkey not in self._probes:
            consumers = (
                self._consumers[ckey]
                if ckey in self._consumers
                else build_consumers(
                    seed, consumer, old, base / f"consumers-{consumer.id}"
                )
            )
            probe = (
                self._probes[pkey]
                if pkey in self._probes
                else run_probe(
                    seed,
                    config,
                    config.flags,
                    old.dir,
                    dict(seed.roles),
                    base / "probe-old",
                )
            )
            # Publish both only once both succeeded, so a failure is retried.
            self._consumers[ckey], self._probes[pkey] = consumers, probe
        return self._consumers[ckey], self._probes[pkey]

    def truth(
        self,
        seed: Seed,
        config: Config,
        old: Built,
        new: Built,
        new_roles: dict[str, RoleText],
        new_flags: Flags,
        tag: str,
        blocked: Sequence[Blocked] = (),
    ) -> ExecTruth:
        reason = execution_unavailable(config)
        if reason:
            return ExecTruth(available=False, reason=reason)
        skip = {c: (status, why) for c, status, why in blocked}
        pairs: list[PairTruth] = []
        old_probe: dict[str, str] | None = None
        for consumer in config.consumer_set:
            if consumer in skip:
                status, why = skip[consumer]
                pairs.append(PairTruth(consumer.id, status, reason=why))
                continue
            consumers, old_probe = self._old(seed, config, old, consumer)
            pairs.append(self._pair(seed, consumer, consumers, new))
        if old_probe is None:
            return ExecTruth(
                available=False,
                reason="no consumer can run against this build: "
                + "; ".join(f"{p.consumer}: {p.reason}" for p in pairs),
                pairs=tuple(pairs),
            )
        signals = [sig for p in pairs for sig in p.signals]
        binary_break = any(p.binary_break for p in pairs)
        source_break = any(p.source_break for p in pairs)
        roles = {**dict(seed.roles), **new_roles}
        new_probe = run_probe(
            seed,
            config,
            new_flags,
            new.dir,
            roles,
            self._build_dir(seed, config) / f"probe-{tag}",
        )
        for k in sorted(set(old_probe) & set(new_probe)):
            if old_probe[k] != new_probe[k]:
                binary_break = True
                signals.append(f"layout {k}: {old_probe[k]} -> {new_probe[k]}")
        return ExecTruth(
            available=True,
            binary_break=binary_break,
            source_break=source_break,
            signals=tuple(signals),
            layout_only=binary_layout_only(binary_break, signals),
            pairs=tuple(pairs),
        )

    def _pair(
        self,
        seed: Seed,
        consumer: ResolvedConsumer,
        consumers: ConsumerSet,
        new: Built,
    ) -> PairTruth:
        """Run one consumer's old-header programs against *new*; recompile them on it."""
        signals: list[str] = []
        binary_break = source_break = False
        tag = f"[{consumer.id}]"
        for shape, exe in consumers.exes.items():
            got = _run(exe, new.dir)
            want = consumers.baseline[shape]
            if got.key() != want.key():
                binary_break = True
                signals.append(f"run {shape} {tag}: {_describe(want, got)}")
        for shape, exe in consumers.exes.items():
            src = exe.with_suffix(f".{_ext(seed.lang)}")
            cmd = compile_cmd(
                consumer.toolchain, consumer.std, consumer.flags, debug=False
            )
            cmd += ["-w", "-fsyntax-only", "-I", str(new.dir), str(src)]
            proc = _compile(cmd, f"new-header compile of {shape} {tag}")
            if compiler_crashed(proc):
                # A crashed or killed compiler says nothing about the source.
                raise CompileError(
                    f"new-header compile of {shape} {tag} crashed (exit {proc.returncode}): "
                    f"{' '.join(cmd)}\n--- {src.name} ---\n"
                    f"{src.read_text(encoding='utf-8')}--- stderr ---\n{proc.stderr}"
                )
            if proc.returncode != 0:
                source_break = True
                err = next(
                    (ln for ln in proc.stderr.splitlines() if "error" in ln),
                    proc.stderr[:120],
                )
                signals.append(
                    f"source {shape} {tag}: {err.split(': ', 1)[-1].strip()}"
                )
        return PairTruth(
            consumer.id,
            "ran",
            binary_break=binary_break,
            source_break=source_break,
            signals=tuple(signals),
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
