"""Build old/new libraries from a seed and an operator, then run abicheck on them.

Only the public entry points are used: ``abicheck.dumper.dump`` for the
snapshots and ``abicheck.checker.compare`` for the verdict. A compiler failure
raises ``CompileError``; it is never converted into a skip, because a corpus
case that did not build proved nothing.
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from abicheck.checker import compare
from abicheck.dumper import dump
from tests._edit_corpus_operators import VERDICT_RANK, EditOp
from tests._edit_corpus_seeds import Seed

# compiler label -> (C driver, C++ driver)
COMPILERS: dict[str, tuple[str, str]] = {
    "gcc": ("gcc", "g++"),
    "clang": ("clang", "clang++"),
}


class CompileError(RuntimeError):
    """A configured compiler ran and rejected a corpus source. Always a failure."""


@dataclass(frozen=True)
class Built:
    so: Path
    header: Path
    snapshot: object  # AbiSnapshot; typed loosely to keep this module import-light


@dataclass(frozen=True)
class Outcome:
    verdict: str
    kinds: frozenset[str]
    failures: tuple[str, ...]


def _compile(
    seed: Seed, compiler: str, out_dir: Path, header_text: str, source_text: str
) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / seed.header_name).write_text(header_text, encoding="utf-8")
    src = out_dir / seed.source_name
    src.write_text(source_text, encoding="utf-8")
    cc, cxx = COMPILERS[compiler]
    driver = cxx if seed.lang == "c++" else cc
    so = out_dir / "libedit.so"
    cmd = [
        driver,
        "-shared",
        "-fPIC",
        "-g",
        "-O0",
        "-I",
        str(out_dir),
        "-o",
        str(so),
        str(src),
    ]
    if seed.lang == "c++":
        cmd.insert(1, "-std=c++17")
    # Give old and new the same install name / SONAME. Otherwise the default is
    # derived from the output path (macOS) and the two sides differ only by their
    # build directory, which reads as a spurious SONAME_CHANGED.
    if sys.platform == "darwin":
        cmd.insert(1, "-Wl,-install_name,libedit.so")
    else:
        cmd.insert(1, "-Wl,-soname,libedit.so")
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise CompileError(
            f"{' '.join(cmd)} failed (exit {proc.returncode}):\n"
            f"--- source ---\n{source_text}\n--- stderr ---\n{proc.stderr}"
        )
    return so


def build(
    seed: Seed, compiler: str, out_dir: Path, overrides: dict | None = None
) -> Built:
    """Render *seed* (with *overrides* applied), compile it and snapshot the library."""
    header_text, source_text = seed.render(overrides)
    so = _compile(seed, compiler, out_dir, header_text, source_text)
    header = out_dir / seed.header_name
    # Pin the header frontend and the language standard so the dump reads the
    # header the way the library was built, whatever the environment selects.
    is_cxx = seed.lang == "c++"
    snap = dump(
        so,
        [header],
        version=out_dir.name,
        compiler="c++" if is_cxx else "cc",
        lang="c++" if is_cxx else "c",
        header_backend="castxml",
        gcc_options="-std=c++17" if is_cxx else None,
    )
    return Built(so=so, header=header, snapshot=snap)


class BaselineCache:
    """Old-side builds are identical for every operator on a (seed, compiler); build once."""

    def __init__(self, root: Path) -> None:
        self._root = root
        self._cache: dict[tuple[str, str], Built] = {}

    def get(self, seed: Seed, compiler: str) -> Built:
        key = (seed.name, compiler)
        if key not in self._cache:
            self._cache[key] = build(
                seed, compiler, self._root / f"{seed.name}-{compiler}-old"
            )
        return self._cache[key]


def run_operator(
    op: EditOp, seed: Seed, compiler: str, cache: BaselineCache, root: Path
) -> Outcome:
    old = cache.get(seed, compiler)
    new_roles = op.edit(seed.roles)
    new = build(
        seed, compiler, root / f"{seed.name}-{compiler}-{op.name}", overrides=new_roles
    )
    result = compare(old.snapshot, new.snapshot)
    verdict = result.verdict.name
    kinds = frozenset(c.kind.name for c in result.changes)
    return Outcome(
        verdict=verdict, kinds=kinds, failures=tuple(check(op, verdict, kinds))
    )


def check(op: EditOp, verdict: str, kinds: frozenset[str]) -> list[str]:
    """Return the ways the observed result departs from the operator's ground truth."""
    problems: list[str] = []
    missing = sorted(op.expected - kinds)
    if missing:
        problems.append(f"missing expected kinds {missing}")
    for group in op.any_of:
        if not (group & kinds):
            problems.append(f"none of {sorted(group)} reported")
    rank = VERDICT_RANK[verdict]
    if rank < VERDICT_RANK[op.verdict_min]:
        problems.append(f"verdict {verdict} weaker than required {op.verdict_min}")
    if op.verdict_max is not None and rank > VERDICT_RANK[op.verdict_max]:
        problems.append(
            f"verdict {verdict} stronger than benign maximum {op.verdict_max}"
        )
    return problems
