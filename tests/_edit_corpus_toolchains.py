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
        seen_bins.add(real)
        ident = _identify(cc_path)
        if ident is None:
            continue
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


@functools.cache
def _flags_build(tc: Toolchain, lang: str, flags: Flags) -> str | None:
    """None if *flags* builds a shared library and links an executable, else why not."""
    ext = "cpp" if lang == "c++" else "c"
    with tempfile.TemporaryDirectory(prefix="edit-corpus-probe-") as d:
        tmp = Path(d)
        # Default visibility explicitly, as the seeds do, so -fvisibility=hidden links.
        (tmp / f"lib.{ext}").write_text(
            "#pragma GCC visibility push(default)\n"
            "int probe_fn(int x) { return x + 1; }\n"
            "#pragma GCC visibility pop\n"
        )
        (tmp / f"main.{ext}").write_text(
            "int probe_fn(int x);\nint main(void) { return probe_fn(-1); }\n"
            if lang == "c"
            else "int probe_fn(int x);\nint main() { return probe_fn(-1); }\n"
        )
        rendered = flags.render(tc, lang)
        drv = tc.driver(lang)
        lib = [drv, *rendered, "-shared", "-fPIC", "-o", "libprobe.so", f"lib.{ext}"]
        exe = [drv, *rendered, "-o", "main", f"main.{ext}", "-L.", "-lprobe"]
        for cmd in (lib, exe):
            try:
                proc = _run(cmd, cwd=tmp)
            except (OSError, subprocess.TimeoutExpired) as exc:
                return f"{' '.join(cmd)}: {exc}"
            if proc.returncode != 0:
                tail = (proc.stderr.strip().splitlines() or ["(no stderr)"])[-1]
                return f"{tc.id} cannot build with {' '.join(rendered)}: {tail}"
    return None


def unavailable(config: Config) -> str | None:
    """Why *config* cannot be built and compared on this host, or None."""
    host_fmt = config.platform[1]
    if host_fmt != "elf" and config.platform[0] != sys.platform:
        return f"config targets {config.platform}, host is {sys.platform}"
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
            return (
                f"{tc.family} targeting Windows exports no symbols without "
                "__declspec(dllexport); the corpus seeds declare none"
            )
        reason = fl.unsupported_reason(tc, config.lang)
        if reason:
            return f"{role}: {reason}"
        if std_flag(tc, config.std) is None:
            return f"{role} {tc.id} does not accept -std={config.std.name}"
        reason = _flags_build(tc, config.lang, fl)
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
    if config.consumer_fl.bits != config.flags.bits:
        return "consumer and producer word sizes differ; nothing can link"
    return None


def execution_unavailable(config: Config) -> str | None:
    """Why the execution oracle cannot run *config* here, or None."""
    if config.objfmt not in EXECUTION_PLATFORMS:
        return f"execution oracle needs an ELF loader (LD_LIBRARY_PATH); host is {config.objfmt}"
    return None
