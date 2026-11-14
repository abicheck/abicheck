"""Build old/new libraries from a seed, an operator and a ``Config``; ask the checker.

The checker under test is reached only through its adapter
(``abiforge.checkers.abicheck``: ``snapshot`` and ``answer``). The evidence axis
decides what the dump is given: ``binary`` builds without ``-g`` and passes no
header, ``dwarf`` builds with ``-g`` and passes no header, ``headers`` adds the
public header (dumped by ``config.header_backend``: castxml, clang or the
native gcc plugin, with the standard and the layout-changing flags the
library was built with). A compiler failure raises ``CompileError``; it is
never converted into a skip, because a corpus case that did not build proved
nothing.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from abiforge.checkers import abicheck as checker
from abiforge.model.config import (
    Config,
    Flags,
    Standard,
    Toolchain,
    default_config,
)
from abiforge.model.seeds import BuildArgs, RoleText, Seed
from abiforge.operators import EditOp
from abiforge.run.toolchains import std_flag

# compiler label -> (C driver, C++ driver): the base PR's two fixed lanes.
COMPILERS: dict[str, tuple[str, str]] = {
    "gcc": ("gcc", "g++"),
    "clang": ("clang", "clang++"),
}

LIB_NAME = "libedit.so"
#: A ``build_data`` config's L3 record of the library build.
COMPILE_DB = "compile_commands.json"


class CompileError(RuntimeError):
    """A configured compiler ran and rejected a corpus source. Always a failure."""


@dataclass(frozen=True)
class Built:
    dir: Path
    so: Path
    header: Path
    snapshot: object  # AbiSnapshot; typed loosely to keep this module import-light


@dataclass(frozen=True)
class LinkSpec:
    """Per-build link options, for an edit that changes how the library links.

    *flags* follow the harness's own (``-shared``, ``-fPIC``, ``-soname``), so a
    later ``-Wl,-soname,...`` wins; *files* (``(name, text)``: a version
    script, a linker script) are written beside the library first. ``{dir}``
    in a flag is replaced by that build directory.
    """

    flags: tuple[str, ...] = ()
    files: tuple[tuple[str, str], ...] = ()

    def render(self, out_dir: Path) -> list[str]:
        for name, text in self.files:
            (out_dir / name).write_text(text, encoding="utf-8")
        return [f.replace("{dir}", str(out_dir)) for f in self.flags]


def _seed_link(extra: BuildArgs) -> LinkSpec:
    """A seed's build roles as link options: its version script and ldflags."""
    files: tuple[tuple[str, str], ...] = ()
    flags: tuple[str, ...] = ()
    if extra.vscript:
        files = (("edit.map", extra.vscript + "\n"),)
        flags = ("-Wl,--version-script={dir}/edit.map",)
    return LinkSpec(flags=(*flags, *extra.ldflags), files=files)


@dataclass(frozen=True)
class Outcome:
    verdict: str
    kinds: frozenset[str]
    failures: tuple[str, ...]


def label_toolchain(label: str) -> Toolchain:
    """The plain-``PATH`` driver pair of a base-PR compiler label as a Toolchain."""
    from abiforge.run.toolchains import _identify

    cc, cxx = COMPILERS[label]
    ident = _identify(cc) if shutil.which(cc) else None
    family, version = ident if ident else (label, 0)
    return Toolchain(family, version, cc, cxx)


def compile_cmd(
    tc: Toolchain, std: Standard, flags: Flags, *, debug: bool
) -> list[str]:
    """Driver + standard + ABI flags shared by library, consumer and probe builds."""
    spelling = std_flag(tc, std) or std.name
    cmd = [tc.driver(std.lang), f"-std={spelling}", *flags.render(tc, std.lang)]
    if debug:
        cmd.append("-g")
    return cmd


def dump_std_spelling(config: Config) -> str:
    """A ``-std=`` spelling the header frontend's compiler accepts.

    castxml emulates the system ``cc``/``c++`` (gcc on Linux), not the
    producer, so ``c23`` from clang must be spelled ``c2x`` for gcc 13.
    """
    from abiforge.run.toolchains import std_flag_for_driver

    driver = "c++" if config.lang == "c++" else "cc"
    return (
        std_flag_for_driver(driver, config.std)
        or std_flag(config.producer, config.std)
        or config.std.name
    )


def _check(cmd: list[str], proc: subprocess.CompletedProcess[str], what: str) -> None:
    if proc.returncode != 0:
        raise CompileError(
            f"{' '.join(cmd)} failed (exit {proc.returncode}):\n"
            f"--- {what} ---\n--- stderr ---\n{proc.stderr}"
        )


