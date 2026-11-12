"""Toolchain registry for the generated ABI corpus: what this host can build.

``discover()`` finds the C/C++ compiler pairs on ``PATH`` (``gcc``/``g++``,
``gcc-N``/``g++-N``, ``clang``/``clang++``, ``clang-N``/``clang++-N``, Apple
clang, MSVC ``cl``), reads each one's family and major version from the driver
itself, and folds duplicates (``gcc`` and ``gcc-13`` resolving to one binary).

``build_toolchains()`` / ``consumer_toolchains()`` restrict a run to the
compilers named by ``ABICHECK_EDIT_CORPUS_TOOLCHAIN`` and
``ABICHECK_EDIT_CORPUS_CONSUMER_TOOLCHAIN`` (comma-separated, ``gcc-14``,
``clang19``, or a bare family for every installed major). A malformed or
uninstalled name raises ``ToolchainSelectionError``: a requested compiler
that is missing fails the run, it never shrinks it to whatever is installed.

``unavailable(config)`` answers *why* a config's library build cannot be
built here, or ``None``; ``consumer_unavailable(config, consumer)`` answers
the same for one consumer of that build. Each answer comes from probing the real driver: the standard
spelling (``-std=c23`` vs ``-std=c2x``), and whether the flag set builds both
a shared library and an executable (``-m32`` needs multilib). A cell whose
config is unavailable is reported unavailable/skipped, never passed.

MSVC and Apple clang are *modelled* (``Toolchain`` families, ``Flags`` renders
``/O``, ``/Zp``, ``/vd``) but not built: an MSVC or clang-on-Windows DLL
exports nothing without ``__declspec(dllexport)``, which the seeds do not
declare, so those configs are always unavailable here; Apple clang is
discovered, but the execution oracle is ELF-only.
Probe results are cached per process.
"""

from __future__ import annotations

import functools
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from tests._edit_corpus_config import (
    FAMILIES,
    Config,
    Flags,
    ResolvedConsumer,
    Standard,
    Toolchain,
    host_platform,
)

if TYPE_CHECKING:
    from tests._edit_corpus_seeds import Seed

_PROBE_TIMEOUT = 60

# Execution needs a dynamic loader we can redirect: LD_LIBRARY_PATH on ELF.
EXECUTION_PLATFORMS = ("elf",)


def _run(cmd: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        check=False,
        cwd=cwd,
        timeout=_PROBE_TIMEOUT,
    )


def _identify(cc: str) -> tuple[str, int] | None:
    """(family, major version) of a C driver, read from the driver."""
    try:
        proc = _run([cc, "--version"])
    except (OSError, subprocess.TimeoutExpired):
        return None
    text = proc.stdout + proc.stderr
    if "Apple clang" in text:
        family = "apple-clang"
    elif "clang version" in text:
        family = "clang"
    elif re.search(r"\b(gcc|GCC)\b", text) or "Free Software Foundation" in text:
        family = "gcc"
    else:
        return None
    m = re.search(r"version (\d+)\.", text) if family != "gcc" else None
    if family == "gcc":
        try:
            dv = _run([cc, "-dumpfullversion", "-dumpversion"]).stdout.strip()
        except (OSError, subprocess.TimeoutExpired):
            return None
        m = re.match(r"(\d+)", dv)
    if not m:
        return None
    return family, int(m.group(1))


def _candidates() -> list[tuple[str, str]]:
    pairs = [("gcc", "g++"), ("clang", "clang++")]
    for n in range(4, 31):
        pairs.append((f"gcc-{n}", f"g++-{n}"))
        pairs.append((f"clang-{n}", f"clang++-{n}"))
    return pairs


