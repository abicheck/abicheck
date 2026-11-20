"""Build old/new libraries from a seed, an operator and a ``Config``; ask the checker.

The checker under test is reached only through its adapter
(``abiforge.checkers.abicheck``: ``snapshot`` and ``answer``). The evidence set
decides what the dump is given. A build carries DWARF (``-g``) only when ``D``
is in it, so ``{B}`` and ``{B,H}`` are stripped binaries (checked after the
link: no ``.debug_*`` section may remain). ``H`` adds the public header, dumped
by ``config.header_backend`` (castxml, clang or the native gcc plugin) with
``-std``, ``-I`` and the public ``-D``/``-U``. The layout-changing flags the
library was built with go to the dumper only in a cell that records the build
(``D`` or ``BD``): a distributed header records none of them. A compiler failure
raises ``CompileError``; it is never converted into a skip, because a corpus
case that did not build proved nothing.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, replace
from pathlib import Path

from abiforge.checkers import abicheck as checker
from abiforge.model.config import (
    Config,
    Flags,
    Standard,
    Toolchain,
    default_config,
)
from abiforge.model.finding import Finding
from abiforge.model.seeds import BuildArgs, RoleText, Seed
from abiforge.operators import EditOp
from abiforge.run.binfacts import binary_facts
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
    #: Function-level findings, named per symbol; filled only for a failing cell.
    findings: tuple[str, ...] = ()
    #: Per-side debug evidence and dSYM presence; filled only for a failing cell.
    evidence: tuple[str, ...] = ()


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


#: Per object format (ELF, PE): the tool that lists a library's sections, and
#: the pattern that picks out its DWARF section names from that tool's output.
_SECTION_LISTING: dict[str, tuple[list[str], str]] = {
    # ELF: readelf's section table, ``.debug_*`` names.
    "elf": (["readelf", "-SW"], r"\]\s+(\.debug\S*)"),
    # PE/COFF (mingw): objdump's section table; DWARF keeps its ``.debug_*`` names.
    "pe": (["objdump", "-h"], r"^\s+\d+\s+(\.debug_\S+)"),
}


def object_format() -> str:
    """The object format of this host's libraries (``elf``, ``pe`` or ``macho``)."""
    if sys.platform == "darwin":
        return "macho"
    if sys.platform == "win32":
        return "pe"
    return "elf"


def _listing(cmd: list[str], what: str) -> str:
    """The stdout of a listing tool. A tool that fails or prints nothing raises:
    a failed listing is never read as "no DWARF". A missing tool raises
    ``FileNotFoundError`` from ``subprocess``, which is never caught here."""
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    _check(cmd, proc, what)
    if not proc.stdout.strip():
        raise CompileError(f"{' '.join(cmd)} printed no {what}")
    return proc.stdout


def debug_sections(so: Path) -> list[str]:
    """The DWARF a built library still carries, named in its own format.

    ELF and PE: the ``.debug_*`` sections. Mach-O: the ``__debug_*`` sections of
    the ``__DWARF`` segment, and the N_OSO debug-map stabs that a ``-g`` link
    leaves in the image, each naming the object file that holds the DWARF.
    """
    if object_format() == "macho":
        sections = _listing(["otool", "-l", str(so)], "section table")
        stabs = _listing(["nm", "-a", "-p", str(so)], "symbol table")
        names = re.findall(r"sectname\s+(__debug_\S+)", sections)
        names += [f"OSO {o}" for o in re.findall(r"\bOSO\b\s+(\S.*)$", stabs, re.M)]
        return names
    listing, pattern = _SECTION_LISTING[object_format()]
    return re.findall(pattern, _listing([*listing, str(so)], "section table"), re.M)


def strip_debug(so: Path) -> None:
    """Strip DWARF from a built ELF library, then prove none is left.

    A ``{B}`` or ``{B,H}`` side must be a stripped binary: a leftover
    ``.debug_*`` section would hand the checker DWARF the cell does not claim.
    ELF and PE are stripped with binutils ``strip`` (mingw's on PE: a link
    without ``-g`` still pulls DWARF in from the toolchain's CRT and libgcc
    objects). Every format is checked afterwards; a section that survives
    the strip fails the build.
    """
    if object_format() in ("elf", "pe"):
        cmd = ["strip", "--strip-debug", str(so)]
        _check(
            cmd,
            subprocess.run(cmd, capture_output=True, text=True, check=False),
            "strip",
        )
    left = debug_sections(so)
    if left:
        raise CompileError(f"{so} still has DWARF after strip --strip-debug: {left}")


