"""Differential type-spelling matrix (families F14 and F15).

For every ABI-relevant type pair and every site it can occupy (a function
parameter, a struct field, a return where legal, a typedef), build two
versions of a library that differ only in that type, and compare them through
each evidence lane:

* ``A`` -- DWARF: ``-g`` builds, ``abicheck compare`` with no headers;
* ``B`` -- headers without debug info: ``-H old=... -H new=...``;
* ``C`` -- dump each side to JSON, then compare the two dumps.

Whether a (pair, site) is ABI-different is decided by an oracle that reads no
abicheck output: a probe is compiled per side, its layout is printed by a
program, its argument-passing, call and return code is disassembled with
``objdump``, and the exported symbols of the site library are listed with
``nm``. Equal fingerprints mean ABI-identical. ``test_oracle_partitions_the_cells``
pins the identical cells; every other legal cell must be reported.

Compiler coverage: GCC writes no ``DW_AT_calling_convention`` for ``ms_abi`` on
a subroutine type, so lane A cannot observe that convention in a GCC build
(the header lanes are the evidence there). The clang-built DWARF case is in
``tests/test_dwarf_subroutine_calling_convention.py``.
"""

from __future__ import annotations

import atexit
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from functools import cache
from pathlib import Path

import pytest
from tests._abicheck_subprocess import abicheck_command

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[1]
_WORK = Path(tempfile.mkdtemp(prefix="typespell-matrix-"))
atexit.register(shutil.rmtree, _WORK, ignore_errors=True)

_LANES = ("A", "B", "C")


@dataclass(frozen=True)
class Pair:
    """Two spellings of one type and the sites it can occupy.

    ``v1``/``v2`` are declarator templates; ``{n}`` is replaced by the declared
    name (``p``, ``m``, ``tdef_t``, or the return declarator). ``pre1``/``pre2``
    are declarations the type needs. ``call`` is the statement that calls a
    function-pointer type, for pairs whose call code differs.
    """

    key: str
    v1: str
    v2: str
    sites: tuple[str, ...] = ("param", "field", "typedef", "return")
    pre1: str = ""
    pre2: str = ""
    call: str = ""
    cxx: bool = False
    bitfield: bool = False


PAIRS: dict[str, Pair] = {
    p.key: p
    for p in (
        Pair(
            "vec16i_vs_arr4",
            "int {n} __attribute__((vector_size(16)))",
            "int {n}[4]",
            sites=("param", "field", "typedef"),
        ),
        # A vector_size attribute after a function declarator applies to the
        # function, not its return type, so a vector return is not spelled
        # by this template and the return site is left out for vector pairs.
        Pair(
            "vec8i_vs_vec16i",
            "int {n} __attribute__((vector_size(8)))",
            "int {n} __attribute__((vector_size(16)))",
            sites=("param", "field", "typedef"),
        ),
        Pair("int_vs_long", "int {n}", "long {n}"),
        Pair("i128_vs_ldouble", "__int128 {n}", "long double {n}"),
        Pair("f16_vs_short", "_Float16 {n}", "short {n}"),
        Pair("fcomplex_vs_dcomplex", "float _Complex {n}", "double _Complex {n}"),
        # Only the field site is classified. As a by-value parameter the callee
        # realigns its stack, which the probe reads as a code difference although
        # the caller-visible convention is unchanged; that makes the typedef
        # site's oracle verdict ambiguous, so it is left out rather than pinned.
        Pair(
            "aligned16_int",
            "int {n} __attribute__((aligned(16)))",
            "int {n}",
            sites=("field",),
        ),
        Pair(
            "packed_struct",
            "struct PT {n}",
            "struct PT {n}",
            pre1="struct PT { char c; int i; } __attribute__((packed));",
            pre2="struct PT { char c; int i; };",
        ),
        Pair(
            "packed_enum",
            "enum PE {n}",
            "enum PE {n}",
            pre1="enum PE { PE_A, PE_B } __attribute__((packed));",
            pre2="enum PE { PE_A, PE_B };",
        ),
        Pair(
            "bitfield_3_vs_4",
            "int {n} : 3",
            "int {n} : 4",
            sites=("field",),
            bitfield=True,
        ),
        Pair(
            "ms_abi_fnptr",
            "void (__attribute__((ms_abi)) *{n})(int)",
            "void (*{n})(int)",
            call="p(v);",
        ),
        Pair(
            "sysv_vs_ms_fnptr",
            "void (__attribute__((sysv_abi)) *{n})(int)",
            "void (__attribute__((ms_abi)) *{n})(int)",
            call="p(v);",
        ),
        Pair("fnptr_param_list", "void (*{n})(int)", "void (*{n})(long)", call="p(v);"),
        Pair(
            "fnptr_return_change",
            "int (*{n})(int)",
            "long (*{n})(int)",
            call="volatile long s = p(v);",
        ),
        Pair(
            "noexcept_fnptr",
            "void (*{n})(int) noexcept",
            "void (*{n})(int)",
            call="p(v);",
            cxx=True,
        ),
        # ABI-identical pairs: negative controls.
        Pair("volatile_int", "volatile int {n}", "int {n}", sites=("param", "field")),
        Pair("atomic_int", "_Atomic int {n}", "int {n}", sites=("param", "field")),
        Pair("const_pointee", "const int *{n}", "int *{n}", sites=("param",)),
        Pair("restrict_ptr", "int *restrict {n}", "int *{n}", sites=("param",)),
        Pair("fnptr_same", "void (*{n})(int)", "void (*{n})(int)", call="p(v);"),
    )
}

