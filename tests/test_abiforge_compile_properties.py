"""Both sides of every edit compile, for every standard the seed declares (plan §6.1(b)).

The compiler is the oracle here: a side that does not compile is an operator
defect, whatever abicheck says. The commands are the harness's own
(``abiforge.run.harness.compile_cmd`` and ``std_flag``), so the check covers the
flags a real cell is built with. Syntax only (``-fsyntax-only``): this is the
property "the edit is valid C/C++ under this standard", and linking is covered
by the corpus cells.

A standard the host compiler has no ``-std=`` spelling for, or a host capability
``capability_gap`` reports, is the explicit unavailable case the harness itself
uses; it is reported with its reason, never silently dropped.

Marked ``slow``: about two thousand compiler invocations.
"""

from __future__ import annotations

import subprocess
import tempfile
from collections.abc import Iterable
from pathlib import Path

import pytest

from abiforge.model.config import STANDARDS, Config, Flags, Standard
from abiforge.model.opkit import EditOp
from abiforge.model.seeds import SEEDS, RoleText, Seed
from abiforge.operators import applicable
from abiforge.run.harness import compile_cmd
from abiforge.run.toolchains import Toolchain, capability_gap, std_flag

pytestmark = pytest.mark.slow

GCC = Toolchain("gcc", 0, "gcc", "g++")
COMPILE_TIMEOUT = 120


def _write_tree(
    root: Path, seed: Seed, overrides: dict[str, RoleText] | None
) -> list[Path]:
    """Write one side's header, source and extra files; return its translation units."""
    header, source = seed.render(overrides)
    (root / seed.header_name).write_text(header, encoding="utf-8")
    src = root / seed.source_name
    src.write_text(source, encoding="utf-8")
    units = [src]
    for rel, text in seed.render_extra(overrides).items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        if rel in seed.extra_sources:
            units.append(target)
    return units


def _syntax_check(
    seed: Seed,
    config: Config,
    overrides: dict[str, RoleText] | None,
    cflags: Iterable[str],
    flags: Flags,
) -> str | None:
    """None when the side compiles; otherwise the compiler's first error lines."""
    with tempfile.TemporaryDirectory(prefix="abiforge-compile-") as tmp:
        root = Path(tmp)
        units = _write_tree(root, seed, overrides)
        cmd = compile_cmd(config.producer, config.std, flags, debug=False)
        cmd += [*cflags, "-fsyntax-only", "-I", str(root), *(str(u) for u in units)]
        proc = subprocess.run(
            cmd, capture_output=True, text=True, check=False, timeout=COMPILE_TIMEOUT
        )
        if proc.returncode == 0:
            return None
        return "\n".join(proc.stderr.splitlines()[:4])


def _config(std: Standard) -> Config:
    return Config(std=std, producer=GCC)


def _spelled(std: Standard) -> bool:
    return std_flag(GCC, std) is not None


SEED_STANDARDS = [
    (seed, std) for seed in SEEDS for std in STANDARDS if std.lang == seed.lang
]


# Evidence settings an edit may need (``EditOp.requires``). Compilation does not
# depend on them, so the first candidate that admits an edit is the one it runs
# under; source-level edits need the clang backend's source replay.
_EVIDENCE_CANDIDATES: tuple[dict[str, object], ...] = (
    {},
    {"sources": True, "header_backend": "clang"},
    {"build_data": True},
    {"evidence": "dwarf"},
    {"evidence": "binary"},
)


def _config_for(op: EditOp, std: Standard) -> Config | None:
    """The first evidence configuration under *std* that *op* declares meaningful."""
    for extra in _EVIDENCE_CANDIDATES:
        config = Config(std=std, producer=GCC, **extra)  # type: ignore[arg-type]
        if op.requires(config) is None:
            return config
    return None


@pytest.mark.parametrize(
    ("seed", "std"), SEED_STANDARDS, ids=lambda p: getattr(p, "name", p)
)
def test_both_sides_compile_for_every_applicable_edit(
    seed: Seed, std: Standard
) -> None:
    if not _spelled(std):
        pytest.skip(
            f"{GCC.id} has no -std= spelling for {std.name}: a host capability gap"
        )
    failures: list[str] = []
    checked = 0
    unavailable: list[str] = []
    for op in applicable(seed):
        config = _config_for(op, std)
        if config is None:
            unavailable.append(
                f"{op.name}: no evidence configuration admits it under {std.name}"
            )
            continue
        gap = capability_gap(op, config)
        if gap is not None:
            unavailable.append(f"{op.name}: {gap}")
            continue
        checked += 1
        failures.extend(_side_failures(op, seed, config))
    assert checked, (
        f"no edit was checked for {seed.name} under {std.name}: {unavailable}"
    )
    assert failures == [], "\n\n".join(failures)


def _side_failures(op: EditOp, seed: Seed, config: Config) -> list[str]:
    """Compile failures for the new side of *op* (the base side is checked above)."""
    roles = dict(seed.roles)
    overrides = op.edit(roles)
    new_flags = op.new_flags(config.flags)
    new_cflags = seed.build_args(overrides or None).cflags
    error = _syntax_check(seed, config, overrides or None, new_cflags, new_flags)
    if error is None:
        return []
    return [
        f"{op.name} on {seed.name} under {config.std.name}: new side fails\n{error}"
    ]