def _compile_library(
    seed: Seed,
    config: Config,
    flags: Flags,
    out_dir: Path,
    header_text: str,
    source_text: str,
    link: LinkSpec = LinkSpec(),
    extra: BuildArgs | None = None,
    extra_texts: dict[str, str] | None = None,
) -> Path:
    extra = extra or BuildArgs()
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / seed.header_name).write_text(header_text, encoding="utf-8")
    src = out_dir / seed.source_name
    src.write_text(source_text, encoding="utf-8")
    for rel, text in (extra_texts or {}).items():
        (out_dir / rel).parent.mkdir(parents=True, exist_ok=True)
        (out_dir / rel).write_text(text, encoding="utf-8")
    units = [src, *(out_dir / rel for rel in seed.extra_sources)]
    so = out_dir / LIB_NAME
    debug = "D" in config.evidence_set
    cmd = compile_cmd(config.producer, config.std, flags, debug=debug)
    # A Mach-O debug link keeps DWARF in the object files it names (the debug
    # map), and clang deletes those temporaries after a one-step link. So the
    # units are compiled to objects that stay on disk, and dsymutil reads them.
    macho_debug = debug and object_format() == "macho"
    link_inputs = (
        _compile_macho_objects(cmd, extra, out_dir, units) if macho_debug else units
    )
    cmd += [*extra.cflags, "-shared", "-I", str(out_dir), "-o", str(so)]
    cmd += [str(u) for u in link_inputs]
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
        # does (``dump --sources`` auto-discovers it for L4 replay). The
        # compiler runs in this process's cwd, so that is the directory the
        # commands' relative paths resolve against (out_dir may be relative).
        # One entry per translation unit, each the command compiling only it.
        linked = {str(u) for u in link_inputs}
        lead = [a for a in cmd if a not in linked]
        entries = [
            {"directory": str(Path.cwd()), "file": str(u), "arguments": [*lead, str(u)]}
            for u in units
        ]
        (out_dir / COMPILE_DB).write_text(json.dumps(entries), encoding="utf-8")
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise CompileError(
            f"{' '.join(cmd)} failed (exit {proc.returncode}):\n"
            f"--- source ---\n{source_text}\n--- stderr ---\n{proc.stderr}"
        )
    if not debug:
        strip_debug(so)
    elif macho_debug:
        make_dsym(so)
    return so


def _compile_macho_objects(
    base: list[str], extra: BuildArgs, out_dir: Path, units: list[Path]
) -> list[Path]:
    """Compile each translation unit to an object file that outlives the link."""
    objects: list[Path] = []
    for unit in units:
        # Keyed by the unit's path under out_dir, so two units that share a
        # basename in different directories keep distinct objects.
        obj = out_dir / f"{unit.relative_to(out_dir)}.o"
        obj.parent.mkdir(parents=True, exist_ok=True)
        cmd = [*base, *extra.cflags, "-c", "-fPIC", "-I", str(out_dir), str(unit)]
        cmd += ["-o", str(obj)]
        _check(
            cmd,
            subprocess.run(cmd, capture_output=True, text=True, check=False),
            "compile",
        )
        objects.append(obj)
    return objects