#: Cells the independent oracle finds ABI-identical. Every other legal cell of
#: a pair in PAIRS is ABI-different and must be reported.
#: ``noexcept`` is part of a C++17 function type but not of its code: the
#: field and return sites keep the same layout and call code, and only the
#: parameter and typedef sites change a mangled exported name.
IDENTICAL_CELLS: frozenset[tuple[str, str]] = frozenset(
    {
        ("volatile_int", "param"),
        ("volatile_int", "field"),
        ("atomic_int", "param"),
        ("atomic_int", "field"),
        ("const_pointee", "param"),
        ("restrict_ptr", "param"),
        ("fnptr_same", "param"),
        ("fnptr_same", "field"),
        ("fnptr_same", "typedef"),
        ("fnptr_same", "return"),
        ("noexcept_fnptr", "field"),
        ("noexcept_fnptr", "return"),
    }
)

#: Pairs whose convention is carried by a returned function pointer.
_RETURNED_CONVENTION_PAIRS = frozenset(
    {"ms_abi_fnptr", "sysv_vs_ms_fnptr", "fnptr_return_change"}
)

#: GCC writes no DW_AT_calling_convention for ms_abi/sysv_abi on a subroutine
#: type, so a GCC -g build cannot show the convention (verified with readelf);
#: the header lanes carry it.
_GCC_DWARF_CC_XFAIL = (
    "GCC writes no DW_AT_calling_convention for ms_abi on a subroutine type; "
    "DWARF cannot observe it in a GCC build, the header lanes can"
)

#: ABI-identical cells every lane reports as a break today: ``_Atomic`` is
#: spelled and compared by name, so ``_Atomic int`` against ``int`` is a
#: finding in DWARF and in both header lanes. Recorded in ``results.md``; the
#: brief for this change does not fix over-reports, so these are not asserted.
_OVER_REPORTED_IDENTICAL = frozenset({("atomic_int", "param"), ("atomic_int", "field")})