@functools.lru_cache(maxsize=1)
def discover() -> tuple[Toolchain, ...]:
    """Every distinct installed toolchain, sorted by (family, version)."""
    found: dict[tuple[str, int], Toolchain] = {}
    seen_bins: set[str] = set()
    for cc, cxx in _candidates():
        cc_path, cxx_path = shutil.which(cc), shutil.which(cxx)
        if cc_path is None or cxx_path is None:
            continue
        real = os.path.realpath(cc_path)
        if real in seen_bins:
            continue
        ident = _identify(cc_path)
        # The C++ driver must be the same family and major as the C one;
        # a gcc 13 / g++ 14 pair is not one toolchain (the g++-N pair is found
        # under its own name).
        if ident is None or _identify(cxx_path) != ident:
            continue
        # Only a validated pair claims its C driver: `gcc` (13) with a
        # mismatched `g++` must not hide `gcc-13`/`g++-13`, the same binary.
        seen_bins.add(real)
        found.setdefault(ident, Toolchain(ident[0], ident[1], cc_path, cxx_path))
    if sys.platform == "win32" and shutil.which("cl"):
        found.setdefault(("msvc", 0), Toolchain("msvc", 0, "cl", "cl"))
    return tuple(sorted(found.values()))


class ToolchainSelectionError(ValueError):
    """A toolchain selection names something malformed or not installed."""


TOOLCHAIN_ENV = "ABICHECK_EDIT_CORPUS_TOOLCHAIN"
CONSUMER_TOOLCHAIN_ENV = "ABICHECK_EDIT_CORPUS_CONSUMER_TOOLCHAIN"

# ``apple-clang`` first: the alternation is tried in order.
_SELECTOR = re.compile(r"(apple-clang|gcc|clang|msvc)(?:-?([0-9]+))?")


def parse_selection(
    raw: str, var: str = TOOLCHAIN_ENV
) -> tuple[tuple[str, int | None], ...]:
    """``"gcc-14, clang19,clang"`` -> ``(("gcc", 14), ("clang", 19), ("clang", None))``."""
    out: list[tuple[str, int | None]] = []
    for item in raw.split(","):
        name = item.strip()
        if not name:
            continue
        m = _SELECTOR.fullmatch(name)
        if m is None:
            raise ToolchainSelectionError(
                f"{var}: {name!r} is not a toolchain name; use FAMILY or FAMILY-MAJOR "
                f"with FAMILY in {', '.join(FAMILIES)} (e.g. gcc-14, clang-19, gcc)"
            )
        out.append((m.group(1), int(m.group(2)) if m.group(2) else None))
    return tuple(dict.fromkeys(out))


def select(
    raw: str, installed: Sequence[Toolchain], var: str = TOOLCHAIN_ENV
) -> tuple[Toolchain, ...]:
    """The *installed* toolchains *raw* names; every installed one when it is empty.

    A name that matches nothing installed raises: a selection that silently
    dropped a compiler would report a narrower run as the requested one.
    """
    wanted = parse_selection(raw, var)
    if not wanted:
        return tuple(installed)
    have = ", ".join(t.id for t in installed) or "none"
    picked: list[Toolchain] = []
    for family, version in wanted:
        hits = [
            t
            for t in installed
            if t.family == family and (version is None or t.version == version)
        ]
        if not hits:
            label = family if version is None else f"{family}-{version}"
            raise ToolchainSelectionError(
                f"{var} asks for {label}, which is not installed on this host "
                f"(installed: {have})"
            )
        picked.extend(hits)
    return tuple(sorted(dict.fromkeys(picked)))


_OPTIONS = {
    "--edit-corpus-toolchain": TOOLCHAIN_ENV,
    "--edit-corpus-consumer-toolchain": CONSUMER_TOOLCHAIN_ENV,
}


def add_pytest_options(parser: object) -> None:
    """Register the CLI spellings of the two selection variables (conftest)."""
    for opt, var in _OPTIONS.items():
        parser.addoption(  # type: ignore[attr-defined]
            opt,
            default=None,
            metavar="NAMES",
            help=f"Edit corpus: comma-separated toolchains (gcc-14,clang-19,gcc); sets {var}.",
        )


def apply_pytest_options(config: object) -> None:
    """Export the options as the env variables, validated, before collection.

    Runs in every xdist worker too, so workers see the same selection. A
    malformed name fails the session here, not at the first generated cell.
    """
    import pytest

    for opt, var in _OPTIONS.items():
        value = config.getoption(opt, default=None)  # type: ignore[attr-defined]
        if value is not None:
            try:
                # Names *and* installation: a missing compiler fails the session.
                select(value, discover(), var)
            except ToolchainSelectionError as exc:
                raise pytest.UsageError(f"{opt}: {exc}") from exc
            os.environ[var] = value