def _compile_library(
    seed: Seed,
    config: Config,
    flags: Flags,
    out_dir: Path,
    header_text: str,
    source_text: str,
    link: LinkSpec = LinkSpec(),
    extra: BuildArgs | None = None,
) -> Path:
    extra = extra or BuildArgs()
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / seed.header_name).write_text(header_text, encoding="utf-8")
    src = out_dir / seed.source_name
    src.write_text(source_text, encoding="utf-8")
    so = out_dir / LIB_NAME
    cmd = compile_cmd(
        config.producer, config.std, flags, debug=config.evidence != "binary"
    )
    cmd += [*extra.cflags, "-shared", "-I", str(out_dir), "-o", str(so), str(src)]
    # PIC is an ELF/Mach-O concept: clang rejects -fPIC for the Windows target.
    if sys.platform != "win32":
        cmd.append("-fPIC")
    # Give old and new the same install name / SONAME. Otherwise the default is
    # derived from the output path (macOS) and the two sides differ only by their
    # build directory, which reads as a spurious SONAME_CHANGED.
    if sys.platform == "darwin":
        cmd.append(f"-Wl,-install_name,{LIB_NAME}")
    elif sys.platform.startswith("linux"):
        # A seed's build.soname role overrides the default; "" means no
        # DT_SONAME at all, which a later -soname flag cannot express.
        soname = LIB_NAME if extra.soname is None else extra.soname
        if soname:
            cmd.append(f"-Wl,-soname,{soname}")
    cmd += _seed_link(extra).render(out_dir)
    cmd += link.render(out_dir)
    if config.build_data or config.sources:
        # The L3 record of this build: the one command that compiled it. A
        # ``sources`` tree carries it the way a real checkout's build dir
        # does (``dump --sources`` auto-discovers it for L4 replay).
        entry = {"directory": str(out_dir), "file": str(src), "arguments": cmd}
        (out_dir / COMPILE_DB).write_text(json.dumps([entry]), encoding="utf-8")
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise CompileError(
            f"{' '.join(cmd)} failed (exit {proc.returncode}):\n"
            f"--- source ---\n{source_text}\n--- stderr ---\n{proc.stderr}"
        )
    return so


def build(
    seed: Seed,
    config: Config,
    out_dir: Path,
    overrides: dict[str, RoleText] | None = None,
    flags: Flags | None = None,
    link: LinkSpec = LinkSpec(),
) -> Built:
    """Render *seed* (with *overrides*), compile it under *config* and snapshot it.

    *flags* replaces the config's producer flags (a rebuild edit's new side);
    *link* adds link options (an edit's version script, SONAME, extra libs).
    """
    fl = flags or config.flags
    header_text, source_text = seed.render(overrides)
    so = _compile_library(
        seed,
        config,
        fl,
        out_dir,
        header_text,
        source_text,
        link,
        seed.build_args(overrides),
    )
    header = out_dir / seed.header_name
    is_cxx = seed.lang == "c++"
    headers = [header] if config.evidence == "headers" else []
    std = dump_std_spelling(config)
    # The header frontend reads the header the way the library was built:
    # same standard, same layout-changing flags.
    # A build role's macro definitions (-D/-U) change what the header
    # declares (``_TIME_BITS=64`` widens time_t), so the dump gets them too.
    defines = [
        f for f in seed.build_args(overrides).cflags if f.startswith(("-D", "-U"))
    ]
    gcc_options = " ".join(
        [f"-std={std}", *fl.layout_flags(config.producer, seed.lang), *defines]
    )
    snap = checker.snapshot(
        so,
        headers,
        version=out_dir.name,
        is_cxx=is_cxx,
        backend=config.header_backend,
        gcc_options=gcc_options,
        build_info=out_dir / COMPILE_DB if config.build_data else None,
        sources=out_dir if config.sources else None,
    )
    return Built(dir=out_dir, so=so, header=header, snapshot=snap)


class BaselineCache:
    """Old-side builds are identical for every operator on a (seed, build); build once.

    Keyed by ``Config.build_id``: the consumers a config names never change
    the library, so configs that differ only in their consumers share it.
    """

    def __init__(self, root: Path) -> None:
        self._root = root
        self._cache: dict[tuple[str, str], Built] = {}

    def get(self, seed: Seed, config: Config | str) -> Built:
        cfg = _as_config(seed, config)
        key = (seed.name, cfg.build_id)
        if key not in self._cache:
            self._cache[key] = build(
                seed, cfg, self._root / f"{seed.name}-{cfg.build_id}-old"
            )
        return self._cache[key]


class NewBuildCache:
    """New-side builds per (operator, seed, build): reused across consumer sets."""

    def __init__(self, root: Path) -> None:
        self._root = root
        self._cache: dict[tuple[str, str, str], Built] = {}

    def get(self, op: EditOp, seed: Seed, config: Config) -> Built:
        key = (op.name, seed.name, config.build_id)
        if key not in self._cache:
            self._cache[key] = build_new(op, seed, config, self._root)
        return self._cache[key]


def _as_config(seed: Seed, config: Config | str) -> Config:
    if isinstance(config, Config):
        return config
    return default_config(seed.lang, label_toolchain(config))


def build_new(
    op: EditOp, seed: Seed, config: Config, root: Path, link: LinkSpec = LinkSpec()
) -> Built:
    """The edited (new) side; *link* is the edit's own link options, if any."""
    return build(
        seed,
        config,
        root / f"{seed.name}-{config.build_id}-{op.name}",
        overrides=op.edit(seed.roles),
        flags=op.new_flags(config.flags),
        link=link,
    )


# abicheck's answer when it refuses to compare the two snapshots at all.
REFUSED = "REFUSED"


def abicheck_answer(old: Built, new: Built) -> tuple[str, frozenset[str]]:
    """(verdict name, ChangeKind names) from the checker adapter; ``REFUSED``
    plus the error type when the checker refuses to compare."""
    return checker.answer(old.snapshot, new.snapshot)


def run_operator(
    op: EditOp, seed: Seed, config: Config | str, cache: BaselineCache, root: Path
) -> Outcome:
    """Build and compare one cell; check it against ``op.expect(config)``."""
    cfg = _as_config(seed, config)
    old = cache.get(seed, cfg)
    new = build_new(op, seed, cfg, root)
    verdict, kinds = abicheck_answer(old, new)
    return Outcome(
        verdict=verdict,
        kinds=kinds,
        failures=tuple(op.expect(cfg).problems(verdict, kinds)),
    )


def check(op: EditOp, verdict: str, kinds: frozenset[str]) -> list[str]:
    """How the observed result departs from the operator's base (default-config) truth."""
    return op.base().problems(verdict, kinds)