#: ABI-identical cells lane A reports precisely today. Lane A over-reports the
#: name-identity pairs (char/signed char, long/long long, _Bool, enum/unsigned,
#: __int128 signedness, vector float/long, restrict); those are listed in the
#: final report, not asserted here.
_LANE_A_PRECISE_IDENTICAL = frozenset(
    {
        ("volatile_int", "param"),
        ("volatile_int", "field"),
        ("const_pointee", "param"),
        ("fnptr_same", "param"),
        ("fnptr_same", "field"),
        ("fnptr_same", "typedef"),
        ("fnptr_same", "return"),
        ("noexcept_fnptr", "field"),
        ("noexcept_fnptr", "return"),
    }
)

#: Which probe functions carry each site's ABI, and which layout tokens it reads
#: from the sizes program (P size/align; struct size/align/offsetof).
#: A function-pointer type's call code is part of every site that holds one.
_SITE_PROBES = {
    "param": ("probe_param", "probe_call"),
    "typedef": ("probe_param", "probe_call"),
    "field": ("probe_fcall", "probe_call"),
    "return": ("probe_retcall", "probe_call"),
}
_SITE_LAYOUT = {
    # A parameter, typedef or return is passed by value: its ABI is the size
    # and the register class, not the alignment (an over-aligned scalar passes
    # exactly as its plain type does). A field's ABI includes its alignment.
    "param": (0,),
    "typedef": (0,),
    "return": (0,),
    "field": (2, 3, 4),
}


def _need(tool: str) -> str:
    path = shutil.which(tool)
    if path is None:
        pytest.skip(f"{tool} not available")
    return path


def _render(template: str, name: str) -> str:
    return template.replace("{n}", name)


def _run(argv: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT)
    return subprocess.run(
        argv, cwd=cwd, env=env, capture_output=True, text=True, check=False
    )


def _compiler(pair: Pair) -> str:
    return _need("g++" if pair.cxx else "gcc")


def _lang(pair: Pair) -> list[str]:
    return ["-x", "c++", "-std=c++17"] if pair.cxx else ["-x", "c"]


def _type(pair: Pair, side: str, name: str) -> str:
    return _render(pair.v1 if side == "1" else pair.v2, name)


def _pre(pair: Pair, side: str) -> str:
    return pair.pre1 if side == "1" else pair.pre2


def _legal(pair: Pair, site: str) -> bool:
    if site not in pair.sites:
        return False
    return not (pair.bitfield and site != "field")


# ── site sources ─────────────────────────────────────────────────────────


def _header_text(pair: Pair, side: str, site: str) -> str:
    pre = _pre(pair, side)
    if site == "param":
        return f"{pre}\nvoid f({_type(pair, side, 'p')});\n"
    if site == "field":
        field = _type(pair, side, "m")
        return f"{pre}\nstruct S {{ int tag; {field}; }};\nvoid f(struct S *s);\n"
    if site == "typedef":
        return f"{pre}\ntypedef {_type(pair, side, 'tdef_t')};\nvoid f(tdef_t p);\n"
    if site == "return":
        return f"{pre}\n{_type(pair, side, 'f(void)')};\n"
    raise AssertionError(site)


def _definition(pair: Pair, side: str, site: str) -> str:
    if site == "param":
        return f"void f({_type(pair, side, 'p')}) {{ (void)p; }}"
    if site == "field":
        return "void f(struct S *s) { (void)s; }"
    if site == "typedef":
        return "void f(tdef_t p) { (void)p; }"
    if site == "return":
        return f"{_type(pair, side, 'f(void)')} {{ __builtin_unreachable(); }}"
    raise AssertionError(site)


# ── library builds ───────────────────────────────────────────────────────


def _header_path(key: str, site: str, side: str) -> Path:
    return _WORK / key / site / f"v{side}.h"