def build_toolchains() -> tuple[Toolchain, ...]:
    """The toolchains that may build libraries in this run."""
    return select(os.environ.get(TOOLCHAIN_ENV, ""), discover(), TOOLCHAIN_ENV)


def consumer_toolchains() -> tuple[Toolchain, ...]:
    """The toolchains that may build consumers; defaults to the build selection."""
    raw = os.environ.get(CONSUMER_TOOLCHAIN_ENV, "")
    if raw.strip():
        return select(raw, discover(), CONSUMER_TOOLCHAIN_ENV)
    return build_toolchains()


def toolchain(family: str, version: int) -> Toolchain:
    """The installed *family* *version*, or a placeholder ``unavailable()`` rejects."""
    for tc in discover():
        if tc.family == family and tc.version == version:
            return tc
    return Toolchain(family, version, f"<{family}-{version} not installed>", "")


def by_family(family: str) -> tuple[Toolchain, ...]:
    return tuple(t for t in discover() if t.family == family)


def newest(family: str) -> Toolchain | None:
    tcs = by_family(family)
    return tcs[-1] if tcs else None


@functools.cache
def std_flag(tc: Toolchain, std: Standard) -> str | None:
    """The ``-std=`` spelling *tc* accepts for *std*, or None if it has none."""
    if tc.family == "msvc":
        return None  # the corpus MSVC lane is not built; see unavailable()
    return std_flag_for_driver(tc.driver(std.lang), std)