def make_dsym(so: Path) -> Path:
    """Collect a Mach-O debug-map link's DWARF into ``<so>.dSYM``, the bundle
    abicheck resolves next to the dylib. A missing bundle is a build failure:
    a cell that claims DWARF must not be checked without it."""
    dsym = so.with_name(so.name + ".dSYM")
    cmd = ["dsymutil", str(so), "-o", str(dsym)]
    _check(
        cmd,
        subprocess.run(cmd, capture_output=True, text=True, check=False),
        "dsymutil",
    )
    if not dsym.is_dir():
        raise CompileError(f"dsymutil wrote no {dsym} for {so}")
    return dsym


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
    The config is this side's: an asymmetric-evidence edit passes its new side's
    config (``EditOp.new_side_config``).
    """
    fl = flags or config.flags
    if config.sources:
        # A source tree is laid out the way a project is: under a project
        # directory (``src/``), so a path read from either side's tree is
        # comparable across the two checkouts (abicheck anchors a declaring
        # path at its last project-layout segment). Flat in ``old/`` and
        # ``new/``, every header path differed by its checkout root alone, and
        # L5 identity reconciliation could never pair a declaration across
        # the two sides.
        out_dir = out_dir / "src"
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
        seed.render_extra(overrides),
    )
    header = out_dir / seed.header_name
    is_cxx = seed.lang == "c++"
    headers = [header] if config.evidence == "headers" else []
    std = dump_std_spelling(config)
    # The header frontend gets what a distributed header carries: the standard,
    # the include directory, and the public macro definitions. A build role's
    # -D/-U changes what the header declares (``_TIME_BITS=64`` widens time_t),
    # so those reach the dump. The layout-changing flags (-m32, -fpack-struct,
    # the producer's -D) are not in a distributed header. They reach the dump
    # only in a cell whose evidence records the build: DWARF (``D``) or build
    # data (``BD``). A stripped ``{B,H}`` side gets none of them, which is the
    # primary configuration's real view of the header.
    defines = [
        f for f in seed.build_args(overrides).cflags if f.startswith(("-D", "-U"))
    ]
    options = [f"-std={std}", f"-I{out_dir}", *defines]
    if {"D", "BD"} & config.evidence_set.atoms:
        options += fl.layout_flags(config.producer, seed.lang)
    gcc_options = " ".join(options)
    snap = checker.snapshot(
        so,
        headers,
        version=out_dir.name,
        is_cxx=is_cxx,
        backend=config.header_backend,
        gcc_options=gcc_options,
        build_info=out_dir / COMPILE_DB if config.build_data else None,
        sources=out_dir if config.sources else None,
        follow_deps=config.follow_deps,
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
        op.new_side_config(config),
        root / f"{seed.name}-{config.build_id}-{op.name}",
        overrides=op.edit(seed.roles),
        flags=op.new_flags(config.flags),
        link=link,
    )


# abicheck's answer when it refuses to compare the two snapshots at all.
REFUSED = "REFUSED"


def abicheck_judgement(
    old: Built, new: Built
) -> tuple[str, frozenset[str], frozenset[str], tuple[Finding, ...]]:
    """(verdict, kinds, disclosed kinds, structured findings) from the checker adapter."""
    return checker.judged(old.snapshot, new.snapshot)


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
    verdict, kinds, _disclosed, findings = abicheck_judgement(old, new)
    # The same binary facts run_cell reads: a licensed edit is judged alike on both paths.
    facts = binary_facts(old.so, new.so) if op.licences else {}
    exp = op.expect(cfg, facts)
    # ... and the same entity claims, witnesses included.
    from abiforge.run.witness import witness_claims

    witnesses, _dropped = witness_claims(op, seed, old.so, new.so)
    if witnesses:
        exp = replace(exp, claims=exp.claims + witnesses)
    failures = tuple(exp.problems(verdict, kinds, findings))
    if not failures:
        return Outcome(verdict=verdict, kinds=kinds, failures=failures)
    return Outcome(
        verdict=verdict,
        kinds=kinds,
        failures=failures,
        findings=checker.function_findings(old.snapshot, new.snapshot),
        evidence=(_side_evidence("old", old), _side_evidence("new", new)),
    )


def _side_evidence(side: str, built: Built) -> str:
    """One side's debug evidence in the snapshot, and whether its dSYM DWARF
    file is on disk where the Mach-O debug resolver looks for it."""
    dwarf_file = (
        built.so.with_name(built.so.name + ".dSYM")
        / "Contents/Resources/DWARF"
        / built.so.name
    )
    state = "present" if dwarf_file.is_file() else "absent"
    return (
        f"{side}: {checker.debug_evidence(built.snapshot)}; "
        f"dSYM DWARF {state} at {dwarf_file}"
    )


def check(op: EditOp, verdict: str, kinds: frozenset[str]) -> list[str]:
    """How the observed result departs from the operator's base (default-config) truth."""
    return op.base().problems(verdict, kinds)