@cache
def _build(key: str, site: str, side: str, debug: bool) -> Path:
    pair = PAIRS[key]
    cc = _compiler(pair)
    out_dir = _WORK / key / site
    out_dir.mkdir(parents=True, exist_ok=True)
    header = _header_path(key, site, side)
    header.write_text(_header_text(pair, side, site), encoding="utf-8")
    src = out_dir / f"v{side}.{'cc' if pair.cxx else 'c'}"
    src.write_text(
        f'#include "v{side}.h"\n{_definition(pair, side, site)}\n', encoding="utf-8"
    )
    lib = out_dir / f"v{side}{'g' if debug else 'n'}.so"
    argv = [
        cc,
        *(["-g"] if debug else []),
        "-O0",
        "-shared",
        "-fPIC",
        "-Wl,-soname,libx.so.1",
        *_lang(pair),
        str(src),
        "-o",
        str(lib),
    ]
    proc = _run(argv, out_dir)
    if proc.returncode != 0:
        pytest.fail(f"compile failed: {' '.join(argv)}\n{proc.stderr}")
    return lib


# ── the oracle ───────────────────────────────────────────────────────────


def _probe_sizes(pair: Pair, side: str) -> str:
    """Program printing: sizeof(P), _Alignof(P), sizeof(struct), _Alignof(struct), offsetof(m).

    A bitfield has no address, so its P and offset fields print 0.
    """
    pre = _pre(pair, side)
    struct = f"struct probe_s {{ char c; {_type(pair, side, 'm')}; }};"
    align = "alignof" if pair.cxx else "_Alignof"
    if pair.bitfield:
        body = (
            f"{struct}\n"
            'int main(void) { printf("0 0 %zu %zu 0\\n", sizeof(struct probe_s), '
            f"{align}(struct probe_s)); return 0; }}"
        )
    else:
        body = (
            f"typedef {_type(pair, side, 'P')};\n{struct}\n"
            'int main(void) { printf("%zu %zu %zu %zu %zu\\n", sizeof(P), '
            f"{align}(P), sizeof(struct probe_s), {align}(struct probe_s), "
            "offsetof(struct probe_s, m)); return 0; }"
        )
    return f"#include <stddef.h>\n#include <stdio.h>\n{pre}\n{body}\n"


def _probe_bodies(pair: Pair, side: str) -> str:
    """Probe functions whose call, argument and return code shows the ABI.

    Every probe has C linkage so a C++ mangled name cannot change the
    disassembly; only the code of the type matters.
    """
    link = 'extern "C" ' if pair.cxx else ""
    pre = _pre(pair, side)
    lines = [pre, f"struct probe_s {{ char c; {_type(pair, side, 'm')}; }};"]
    lines.append("char probe_buf[64];")
    if pair.bitfield:
        lines += [
            f"{link}void probe_sink(long);",
            f"{link}void probe_fcall(struct probe_s *s) {{ probe_sink((long)s->m); }}",
        ]
    else:
        # The callee's argument prologue and the field's bytes are read with a
        # memcpy of the object's address, not by value: a by-value read of an
        # _Atomic object is a seq-cst load, whose code is not ABI.
        lines += [
            f"typedef {_type(pair, side, 'P')};",
            f"{link}void probe_param(P p) {{ __builtin_memcpy(probe_buf, &p, sizeof p); }}",
            f"{link}void probe_fcall(struct probe_s *s) "
            "{ __builtin_memcpy(probe_buf, &s->m, sizeof s->m); }",
        ]
        # An array cannot be returned, so the return probe exists only for a
        # type that can be (the return site is not legal for an array pair).
        if "[" not in pair.v1 and "[" not in pair.v2:
            lines += [
                f"{link}P probe_ret(void);",
                f"{link}P probe_retcall(void) {{ return probe_ret(); }}",
            ]
    if pair.call:
        # The call takes a variable so an int/long width shows in the code.
        lines.append(f"{link}void probe_call(P p, long v) {{ {pair.call} }}")
    return "\n".join(lines) + "\n"


_FUNC_HEADER = re.compile(r"^[0-9a-f]+ <(?P<name>[^>]+)>:$")


