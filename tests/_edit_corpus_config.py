"""Configuration axes of the generated ABI corpus.

A corpus *cell* is ``(operator, seed, Config)``. ``Config`` names every
parameter that can decide an edit's ABI outcome, as explicit axes:

- language standard (``Standard``) and the language features it provides;
- producer toolchain (compiler family + major version) and producer flags
  (``Flags``: -O level, symbol visibility, -m32/-m64, -fshort-enums,
  -fpack-struct, ``_GLIBCXX_USE_CXX11_ABI``, -fabi-version; MSVC renders the
  /O, /Zp and /vd analogues);
- consumer toolchain and consumer flags (default: the producer's);
- platform / object format (``host_platform``);
- the evidence abicheck is given (``EVIDENCE``: binary only, +DWARF, +headers).

Nothing here probes the host: ``_edit_corpus_toolchains`` decides which
configs can be built, and an unbuildable config is *unavailable*, never
passed. Nothing here is ground truth either: operators map a config to their
expectation in ``_edit_corpus_operators``.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field, replace

# --- language standards ----------------------------------------------------


@dataclass(frozen=True, order=True)
class Standard:
    lang: str  # "c" or "c++"
    year: int
    name: str = field(compare=False)
    # Driver spellings to try, preferred first (gcc 13 knows c2x, not c23).
    spellings: tuple[str, ...] = field(compare=False, default=())

    @property
    def id(self) -> str:
        return self.name


def _std(lang: str, year: int, name: str, *alts: str) -> Standard:
    return Standard(lang, year, name, (name, *alts))


STANDARDS: tuple[Standard, ...] = (
    _std("c", 1989, "c89"),
    _std("c", 1999, "c99"),
    _std("c", 2011, "c11"),
    _std("c", 2017, "c17"),
    _std("c", 2023, "c23", "c2x"),
    _std("c++", 2011, "c++11"),
    _std("c++", 2014, "c++14"),
    _std("c++", 2017, "c++17"),
    _std("c++", 2020, "c++20", "c++2a"),
    _std("c++", 2023, "c++23", "c++2b"),
    _std("c++", 2026, "c++26", "c++2c"),
)
STANDARD_BY_NAME: dict[str, Standard] = {s.name: s for s in STANDARDS}

# Language features an edit can need -> first standard (per language) that has it.
# Absent language = never available there.
FEATURES: dict[str, dict[str, int]] = {
    "inline": {"c": 1999, "c++": 2011},
    "enum_class": {"c++": 2011},
    "enum_underlying": {"c": 2023, "c++": 2011},
    "noexcept_in_type": {"c++": 2017},
    "concepts": {"c++": 2020},
    "no_unique_address": {"c++": 2020},
    "std_string": {"c++": 2011},
}


def has_feature(std: Standard, feature: str) -> bool:
    first = FEATURES[feature].get(std.lang)
    return first is not None and std.year >= first


# --- toolchains ------------------------------------------------------------

FAMILIES = ("gcc", "clang", "apple-clang", "msvc")


@dataclass(frozen=True, order=True)
class Toolchain:
    family: str
    version: int  # major
    cc: str = field(compare=False)
    cxx: str = field(compare=False)

    @property
    def id(self) -> str:
        return f"{self.family}{self.version}"

    def driver(self, lang: str) -> str:
        return self.cxx if lang == "c++" else self.cc

    @property
    def gnu_like(self) -> bool:
        return self.family != "msvc"


# --- flags -----------------------------------------------------------------


@dataclass(frozen=True)
class Flags:
    """ABI-relevant compile flags, spelled per toolchain family by ``render``."""

    opt: str = "O0"  # "O0" | "O2"
    visibility: str = "default"  # "default" | "hidden"
    bits: int = 64  # 64 | 32
    short_enums: bool = False
    pack: int | None = None  # -fpack-struct=N / /ZpN
    cxx11_abi: int | None = None  # _GLIBCXX_USE_CXX11_ABI; None = toolchain default
    abi_version: int | None = None  # gcc -fabi-version=N
    vtordisp: int | None = None  # MSVC /vdN

    @property
    def id(self) -> str:
        parts = [self.opt]
        if self.visibility != "default":
            parts.append("hid")
        if self.bits != 64:
            parts.append(f"m{self.bits}")
        if self.short_enums:
            parts.append("se")
        if self.pack is not None:
            parts.append(f"p{self.pack}")
        if self.cxx11_abi is not None:
            parts.append(f"cxx11abi{self.cxx11_abi}")
        if self.abi_version is not None:
            parts.append(f"fabi{self.abi_version}")
        if self.vtordisp is not None:
            parts.append(f"vd{self.vtordisp}")
        return "+".join(parts)

    def unsupported_reason(self, tc: Toolchain, lang: str) -> str | None:
        """Why this flag set has no spelling for *tc*/*lang*, or None."""
        if self.cxx11_abi is not None and lang != "c++":
            return "_GLIBCXX_USE_CXX11_ABI is a libstdc++ C++ macro"
        if self.cxx11_abi is not None and tc.family in ("msvc", "apple-clang"):
            return f"{tc.family} does not use libstdc++"
        if self.abi_version is not None and tc.family != "gcc":
            return "-fabi-version is a gcc flag"
        if self.vtordisp is not None and tc.family != "msvc":
            return "/vd is an MSVC flag"
        if self.vtordisp is not None and lang != "c++":
            return "/vd affects C++ classes only"
        if tc.family == "msvc" and (self.short_enums or self.visibility != "default"):
            return "MSVC has no -fshort-enums / -fvisibility analogue"
        return None

    def render(self, tc: Toolchain, lang: str) -> list[str]:
        if tc.family == "msvc":
            out = ["/Od" if self.opt == "O0" else "/O2"]
            if self.pack is not None:
                out.append(f"/Zp{self.pack}")
            if self.vtordisp is not None:
                out.append(f"/vd{self.vtordisp}")
            return out
        out = [f"-{self.opt}"]
        if self.visibility != "default":
            out.append(f"-fvisibility={self.visibility}")
        if self.bits == 32:
            out.append("-m32")
        if self.short_enums:
            out.append("-fshort-enums")
        if self.pack is not None:
            out.append(f"-fpack-struct={self.pack}")
        if self.cxx11_abi is not None:
            out.append(f"-D_GLIBCXX_USE_CXX11_ABI={self.cxx11_abi}")
        if self.abi_version is not None:
            out.append(f"-fabi-version={self.abi_version}")
        return out

    def layout_flags(self, tc: Toolchain, lang: str) -> list[str]:
        """The subset that changes how a *header* lays out types (for castxml)."""
        return [
            f
            for f in self.render(tc, lang)
            if f.startswith(("-m32", "-fshort-enums", "-fpack-struct", "-D_GLIBCXX"))
        ]


# --- evidence and platform -------------------------------------------------

# What abicheck is given, weakest first.
EVIDENCE: tuple[str, ...] = ("binary", "dwarf", "headers")
EVIDENCE_RANK: dict[str, int] = {e: i for i, e in enumerate(EVIDENCE)}


def host_platform() -> tuple[str, str]:
    """(platform, object format) of this host."""
    if sys.platform == "darwin":
        return ("darwin", "macho")
    if sys.platform == "win32":
        return ("win32", "pe")
    return (sys.platform, "elf")


# --- the config ------------------------------------------------------------


@dataclass(frozen=True)
class Config:
    std: Standard
    producer: Toolchain
    flags: Flags = Flags()
    consumer: Toolchain | None = None  # None = same as producer
    consumer_flags: Flags | None = None  # None = same as producer flags
    evidence: str = "headers"
    platform: tuple[str, str] = field(default_factory=host_platform)

    @property
    def lang(self) -> str:
        return self.std.lang

    @property
    def consumer_tc(self) -> Toolchain:
        return self.consumer or self.producer

    @property
    def consumer_fl(self) -> Flags:
        if self.consumer_flags is not None:
            return self.consumer_flags
        if self.consumer_tc.family != "gcc" and self.flags.abi_version is not None:
            # -fabi-version has no other-family spelling: that consumer uses its default.
            return replace(self.flags, abi_version=None)
        return self.flags

    @property
    def objfmt(self) -> str:
        return self.platform[1]

    def has(self, feature: str) -> bool:
        return has_feature(self.std, feature)

    @property
    def id(self) -> str:
        parts = [self.std.id, self.producer.id, self.flags.id]
        if self.consumer is not None and self.consumer != self.producer:
            parts.append(f"use-{self.consumer.id}")
        if self.consumer_flags is not None and self.consumer_flags != self.flags:
            parts.append(f"useflags-{self.consumer_flags.id}")
        parts.append(self.evidence)
        return "-".join(parts)

    def with_(self, **kw: object) -> Config:
        return replace(self, **kw)  # type: ignore[arg-type]


def default_config(lang: str, producer: Toolchain) -> Config:
    """The base PR's fixed configuration: C17 / C++17, -O0, default visibility, headers."""
    return Config(
        std=STANDARD_BY_NAME["c++17" if lang == "c++" else "c17"], producer=producer
    )
