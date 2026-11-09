"""Toolchain registry for the generated ABI corpus: what this host can build.

``discover()`` finds the C/C++ compiler pairs on ``PATH`` (``gcc``/``g++``,
``gcc-N``/``g++-N``, ``clang``/``clang++``, ``clang-N``/``clang++-N``, Apple
clang, MSVC ``cl``), reads each one's family and major version from the driver
itself, and folds duplicates (``gcc`` and ``gcc-13`` resolving to one binary).

``unavailable(config)`` answers *why* a config cannot be built here, or
``None``. Each answer comes from probing the real driver: the standard
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
from pathlib import Path

from tests._edit_corpus_config import (
    Config,
    Flags,
    Standard,
    Toolchain,
    host_platform,
)

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
        except (OSError, subprocess.TimeoutExpired):
            return None
        if proc.returncode == 0:
            return spelling
    return None


class ToolchainBroken(RuntimeError):
    """An installed toolchain failed to build the plain baseline probe.

    That is a failure, never a skip: a configured compiler that ran and
    failed proves the lane would test less than it claims.
    """


def _crashed(proc: subprocess.CompletedProcess[str]) -> bool:
    """A compiler that died on a signal or reported an internal error crashed."""
    return proc.returncode < 0 or "internal compiler error" in proc.stderr


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
    if _crashed(proc):
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
        for cmd in (lib, exe):
            try:
                proc = _run(cmd, cwd=tmp)
            except (OSError, subprocess.TimeoutExpired) as exc:
                # The driver was found: one that cannot start or hangs is broken,
                # not missing a capability.
                raise ToolchainBroken(f"{' '.join(cmd)}: {exc}") from exc
            if _crashed(proc):
                raise ToolchainBroken(
                    f"{' '.join(cmd)} crashed (exit {proc.returncode}): {proc.stderr.strip()[-400:]}"
                )
            if proc.returncode != 0:
                if "edit_corpus_target_width" in proc.stderr:
                    return f"{tc.id} with {' '.join(rendered)} does not target {flags.bits}-bit"
                if not _names_capability(proc.stderr, rendered):
                    # Not a missing option or multilib: a compiler that ran and
                    # failed for another reason is a failure, never a skip.
                    raise ToolchainBroken(
                        f"{' '.join(cmd)} failed (exit {proc.returncode}) without naming "
                        f"a requested capability: {proc.stderr.strip()[-400:]}"
                    )
                tail = (proc.stderr.strip().splitlines() or ["(no stderr)"])[-1]
                return f"{tc.id} cannot build with {' '.join(rendered)}: {tail}"
    return None


# What a toolchain without 32-bit multilib says when asked for -m32.
_MULTILIB_MISSING = re.compile(
    r"skipping incompatible|cannot find -l|cannot find crt|stubs-32\.h|"
    r"bits/libc-header-start\.h|gnu/stubs|unsupported architecture|-m32 is not supported"
)


def _names_capability(stderr: str, rendered: list[str]) -> bool:
    """Whether a failed probe's *stderr* names one of the requested capabilities.

    Either the driver rejects one of the non-default flags by name
    (``unrecognized command-line option '-fabi-version=2'``), or ``-m32`` was
    asked for and the 32-bit runtime is missing.
    """
    optional = [f for f in rendered if not f.startswith("-O")]
    if any(f in stderr for f in optional):
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
    """Whether *tc*'s C++ driver compiles against libstdc++ (``__GLIBCXX__``)."""
    try:
        proc = subprocess.run(
            [tc.cxx, "-x", "c++", "-fsyntax-only", "-"],
            input="#include <string>\n#ifndef __GLIBCXX__\n#error not libstdc++\n#endif\n",
            capture_output=True,
            text=True,
            check=False,
            timeout=_PROBE_TIMEOUT,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0


# Features a standard names but a compiler may still lack in that mode (gcc 8
# accepts -std=c++2a without concepts): a snippet only a supporting compiler builds.
FEATURE_PROBES: dict[str, str] = {
    "concepts": "template <class T> concept C = sizeof(T) > 0;\n"
    "template <class T> requires C<T> int f(T) { return 0; }\n",
    "enum_underlying": "enum corpus_probe_e : unsigned char { CORPUS_PROBE_A };\n",
    "no_unique_address": "struct E {}; struct S { [[no_unique_address]] E e; int x; };\n",
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
    if _crashed(proc):
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
                input=src + f'static_assert(_GLIBCXX_USE_CXX11_ABI == {value}, "");\n',
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


def capability_gap(op: object, config: Config) -> str | None:
    """A host capability *op* needs that ``op.requires`` cannot see without probing.

    Kept out of ``requires`` so collecting the corpus never runs a compiler.
    """
    if getattr(op, "needs_libstdcxx", False) and not uses_libstdcxx(config.producer):
        return f"{config.producer.id}'s C++ standard library is not libstdc++"
    for feature in getattr(op, "features", ()):
        for tc in dict.fromkeys((config.producer, config.consumer_tc)):
            if lacks_feature(tc, config.std, feature):
                return f"{tc.id} does not compile {feature} in -std={config.std.name}"
    rebuild = getattr(op, "rebuild", None)
    if rebuild is not None:
        new_abi = rebuild(Flags()).cxx11_abi
        if new_abi is not None and config.flags.cxx11_abi is None:
            if default_cxx11_abi(config.producer) == new_abi:
                return (
                    f"{config.producer.id}'s libstdc++ already defaults to "
                    f"_GLIBCXX_USE_CXX11_ABI={new_abi}: the rebuild changes nothing"
                )
    return None


def unavailable(config: Config) -> str | None:
    """Why *config* cannot be built and compared on this host, or None."""
    if config.platform != host_platform():
        # No cross toolchain: an ELF config on macOS would build Mach-O and
        # still enable the ELF execution oracle.
        return f"config targets {config.platform}, host is {host_platform()}"
    for role, tc, fl in (
        ("producer", config.producer, config.flags),
        ("consumer", config.consumer_tc, config.consumer_fl),
    ):
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
        reason = fl.unsupported_reason(tc, config.lang)
        if reason:
            return f"{role}: {reason}"
        if std_flag(tc, config.std) is None:
            return f"{role} {tc.id} does not accept -std={config.std.name}"
        reason = _flags_unavailable(tc, config.lang, fl)
        if reason:
            return f"{role}: {reason}"
    if config.evidence == "dwarf" and config.objfmt != "elf":
        # Mach-O keeps -g debug info in the object files (no dSYM is produced
        # here) and a PE DLL carries none, so the library has no DWARF to give.
        return f"{config.objfmt} libraries built here carry no DWARF of their own"
    if config.evidence == "headers":
        # castxml emulates the system cc/c++, not the producer: the standard
        # must be spellable there too (gcc 13 has no -std=c++26).
        driver = "c++" if config.lang == "c++" else "cc"
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
    if config.consumer_fl.bits != config.flags.bits:
        return "consumer and producer word sizes differ; nothing can link"
    return None


def execution_unavailable(config: Config) -> str | None:
    """Why the execution oracle cannot run *config* here, or None."""
    if config.objfmt not in EXECUTION_PLATFORMS:
        return f"execution oracle needs an ELF loader (LD_LIBRARY_PATH); host is {config.objfmt}"
    return None