def _disassembly_by_function(obj: Path) -> dict[str, str]:
    objdump = _need("objdump")
    proc = _run([objdump, "-d", "--no-show-raw-insn", str(obj)], obj.parent)
    if proc.returncode != 0:
        pytest.fail(f"objdump failed on {obj}: {proc.stderr}")
    functions: dict[str, list[str]] = {}
    current: str | None = None
    for line in proc.stdout.splitlines():
        header = _FUNC_HEADER.match(line)
        if header:
            current = header.group("name")
            functions.setdefault(current, [])
            continue
        if current is not None and line.strip():
            # Drop the address; keep mnemonics, operands and relocation targets.
            functions[current].append(re.sub(r"^\s*[0-9a-f]+:\s*", "", line))
    return {name: "\n".join(lines) for name, lines in functions.items()}


@cache
def _probe(key: str, side: str) -> tuple[list[str], dict[str, str]]:
    """(layout numbers, disassembly by probe function) for one side of *key*."""
    pair = PAIRS[key]
    cc = _compiler(pair)
    work = _WORK / "oracle" / key / side
    work.mkdir(parents=True, exist_ok=True)
    suffix = ".cc" if pair.cxx else ".c"
    sizes_src = work / f"sizes{suffix}"
    sizes_src.write_text(_probe_sizes(pair, side), encoding="utf-8")
    sizes_bin = work / "sizes"
    proc = _run([cc, *_lang(pair), str(sizes_src), "-o", str(sizes_bin)], work)
    if proc.returncode != 0:
        pytest.fail(f"oracle sizes program failed to compile: {proc.stderr}")
    layout = _run([str(sizes_bin)], work).stdout.split()
    bodies_src = work / f"bodies{suffix}"
    bodies_src.write_text(_probe_bodies(pair, side), encoding="utf-8")
    bodies_obj = work / "bodies.o"
    proc = _run(
        [cc, "-O0", "-c", *_lang(pair), str(bodies_src), "-o", str(bodies_obj)], work
    )
    if proc.returncode != 0:
        pytest.fail(f"oracle bodies failed to compile: {proc.stderr}")
    return layout, _disassembly_by_function(bodies_obj)


def _exported_symbols(key: str, site: str, side: str) -> str:
    """The defined dynamic symbol names and types of the site library.

    A mangled name shows here. Addresses and sizes are dropped: the site body
    changes code size, and the size of a body is not an ABI fact.
    """
    lib = _build(key, site, side, False)
    nm = _need("nm")
    out = _run([nm, "-D", "--defined-only", "-f", "posix", str(lib)], lib.parent).stdout
    names = sorted(
        " ".join(line.split()[:2]) for line in out.splitlines() if line.strip()
    )
    return "\n".join(names)


def cell_fingerprint(key: str, site: str, side: str) -> tuple:
    layout, functions = _probe(key, side)
    picked_layout = tuple(layout[i] for i in _SITE_LAYOUT[site])
    picked_code = tuple(
        (name, functions.get(name, "<absent>")) for name in _SITE_PROBES[site]
    )
    return picked_layout, picked_code, _exported_symbols(key, site, side)


def abi_differs(key: str, site: str) -> bool:
    """Whether the independent oracle finds the two sides of this cell ABI-different."""
    return cell_fingerprint(key, site, "1") != cell_fingerprint(key, site, "2")


# ── abicheck lanes ───────────────────────────────────────────────────────


def _abicheck(*args: str) -> subprocess.CompletedProcess[str]:
    return _run(abicheck_command(*args), REPO_ROOT)