@functools.cache
def std_flag_for_driver(driver: str, std: Standard) -> str | None:
    """The ``-std=`` spelling the *driver* executable accepts for *std*, or None."""
    if shutil.which(driver) is None:
        return None
    lang = "c++" if std.lang == "c++" else "c"
    for spelling in std.spellings:
        try:
            proc = subprocess.run(
                [driver, f"-std={spelling}", "-x", lang, "-fsyntax-only", "-"],
                input="int corpus_probe;\n",
                capture_output=True,
                text=True,
                check=False,
                timeout=_PROBE_TIMEOUT,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            # The driver is on PATH: one that cannot start or hangs is broken,
            # not missing the standard.
            raise ToolchainBroken(f"{driver} -std={spelling}: {exc}") from exc
        if compiler_crashed(proc):
            raise ToolchainBroken(
                f"{driver} -std={spelling} crashed (exit {proc.returncode}):\n{proc.stderr}"
            )
        if proc.returncode == 0:
            return spelling
        if not _rejects_flag(proc.stderr, f"-std={spelling}"):
            # Only a diagnostic rejecting this -std= spelling means the driver
            # lacks it; any other failure (a frontend that cannot start, a
            # missing assembler) is a broken compiler.
            raise ToolchainBroken(
                f"{driver} -std={spelling} failed without rejecting the flag "
                f"(exit {proc.returncode}):\n{proc.stderr or '(no stderr)'}"
            )
    return None


class ToolchainBroken(RuntimeError):
    """An installed toolchain failed to build the plain baseline probe.

    That is a failure, never a skip: a configured compiler that ran and
    failed proves the lane would test less than it claims.
    """


# What a crashed compiler prints even when its driver exits with a positive
# status: gcc's ICE banner and kill reports, clang's frontend crash report.
_CRASH = re.compile(
    r"internal compiler error|Killed signal|terminated program|Segmentation fault|"
    r"core dumped|signal \d+|killed|PLEASE submit a bug report|"
    r"frontend command failed due to signal|Stack dump:"
)


def compiler_crashed(proc: subprocess.CompletedProcess[str]) -> bool:
    """A compiler that died on a signal or printed a crash report crashed."""
    return proc.returncode < 0 or _CRASH.search(proc.stderr) is not None


@functools.cache
def driver_width(
    driver: str, lang: str, flags: tuple[str, ...], bits: int
) -> str | None:
    """None if *driver* with *flags* targets *bits*-bit pointers, else why not.

    Raises ``ToolchainBroken`` when the driver fails for any other reason.

    Used for drivers the harness does not build with but whose target matters,
    such as the system ``cc``/``c++`` the castxml header frontend emulates.
    """
    probe = f"typedef char edit_corpus_target_width[(sizeof(void *) * 8 == {bits}) ? 1 : -1];\n"
    try:
        proc = subprocess.run(
            [
                driver,
                *flags,
                "-x",
                "c++" if lang == "c++" else "c",
                "-fsyntax-only",
                "-",
            ],
            input=probe,
            capture_output=True,
            text=True,
            check=False,
            timeout=_PROBE_TIMEOUT,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ToolchainBroken(f"{driver} {' '.join(flags)}: {exc}") from exc
    if compiler_crashed(proc):
        raise ToolchainBroken(
            f"{driver} {' '.join(flags)} crashed: {proc.stderr.strip()[-400:]}"
        )
    if proc.returncode == 0:
        return None
    if "edit_corpus_target_width" in proc.stderr:
        return f"{driver} {' '.join(flags)} does not target {bits}-bit"
    # Any other failure on a one-line declaration is a broken driver, not a
    # missing capability.
    raise ToolchainBroken(
        f"{driver} {' '.join(flags)} fails a one-line syntax probe: {proc.stderr.strip()[-400:]}"
    )


def _failure_report(cmd: list[str], src: Path, what: str, stderr: str) -> str:
    """The command, the source it compiled and the whole stderr, for a failure."""
    return (
        f"{' '.join(cmd)} {what}\n"
        f"--- {src.name} ---\n{src.read_text(encoding='utf-8')}"
        f"--- stderr ---\n{stderr}"
    )


@functools.cache
def _flags_build(
    tc: Toolchain, lang: str, flags: Flags, check_width: bool = True
) -> str | None:
    """None if *flags* builds a shared library and links an executable, else why not.

    With *check_width*, the executable also asserts ``sizeof(void *)`` matches
    ``flags.bits``, so a compiler whose default is another width is reported,
    not silently tested at the wrong one.
    """
    ext = "cpp" if lang == "c++" else "c"
    width = (
        f"typedef char edit_corpus_target_width[(sizeof(void *) * 8 == {flags.bits}) ? 1 : -1];\n"
        if check_width
        else ""
    )
    with tempfile.TemporaryDirectory(prefix="edit-corpus-probe-") as d:
        tmp = Path(d)
        # Default visibility explicitly, as the seeds do, so -fvisibility=hidden links.
        (tmp / f"lib.{ext}").write_text(
            "#pragma GCC visibility push(default)\n"
            "int probe_fn(int x) { return x + 1; }\n"
            "#pragma GCC visibility pop\n"
        )
        (tmp / f"main.{ext}").write_text(
            width
            + (
                "int probe_fn(int x);\nint main(void) { return probe_fn(-1); }\n"
                if lang == "c"
                else "int probe_fn(int x);\nint main() { return probe_fn(-1); }\n"
            )
        )
        rendered = flags.render(tc, lang)
        drv = tc.driver(lang)
        lib = [drv, *rendered, "-shared", "-fPIC", "-o", "libprobe.so", f"lib.{ext}"]
        exe = [drv, *rendered, "-o", "main", f"main.{ext}", "-L.", "-lprobe"]
        for cmd, src in ((lib, tmp / f"lib.{ext}"), (exe, tmp / f"main.{ext}")):
            try:
                proc = _run(cmd, cwd=tmp)
            except (OSError, subprocess.TimeoutExpired) as exc:
                # The driver was found: one that cannot start or hangs is broken,
                # not missing a capability.
                raise ToolchainBroken(
                    _failure_report(cmd, src, f"did not run: {exc}", "")
                ) from exc
            if compiler_crashed(proc):
                raise ToolchainBroken(
                    _failure_report(
                        cmd, src, f"crashed (exit {proc.returncode})", proc.stderr
                    )
                )
            if proc.returncode != 0:
                if "edit_corpus_target_width" in proc.stderr:
                    return f"{tc.id} with {' '.join(rendered)} does not target {flags.bits}-bit"
                if not _names_capability(proc.stderr, rendered):
                    # Not a missing option or multilib: a compiler that ran and
                    # failed for another reason is a failure, never a skip.
                    raise ToolchainBroken(
                        _failure_report(
                            cmd,
                            src,
                            f"failed (exit {proc.returncode}) without naming a requested capability",
                            proc.stderr,
                        )
                    )
                tail = (proc.stderr.strip().splitlines() or ["(no stderr)"])[-1]
                return f"{tc.id} cannot build with {' '.join(rendered)}: {tail}"
    return None


# What a toolchain without 32-bit multilib says when asked for -m32.
_MULTILIB_MISSING = re.compile(
    r"cannot find -l|cannot find crt|stubs-32\.h|"
    r"bits/libc-header-start\.h|gnu/stubs|unsupported architecture|-m32 is not supported"
)


# A diagnostic that rejects an option or target outright.
_REJECTS_OPTION = re.compile(
    r"error: (unrecognized|unknown|unsupported|invalid)|"
    r"unrecognized command[- ]line option|unknown argument|"
    r"is not supported|unsupported option",
    re.IGNORECASE,
)


def _rejects_flag(stderr: str, flag: str) -> bool:
    """Whether *stderr* has a diagnostic line rejecting *flag* by name."""
    return any(
        flag in ln and _REJECTS_OPTION.search(ln) for ln in stderr.splitlines()
    ) and not _CRASH.search(stderr)


# The marker the libstdc++ probe's #error prints on another C++ library.
_NOT_LIBSTDCXX = "corpus probe: not libstdc++"


def _names_capability(stderr: str, rendered: list[str]) -> bool:
    """Whether a failed probe's *stderr* names one of the requested capabilities.

    Either the driver rejects one of the non-default flags by name
    (``unrecognized command-line option '-fabi-version=2'``), or ``-m32`` was
    asked for and the 32-bit runtime is missing.
    """
    if _CRASH.search(stderr):
        return False
    # A crash is never a missing capability, whatever capability-looking
    # warnings (e.g. ld's "skipping incompatible") came before it.
    optional = [f for f in rendered if not f.startswith("-O")]
    rejected = [ln for ln in stderr.splitlines() if _REJECTS_OPTION.search(ln)]
    # The driver must *reject* a requested flag, not merely mention it (a
    # warning that names the flag says nothing about the failure).
    if any(f in ln for f in optional for ln in rejected):
        return True
    return "-m32" in rendered and _MULTILIB_MISSING.search(stderr) is not None


def _flags_unavailable(tc: Toolchain, lang: str, flags: Flags) -> str | None:
    """Why *flags* cannot be built by *tc*; raises if *tc* cannot build at all."""
    reason = _flags_build(tc, lang, flags)
    if reason is None:
        return None
    baseline = _flags_build(tc, lang, Flags(), check_width=False)
    if baseline is not None:
        raise ToolchainBroken(
            f"{tc.id} ({tc.driver(lang)}) fails the plain {lang} baseline probe: {baseline}"
        )
    return reason


@functools.cache
def uses_libstdcxx(tc: Toolchain) -> bool:
    """Whether *tc*'s C++ driver compiles against libstdc++ (``__GLIBCXX__``).

    False for a driver that is not installed or uses another library; a driver
    that is installed but cannot run, hangs or crashes raises ``ToolchainBroken``.
    """
    if shutil.which(tc.cxx) is None:
        return False
    try:
        proc = subprocess.run(
            [tc.cxx, "-x", "c++", "-fsyntax-only", "-"],
            input=f"#include <string>\n#ifndef __GLIBCXX__\n#error {_NOT_LIBSTDCXX}\n#endif\n",
            capture_output=True,
            text=True,
            check=False,
            timeout=_PROBE_TIMEOUT,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ToolchainBroken(f"{tc.cxx} libstdc++ probe: {exc}") from exc
    if compiler_crashed(proc):
        raise ToolchainBroken(
            f"{tc.cxx} crashed on the libstdc++ probe (exit {proc.returncode}):\n{proc.stderr}"
        )
    if proc.returncode == 0:
        return True
    if _NOT_LIBSTDCXX in proc.stderr:
        return False  # the probe's own #error: another C++ library
    raise ToolchainBroken(
        f"{tc.cxx} failed the libstdc++ probe without reaching its #error "
        f"(exit {proc.returncode}):\n{proc.stderr or '(no stderr)'}"
    )


# Features a standard names but a compiler may still lack in that mode (gcc 8
# accepts -std=c++2a without concepts): a snippet only a supporting compiler builds.
FEATURE_PROBES: dict[str, str] = {
    "concepts": "template <class T> concept C = sizeof(T) > 0;\n"
    "template <class T> requires C<T> int f(T) { return 0; }\n",
    "enum_underlying": "enum corpus_probe_e : unsigned char { CORPUS_PROBE_A };\n",
    # Accepting the attribute is not enough (gcc 8 accepts and ignores it):
    # the empty member must take no space (Itanium ABI; MSVC is not built).
    "no_unique_address": "struct E {}; struct S { [[no_unique_address]] E e; int x; };\n"
    'static_assert(sizeof(S) == sizeof(int), "no_unique_address ignored");\n',
}


@functools.cache
def lacks_feature(tc: Toolchain, std: Standard, feature: str) -> bool:
    """Whether *tc* in *std* mode fails to compile *feature*'s probe."""
    snippet = FEATURE_PROBES.get(feature)
    spelling = std_flag(tc, std)
    if snippet is None or spelling is None:
        return False
    lang = "c++" if std.lang == "c++" else "c"
    try:
        proc = subprocess.run(
            [tc.driver(std.lang), f"-std={spelling}", "-x", lang, "-fsyntax-only", "-"],
            input=snippet,
            capture_output=True,
            text=True,
            check=False,
            timeout=_PROBE_TIMEOUT,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ToolchainBroken(f"{tc.id} feature probe {feature}: {exc}") from exc
    if compiler_crashed(proc):
        raise ToolchainBroken(
            f"{tc.id} crashed on the {feature} probe: {proc.stderr[-400:]}"
        )
    return proc.returncode != 0


@functools.cache
def default_cxx11_abi(tc: Toolchain) -> int | None:
    """The ``_GLIBCXX_USE_CXX11_ABI`` value *tc*'s libstdc++ defaults to, or None."""
    src = "#include <string>\nint corpus_abi = _GLIBCXX_USE_CXX11_ABI;\n"
    for value in (1, 0):
        try:
            proc = subprocess.run(
                [tc.cxx, "-x", "c++", "-fsyntax-only", "-"],
                # A negative array bound, not static_assert: valid in C++98 too.
                input=src
                + f"typedef char corpus_abi_is[(_GLIBCXX_USE_CXX11_ABI == {value}) ? 1 : -1];\n",
                capture_output=True,
                text=True,
                check=False,
                timeout=_PROBE_TIMEOUT,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        if proc.returncode == 0:
            return value
    return None


@functools.cache
def rejects_flags(tc: Toolchain, flags: tuple[str, ...], bits: int = 64) -> bool:
    """Whether *tc* cannot build a trivial C shared object with *flags*.

    ``--fatal-warnings`` makes GNU ld's "-z X ignored" (an older binutils that
    does not know the option) a refusal rather than a silent no-op.
    """
    if shutil.which(tc.cc) is None:
        return True
    width = ["-m32"] if bits == 32 else []
    with tempfile.TemporaryDirectory() as tmp:
        try:
            proc = subprocess.run(
                [
                    tc.cc,
                    *width,
                    "-x",
                    "c",
                    "-shared",
                    "-fPIC",
                    *flags,
                    "-Wl,--fatal-warnings",
                    "-o",
                    os.path.join(tmp, "probe.so"),
                    "-",
                ],
                input="int corpus_probe(int a) { return a + 1; }\n",
                capture_output=True,
                text=True,
                check=False,
                timeout=_PROBE_TIMEOUT,
            )
        except (OSError, subprocess.TimeoutExpired):
            return True
    return proc.returncode != 0


@functools.cache
def predefines(tc: Toolchain, macro: str, bits: int = 64) -> bool:
    """Whether *tc*'s C driver predefines *macro* under its default flags."""
    if shutil.which(tc.cc) is None:
        return False
    width = ["-m32"] if bits == 32 else []
    try:
        proc = subprocess.run(
            [tc.cc, *width, "-x", "c", "-dM", "-E", "-"],
            input="",
            capture_output=True,
            text=True,
            check=False,
            timeout=_PROBE_TIMEOUT,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0 and f"#define {macro} " in proc.stdout


def capability_gap(op: object, config: Config) -> str | None:
    """A host capability *op* needs that ``op.requires`` cannot see without probing.

    Kept out of ``requires`` so collecting the corpus never runs a compiler.
    """
    if getattr(op, "needs_libstdcxx", False) and not uses_libstdcxx(config.producer):
        return f"{config.producer.id}'s C++ standard library is not libstdc++"
    probe = tuple(getattr(op, "probe_flags", ()))
    if probe and rejects_flags(config.producer, probe, config.flags.bits):
        return f"{config.producer.id} does not accept {' '.join(probe)} here"
    default = getattr(op, "default_macro", None)
    if default is not None:
        macro, wanted = default
        if predefines(config.producer, macro, config.flags.bits) != wanted:
            state = "predefines" if not wanted else "does not predefine"
            return (
                f"{config.producer.id} {state} {macro} by default: the edit "
                "leaves the binary as it was"
            )
    for feature in getattr(op, "features", ()):
        if lacks_feature(config.producer, config.std, feature):
            return f"{config.producer.id} does not compile {feature} in -std={config.std.name}"
    rebuild = getattr(op, "rebuild", None)
    if rebuild is not None:
        new_abi = rebuild(Flags()).cxx11_abi
        if new_abi is not None and config.flags.cxx11_abi is None:
            if default_cxx11_abi(config.producer) == new_abi:
                return (
                    f"{config.producer.id}'s libstdc++ already defaults to "
                    f"_GLIBCXX_USE_CXX11_ABI={new_abi}: the rebuild changes nothing"
                )
        # A rebuild to another word size needs that target's start files and
        # libc (32-bit multilib for -m32).
        new_bits = rebuild(config.flags).bits
        if new_bits != config.flags.bits and rejects_flags(
            config.producer, (), new_bits
        ):
            return f"{config.producer.id} cannot build a {new_bits}-bit library here"
    return None


def _toolchain_unavailable(
    role: str, tc: Toolchain, std: Standard, fl: Flags
) -> str | None:
    """Why *tc* cannot compile *std* code with *fl* here (shared by both roles)."""
    if tc not in discover():
        return f"{role} toolchain {tc.id} is not installed"
    if tc.family == "msvc":
        return (
            "MSVC DLLs export nothing without __declspec(dllexport) and the corpus "
            "seeds declare none; the MSVC lane is not built"
        )
    if sys.platform == "win32":
        # clang targeting MSVC exports nothing without __declspec(dllexport)
        # (the seeds declare none). MinGW gcc would auto-export, but the PE
        # expectations (e.g. the MSVC-ABI no_unique_address rule) are not
        # validated under MinGW, so the lane is not enabled there yet.
        return (
            f"{tc.family} on Windows: the corpus PE lane is not validated "
            "(clang/MSVC export nothing without __declspec(dllexport); MinGW untested)"
        )
    reason = fl.unsupported_reason(tc, std.lang)
    if reason:
        return f"{role}: {reason}"
    if std_flag(tc, std) is None:
        return f"{role} {tc.id} does not accept -std={std.name}"
    reason = _flags_unavailable(tc, std.lang, fl)
    if reason:
        return f"{role}: {reason}"
    return None


def unavailable(config: Config) -> str | None:
    """Why *config*'s library pair cannot be built and compared here, or None."""
    if config.platform != host_platform():
        # No cross toolchain: an ELF config on macOS would build Mach-O and
        # still enable the ELF execution oracle.
        return f"config targets {config.platform}, host is {host_platform()}"
    reason = _toolchain_unavailable(
        "producer", config.producer, config.std, config.flags
    )
    if reason:
        return reason
    if config.evidence == "dwarf" and config.objfmt != "elf":
        # Mach-O keeps -g debug info in the object files (no dSYM is produced
        # here) and a PE DLL carries none, so the library has no DWARF to give.
        return f"{config.objfmt} libraries built here carry no DWARF of their own"
    if config.evidence == "headers":
        # castxml emulates the system cc/c++, not the producer: the standard
        # must be spellable there too (gcc 13 has no -std=c++26).
        # The clang backend runs clang itself, so its driver decides.
        if config.header_backend == "clang":
            driver = "clang++" if config.lang == "c++" else "clang"
        else:
            driver = "c++" if config.lang == "c++" else "cc"
        if shutil.which(driver) is None and config.header_backend == "clang":
            return f"clang header backend: no {driver} driver on PATH"
        if std_flag_for_driver(driver, config.std) is None:
            return (
                f"the header frontend's system {driver} does not accept "
                f"-std={config.std.name}; headers evidence cannot be dumped"
            )
        # ... and it must target the producer's word size with the layout
        # flags castxml is given (a 64-bit request passes no -m flag).
        if shutil.which(driver) is not None:
            reason = driver_width(
                driver,
                config.lang,
                tuple(config.flags.layout_flags(config.producer, config.lang)),
                config.flags.bits,
            )
            if reason:
                return f"header frontend: {reason}"
        reason = header_backend_unavailable(config.header_backend, config.lang)
        if reason:
            return reason
    return None


@functools.cache
def header_backend_unavailable(backend: str, lang: str) -> str | None:
    """Why *backend* cannot dump *lang* headers on this host, or None.

    The native gcc backend needs a plugin built for the system gcc (ADR-092:
    ``python -m abicheck.extract.headers.gcc.build --compiler g++``, or
    ``ABICHECK_GCC_PLUGIN``); the clang backend needs a clang driver. A
    missing one makes the cell unavailable, never a pass.

    Only castxml runs off Linux ELF: the native gcc backend is ELF-only
    (ADR-092), and the clang backend's corpus ground truth has been checked
    on ELF alone (on a Windows host it reads a -fshort-enums rebuild as
    NO_CHANGE). A PE or Mach-O host reports those cells unavailable rather
    than judging them against unverified expectations.
    """
    if backend != "castxml" and host_platform()[1] != "elf":
        return (
            f"{backend} header backend: the corpus's header-backend axis is "
            f"validated on Linux ELF only (host is {host_platform()})"
        )
    if backend == "gcc":
        from abicheck.errors import SnapshotError
        from abicheck.extract.headers.gcc.discovery import find_plugin

        driver = "c++" if lang == "c++" else "cc"
        if shutil.which(driver) is None:
            return f"gcc header backend: no system {driver}"
        try:
            find_plugin(driver, lang)
        except (SnapshotError, OSError, subprocess.SubprocessError) as exc:
            return f"gcc header backend: {str(exc).splitlines()[0]}"
    elif backend == "clang" and shutil.which("clang") is None:
        return "clang header backend: no clang driver on PATH"
    return None


def consumer_unavailable(
    config: Config, consumer: ResolvedConsumer, features: Sequence[str] = ()
) -> str | None:
    """Why this host cannot compile *consumer*'s programs, or None.

    *features* are the language features the edited header needs; a consumer
    toolchain that names the standard but lacks one cannot compile it.
    """
    if consumer.std.lang != config.lang:
        return None  # an infeasible mix, not a host gap: see cells.pair_infeasible
    reason = _toolchain_unavailable(
        "consumer", consumer.toolchain, consumer.std, consumer.flags
    )
    if reason:
        return reason
    for feature in features:
        if lacks_feature(consumer.toolchain, consumer.std, feature):
            return (
                f"consumer {consumer.toolchain.id} does not compile {feature} "
                f"in -std={consumer.std.name}"
            )
    return None


def execution_unavailable(config: Config) -> str | None:
    """Why the execution oracle cannot run *config* here, or None."""
    if config.objfmt not in EXECUTION_PLATFORMS:
        return f"execution oracle needs an ELF loader (LD_LIBRARY_PATH); host is {config.objfmt}"
    return None


# Build roles whose values only a GNU/ELF link accepts (a version script,
# ``-z``/``--as-needed`` link flags, ``DT_SONAME``).
_ELF_LINK_ROLES = ("build.vscript", "build.ldflags", "build.soname")


def elf_link_unavailable(seed: Seed, config: Config) -> str | None:
    """Why *seed* cannot be linked on this host: its build roles are ELF-only.

    A host gap (``unavailable``), not an infeasible mix: the same cell runs on
    an ELF host. Apple's ld rejects ``--version-script``, ``-z`` and
    ``--as-needed`` outright.
    """
    if config.objfmt == "elf":
        return None
    used = [r for r in _ELF_LINK_ROLES if r in seed.roles]
    if not used:
        return None
    return (
        f"{seed.name} links with ELF-only build roles ({', '.join(used)}); "
        f"host object format is {config.objfmt}"
    )