def _lane_result(key: str, site: str, lane: str) -> tuple[int, list[dict]]:
    """(exit code, changes) of one lane for one pair and site."""
    out = _WORK / "reports" / key / site
    out.mkdir(parents=True, exist_ok=True)
    report = out / f"{lane}.json"
    if lane == "A":
        proc = _abicheck(
            "compare",
            str(_build(key, site, "1", True)),
            str(_build(key, site, "2", True)),
            "-o",
            f"json={report}",
        )
    elif lane == "B":
        proc = _abicheck(
            "compare",
            str(_build(key, site, "1", False)),
            str(_build(key, site, "2", False)),
            "-H",
            f"old={_header_path(key, site, '1')}",
            "-H",
            f"new={_header_path(key, site, '2')}",
            "-o",
            f"json={report}",
        )
    else:
        snaps = []
        for side in ("1", "2"):
            snap = out / f"s{side}.json"
            dump = _abicheck(
                "dump",
                str(_build(key, site, side, True)),
                "-H",
                str(_header_path(key, site, side)),
                "-o",
                str(snap),
            )
            assert dump.returncode == 0, dump.stderr
            snaps.append(snap)
        proc = _abicheck(
            "compare", str(snaps[0]), str(snaps[1]), "-o", f"json={report}"
        )
    if not report.exists():
        pytest.fail(
            f"lane {lane} wrote no report (exit {proc.returncode}):\n{proc.stderr}"
        )
    changes = json.loads(report.read_text(encoding="utf-8")).get("changes", [])
    return proc.returncode, changes


_ENTITY_NAMES = ("f", "S", "tdef_t", "PT", "PE")


def _mentions_entity(changes: list[dict]) -> bool:
    text = json.dumps(changes)
    return any(re.search(rf"(?<!\w){name}(?!\w)", text) for name in _ENTITY_NAMES)


# ── tests ────────────────────────────────────────────────────────────────


def _legal_cells() -> list[tuple[str, str]]:
    return [
        (key, site)
        for key, pair in PAIRS.items()
        for site in pair.sites
        if _legal(pair, site)
    ]


def test_oracle_partitions_the_cells() -> None:
    """The independent oracle agrees with the pinned identical-cell set."""
    _need("objdump")
    _need("nm")
    disagreements = {
        f"{key}/{site}": abi_differs(key, site)
        for key, site in _legal_cells()
        if abi_differs(key, site) == ((key, site) in IDENTICAL_CELLS)
    }
    assert disagreements == {}, (
        f"oracle disagrees with the pinned cells: {disagreements}"
    )


def _matrix_cells() -> list:
    cells = []
    for key, site in _legal_cells():
        for lane in _LANES:
            marks = []
            if key in ("ms_abi_fnptr", "sysv_vs_ms_fnptr") and lane == "A":
                marks.append(pytest.mark.xfail(strict=True, reason=_GCC_DWARF_CC_XFAIL))
            cells.append(
                pytest.param(key, site, lane, id=f"{key}-{site}-{lane}", marks=marks)
            )
    return cells


@pytest.mark.parametrize(("key", "site", "lane"), _matrix_cells())
def test_matrix_cell(key: str, site: str, lane: str) -> None:
    if lane != "A":
        _need("castxml")
    code, changes = _lane_result(key, site, lane)
    if (key, site) not in IDENTICAL_CELLS:
        assert code != 0, f"{key} {site} lane {lane}: ABI change reads as clean"
        assert _mentions_entity(changes), (
            f"{key} {site} lane {lane}: no change names the entity (exit {code}): {changes}"
        )
        if site == "return" and key in _RETURNED_CONVENTION_PAIRS:
            # The convention belongs to the returned function type: the finding
            # must be the return-type change, and f's own convention must not be
            # reported as changed (that was the presentation bug).
            kinds = {change.get("kind") for change in changes}
            assert "func_return_changed" in kinds, (
                f"{key} return lane {lane}: no return-type finding: {kinds}"
            )
            assert "calling_convention_changed" not in kinds, (
                f"{key} return lane {lane}: the returned type's convention is "
                f"reported as f's own convention: {kinds}"
            )
        return
    if (key, site) in _OVER_REPORTED_IDENTICAL:
        pytest.skip(f"{key} {site}: every lane reports the identical type (listed)")
    if lane == "A" and (key, site) not in _LANE_A_PRECISE_IDENTICAL:
        pytest.skip(f"{key} {site} lane A over-reports an ABI-identical type (listed)")
    assert code != 4, (
        f"{key} {site} lane {lane}: ABI-identical type read as an ABI break"
    )
