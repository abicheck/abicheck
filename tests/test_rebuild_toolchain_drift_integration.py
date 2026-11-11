# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Rebuilding an unchanged ``std::string``-using library reads compatible;
removing one of its own exports in the same rebuild still breaks.

The edit corpus's ``rebuild_optimized`` cell on the ``cxx_stdlib`` seed
(PR #1958): ``-O0`` -> ``-O2`` with no source change read BREAKING or
COMPATIBLE_WITH_RISK on gcc and clang at every evidence level, from weak
``std::``/``__gnu_cxx::`` instantiations ``-O2`` inlines, runtime imports
the old version requirements already covered, and a clang ``std::string``
DIE that is declaration-only at ``-O0``. Every consumer built against the
``-O0`` library runs against the ``-O2`` one (the corpus's execution oracle).

gcc also drops ``__stack_chk_fail`` because ``-O2`` leaves no frame that
needs a canary. With DWARF every unit's ``DW_AT_producer`` records the
protector, which proves it unchanged, and the loss is demoted with its rule;
binary only, ``stack_canary_removed`` keeps its RISK, marked as weaker
evidence. An address-derived alignment drop is demoted unless the old side
declares an alignment the new placement fails.

The ``_GLIBCXX_USE_CXX11_ABI=0`` rebuild under headers is compared and
reported as ``glibcxx_dual_abi_changed`` (exit 4, ADR-050 amendment
2026-11-09); adding another macro difference to it keeps the
``not_comparable`` refusal (exit 16), whose reason names both.

"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from abicheck.checker import compare
from abicheck.checker_policy import API_BREAK_KINDS, BREAKING_KINDS, RISK_KINDS
from abicheck.cli import main
from abicheck.dumper import dump
from abicheck.model.change_catalog.registry import Verdict
from abicheck.serialization import snapshot_to_json

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(sys.platform != "linux", reason="ELF/libstdc++ only"),
]

_HEADER = """\
#ifndef EDIT_SEED_STDLIB_HPP
#define EDIT_SEED_STDLIB_HPP
#include <string>
#pragma GCC visibility push(default)
std::string greet(const std::string& who);
int name_len(const std::string& s);
int add(int a, int b);
#pragma GCC visibility pop
#endif
"""
_GREET = 'std::string greet(const std::string& who) { return "hi " + who; }\n'
_NAME_LEN = "int name_len(const std::string& s) { return (int)s.size(); }\n"
_ADD = "int add(int a, int b) { return a + b; }\n"
_SOURCE = '#include "edit_seed.h"\n' + _GREET + _NAME_LEN + _ADD

_COMPILERS = ("g++", "clang++")
_EVIDENCE = ("binary", "dwarf", "headers")


def _driver(cxx: str) -> str:
    if shutil.which(cxx) is None:
        pytest.skip(f"{cxx} not installed")
    return cxx


def _build(
    tmp: Path, name: str, cxx: str, flags: list[str], source: str, debug: bool
) -> Path:
    d = tmp / name
    d.mkdir()
    (d / "edit_seed.h").write_text(_HEADER)
    (d / "lib.cpp").write_text(source)
    so = d / "libseed.so"
    cmd = [cxx, "-std=c++17", *flags, *(["-g"] if debug else []), "-shared", "-fPIC"]
    cmd += ["-I", str(d), "-o", str(so), str(d / "lib.cpp")]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    assert proc.returncode == 0, (
        f"{cmd}\n{proc.stderr}"
    )  # a real compiler failure fails
    return so


def _snap(so: Path, evidence: str, defines: list[str]):
    headers = [so.parent / "edit_seed.h"] if evidence == "headers" else []
    return dump(
        so,
        headers,
        compiler="c++",
        lang="c++",
        header_backend="castxml",
        gcc_options=" ".join(["-std=c++17", *defines]) if headers else None,
    )


def _not_compatible(result) -> list[tuple[str, str]]:
    """Every finding whose effective verdict is a risk or a break."""
    out = []
    for c in result.changes:
        verdict = c.effective_verdict
        if verdict is None:
            if c.kind in BREAKING_KINDS:
                verdict = Verdict.BREAKING
            elif c.kind in API_BREAK_KINDS:
                verdict = Verdict.API_BREAK
            elif c.kind in RISK_KINDS:
                verdict = Verdict.COMPATIBLE_WITH_RISK
        if verdict in (
            Verdict.BREAKING,
            Verdict.API_BREAK,
            Verdict.COMPATIBLE_WITH_RISK,
        ):
            out.append((c.kind.value, c.symbol))
    return out


# Both libstdc++ runtime ABIs, at C++17 and C++20 (a later -std overrides the
# default). From C++20 the old (COW) std::string's -O0 build also exports a
# weak std::less<const char*>::operator(), the implementation's own
# specialization, which read as the library's (RISK) before the
# customization-point owner accepted pointers to builtins and standard types.
_OLD_ABI = "-D_GLIBCXX_USE_CXX11_ABI=0"
_RUNTIME_ABI = ((), ("-std=c++20",), (_OLD_ABI,), ("-std=c++20", _OLD_ABI))


@pytest.mark.parametrize(
    "abi", _RUNTIME_ABI, ids=("c++17", "c++20", "c++17-abi0", "c++20-abi0")
)
@pytest.mark.parametrize("evidence", _EVIDENCE)
@pytest.mark.parametrize("cxx", _COMPILERS)
def test_optimization_rebuild_reads_compatible(tmp_path, cxx, evidence, abi):
    cxx = _driver(cxx)
    debug = evidence != "binary"
    defines = list(abi)
    old = _snap(
        _build(tmp_path, "old", cxx, ["-O0", *defines], _SOURCE, debug),
        evidence,
        defines,
    )
    new = _snap(
        _build(tmp_path, "new", cxx, ["-O2", *defines], _SOURCE, debug),
        evidence,
        defines,
    )
    result = compare(old, new)
    residue = _not_compatible(result)
    # Only the stack-canary observation may remain, and only binary only, where
    # no unit records the protector (see the module docstring).
    allowed = {"stack_canary_removed"} if evidence == "binary" else set()
    assert [k for k, _ in residue if k not in allowed] == [], residue
    expected = (
        {Verdict.COMPATIBLE_WITH_RISK}
        if residue
        else {Verdict.NO_CHANGE, Verdict.COMPATIBLE}
    )
    assert result.verdict in expected


@pytest.mark.parametrize("evidence", _EVIDENCE)
@pytest.mark.parametrize("cxx", _COMPILERS)
def test_own_export_removed_in_the_same_rebuild_still_breaks(tmp_path, cxx, evidence):
    cxx = _driver(cxx)
    debug = evidence != "binary"
    shrunk = '#include "edit_seed.h"\n' + _GREET + _ADD  # name_len no longer defined
    old = _snap(_build(tmp_path, "old", cxx, ["-O0"], _SOURCE, debug), evidence, [])
    new = _snap(_build(tmp_path, "new", cxx, ["-O2"], shrunk, debug), evidence, [])
    result = compare(old, new)
    assert result.verdict is Verdict.BREAKING
    broken = {sym for kind, sym in _not_compatible(result)}
    assert any("name_len" in s for s in broken), broken
    assert all("name_len" in s for s in broken if not s.startswith("__stack")), broken


def _compare_cli(tmp_path: Path, old, new) -> tuple[int, dict, str]:
    (tmp_path / "old.json").write_text(snapshot_to_json(old))
    (tmp_path / "new.json").write_text(snapshot_to_json(new))
    report = tmp_path / "out.json"
    run = CliRunner().invoke(
        main,
        [
            "compare",
            str(tmp_path / "old.json"),
            str(tmp_path / "new.json"),
            "-o",
            f"json={report}",
        ],
    )
    return run.exit_code, json.loads(report.read_text()), run.output


def test_dual_abi_rebuild_is_reported_as_a_break(tmp_path):
    cxx = _driver("g++")
    define = "-D_GLIBCXX_USE_CXX11_ABI=0"
    old = _snap(_build(tmp_path, "old", cxx, ["-O0"], _SOURCE, True), "headers", [])
    new = _snap(
        _build(tmp_path, "new", cxx, ["-O0", define], _SOURCE, True),
        "headers",
        [define],
    )
    exit_code, doc, output = _compare_cli(tmp_path, old, new)
    assert exit_code == 4, output
    assert doc["verdict"] == "BREAKING"
    (root,) = [c for c in doc["changes"] if c["kind"] == "glibcxx_dual_abi_changed"]
    assert "_GLIBCXX_USE_CXX11_ABI=0 (header parse)" in root["new_value"]
    assert "re-mangled" in root["description"]

    # The binaries alone report the same break.
    bin_old = _snap(tmp_path / "old" / "libseed.so", "binary", [])
    bin_new = _snap(tmp_path / "new" / "libseed.so", "binary", [])
    assert compare(bin_old, bin_new).verdict is Verdict.BREAKING


def test_dual_abi_rebuild_with_another_macro_is_still_refused(tmp_path):
    cxx = _driver("g++")
    defines = ["-D_GLIBCXX_USE_CXX11_ABI=0", "-DEDIT_SEED_EXTRA=1"]
    old = _snap(_build(tmp_path, "old", cxx, ["-O0"], _SOURCE, True), "headers", [])
    new = _snap(
        _build(tmp_path, "new", cxx, ["-O0", *defines], _SOURCE, True),
        "headers",
        defines,
    )
    exit_code, doc, output = _compare_cli(tmp_path, old, new)
    assert exit_code == 16, output
    assert doc["verdict"] is None
    assert doc["reason"]["kind"] == "profile_mismatch"
    message = doc["reason"]["message"]
    assert "-D_GLIBCXX_USE_CXX11_ABI=0" in message
    assert "-DEDIT_SEED_EXTRA=1" in message
    assert "glibcxx_dual_abi_changed" in message


#: What a gcc without the 32-bit multilib runtime prints: a missing 32-bit
#: startup object or runtime library, or header. ``skipping incompatible`` alone
#: is not that (ld may go on to find a compatible one), so any other failure of
#: the probe is a real compiler failure, not a missing capability.
_NO_MULTILIB = re.compile(
    r"cannot find (crt\w*\.o|-lgcc|-lgcc_s|-lc)\b|gnu/stubs-32\.h"
)


def _require_m32() -> None:
    cmd = ["gcc", "-m32", "-shared", "-fPIC", "-x", "c", "-", "-o", "/dev/null"]
    source = "int x;\n"
    probe = subprocess.run(
        cmd, input=source, capture_output=True, text=True, check=False
    )
    if probe.returncode == 0:
        return
    if _NO_MULTILIB.search(probe.stderr):
        pytest.skip(
            f"gcc -m32 cannot link (gcc-multilib missing): {probe.stderr[-200:]}"
        )
    pytest.fail(f"gcc -m32 probe failed: {cmd}\nsource: {source!r}\n{probe.stderr}")


@pytest.mark.parametrize("evidence", _EVIDENCE)
def test_i386_c_rebuild_reads_compatible_once_debug_info_is_read(tmp_path, evidence):
    """The corpus's minimized cell (rebuild_optimized on c_core, c17 gcc13 -m32).

    ``-O2`` needs no canary for ``fill_rec`` while ``-fstack-protector-strong``
    stays on in every unit, and places ``g_counter`` at ``0x4008`` instead of
    ``0x4010``. With DWARF the census proves the protector unchanged and the
    canary loss is demoted; the address-derived alignment is demoted at every
    evidence level, since nothing declares an alignment the new placement
    fails. Binary only, the canary loss keeps its RISK (no unit records its
    flags) -- the strict ``tests/_edit_corpus_known.py`` entry.
    """
    _driver("gcc")
    _require_m32()
    from tests._edit_corpus_cells import CellContext, run_cell
    from tests._edit_corpus_config import STANDARD_BY_NAME, Config, Flags
    from tests._edit_corpus_known import known_mismatch
    from tests._edit_corpus_operators import OP_BY_NAME
    from tests._edit_corpus_seeds import SEED_BY_NAME
    from tests._edit_corpus_toolchains import toolchain

    config = Config(
        std=STANDARD_BY_NAME["c17"],
        producer=toolchain("gcc", 13),
        flags=Flags(bits=32),
        evidence=evidence,
    )
    cell = run_cell(
        OP_BY_NAME["rebuild_optimized"],
        SEED_BY_NAME["c_core"],
        config,
        CellContext.at(tmp_path),
    )
    known = known_mismatch("rebuild_optimized", "c_core", config)
    if evidence == "binary":
        assert cell.verdict == "COMPATIBLE_WITH_RISK", cell.describe()
        assert known is not None and cell.verdict in known.verdicts
    else:
        assert cell.verdict in ("NO_CHANGE", "COMPATIBLE"), cell.describe()
        assert known is None


def _build_c(
    tmp: Path, name: str, cc: str, source: str, flags: list[str], header: str
) -> Path:
    d = tmp / name
    d.mkdir()
    (d / "lib.h").write_text(header)
    (d / "lib.c").write_text(source)
    so = d / "libal.so"
    cmd = [cc, *flags, "-shared", "-fPIC", "-o", str(so), str(d / "lib.c")]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    assert proc.returncode == 0, f"{cmd}\n{proc.stderr}"
    return so


def _dump_c(so: Path, evidence: str, backend: str = "clang"):
    headers = [so.parent / "lib.h"] if evidence == "headers" else []
    return dump(so, headers, compiler="cc", lang="c", header_backend=backend)


def _alignas_drop(tmp_path: Path, cc: str, bits: int, evidence: str, backend: str):
    """Dump an exported object that loses ``_Alignas(16)``; the ``g_data``
    reduction findings, or a skip when the linker kept it 16-aligned."""
    flags = ["-O0", f"-m{bits}", *(["-g"] if evidence != "binary" else [])]
    snaps = []
    for name, spec in (("old", "_Alignas(16) "), ("new", "")):
        header = f"extern {spec}int g_data;\nint get(void);\n"
        source = (
            f'#include "lib.h"\nint pad = 2;\n{spec}int g_data = 1;\n'
            "int get(void) { return g_data; }\n"
        )
        so = _build_c(tmp_path, name, cc, source, flags, header)
        snaps.append(_dump_c(so, evidence, backend))
    new_sym = next(s for s in snaps[1].elf.symbols if s.name == "g_data")
    if new_sym.value_alignment >= 16:
        pytest.skip("the linker kept g_data 16-aligned; nothing was lost")
    found = [
        c
        for c in compare(*snaps).changes
        if c.kind.value == "exported_object_alignment_reduced" and c.symbol == "g_data"
    ]
    return snaps, found


@pytest.mark.parametrize("evidence", _EVIDENCE)
@pytest.mark.parametrize("bits", [64, 32])
@pytest.mark.parametrize("cc", ["gcc", "clang"])
def test_a_real_alignment_loss_still_reports(tmp_path, cc, bits, evidence):
    """Negative control: ``_Alignas(16)`` dropped from an exported object.

    With a declared old alignment (DWARF ``DW_AT_alignment``, or the clang
    header backend's ``AlignedAttr``) the reduction keeps its RISK. Binary
    only, both numbers are placements: the finding is kept and demoted with
    its rule, never dropped.
    """
    _driver(cc)
    if bits == 32:
        _require_m32()
    snaps, found = _alignas_drop(tmp_path, cc, bits, evidence, "clang")
    if evidence == "dwarf":
        old_var = snaps[0].var_by_mangled("g_data")
        assert old_var is not None and old_var.alignment_bits == 128
    assert [c.kind.value for c in found] == ["exported_object_alignment_reduced"]
    if evidence == "binary":
        assert found[0].effective_verdict is Verdict.COMPATIBLE
        assert found[0].modulation_rule == (
            "address-alignment-without-declared-requirement"
        )
    else:
        assert found[0].effective_verdict is None, found[0].modulation_rule


@pytest.mark.known_gap_canary
def test_castxml_headers_hide_a_dropped_alignas(tmp_path):
    """castxml 0.7 emits no ``align`` on a ``Variable``, so the header side
    records an ``_Alignas(16)`` object at its type's natural alignment on both
    sides, and the reduction the binary shows is read as placement and not
    reported (``docs/contribute/known-gaps.md``)."""
    _driver("gcc")
    _snaps, found = _alignas_drop(tmp_path, "gcc", 64, "headers", "castxml")
    assert found == []


_CANARY_SOURCE = (
    "int sum(int n) { char buf[16];\n"
    "  for (int i = 0; i < 16; i++) buf[i] = (char)(n + i);\n"
    "  int s = 0; for (int i = 0; i < 16; i++) s += buf[i]; return s; }\n"
)


def _canary_pair(tmp_path, cc, bits, evidence, old_flags, new_flags, record=()):
    """Dump the same source built with *old_flags* and *new_flags*; the
    ``stack_canary_removed`` findings and whether the binaries lost
    ``__stack_chk_fail`` at all."""
    _driver(cc)
    if bits == 32:
        _require_m32()
    debug = ["-g", *record] if evidence != "binary" else []
    snaps, canary = [], []
    header = "int sum(int n);\n"
    for name, flags in (("old", old_flags), ("new", new_flags)):
        so = _build_c(
            tmp_path, name, cc, _CANARY_SOURCE, [f"-m{bits}", *flags, *debug], header
        )
        snaps.append(_dump_c(so, evidence))
        canary.append(snaps[-1].elf.has_stack_canary)
    found = [
        c for c in compare(*snaps).changes if c.kind.value == "stack_canary_removed"
    ]
    return snaps, found, canary == [True, False]


@pytest.mark.parametrize("evidence", _EVIDENCE)
@pytest.mark.parametrize("bits", [64, 32])
@pytest.mark.parametrize(
    ("cc", "record"),
    [("gcc", ()), ("clang", ()), ("clang", ("-grecord-command-line",))],
)
def test_a_canary_no_frame_needs_is_demoted_only_when_every_unit_records_it(
    tmp_path, cc, record, bits, evidence
):
    """``-O0`` -> ``-O2`` under ``-fstack-protector-strong``: ``-O2`` keeps
    the buffer in registers and needs no canary. The loss is demoted when
    every unit's ``DW_AT_producer`` records the flag (gcc by default, clang
    with ``-grecord-command-line``), and keeps its RISK, marked as weaker
    evidence, when no unit records it (binary only, or clang's default)."""
    flags = "-fstack-protector-strong"
    snaps, found, lost = _canary_pair(
        tmp_path, cc, bits, evidence, ["-O0", flags], ["-O2", flags], record
    )
    if not lost:
        assert found == []
        pytest.skip(f"{cc} -m{bits} -O2 keeps the canary; nothing was lost")
    assert [c.kind.value for c in found] == ["stack_canary_removed"]
    recorded = evidence != "binary" and (cc == "gcc" or record)
    if recorded:
        assert snaps[0].cu_stack_protector == {"strong": 1}
        assert snaps[1].cu_stack_protector == {"strong": 1}
        assert found[0].effective_verdict is Verdict.COMPATIBLE
        assert found[0].modulation_rule == "stack-protector-unchanged-in-every-unit"
    else:
        assert found[0].effective_verdict is None
        assert found[0].review_evidence["stack_protector"] == "unknown"
        assert "weaker evidence" in found[0].review_evidence["note"]


@pytest.mark.parametrize("evidence", ["dwarf", "headers"])
@pytest.mark.parametrize("bits", [64, 32])
@pytest.mark.parametrize("cc", ["gcc", "clang"])
def test_a_dropped_protector_flag_keeps_its_risk(tmp_path, cc, bits, evidence):
    """Negative control (catalog case135's change): ``-fstack-protector-all``
    -> ``-fno-stack-protector`` with debug info recording both is a real
    hardening loss, and the census names it."""
    record = ("-grecord-command-line",) if cc == "clang" else ()
    snaps, found, lost = _canary_pair(
        tmp_path,
        cc,
        bits,
        evidence,
        ["-O0", "-fstack-protector-all"],
        ["-O0", "-fno-stack-protector"],
        record,
    )
    assert lost
    assert snaps[0].cu_stack_protector == {"all": 1}
    assert snaps[1].cu_stack_protector == {"none": 1}
    assert [c.kind.value for c in found] == ["stack_canary_removed"]
    assert found[0].effective_verdict is None
    assert found[0].review_evidence["stack_protector"] == "weakened"
    assert compare(*snaps).verdict is Verdict.COMPATIBLE_WITH_RISK


def test_headerless_additive_change_keeps_placement_alignment_compatible(tmp_path):
    """Formerly a known-gap canary: adding a neighbour moves ``counter``'s
    link-time address, and the address-derived alignment drop gated a
    header-less run at COMPATIBLE_WITH_RISK. Through the CLI the finding is
    kept, with its rule, and the run reads COMPATIBLE."""
    from tests._abicheck_subprocess import abicheck_command, abicheck_env

    _driver("gcc")
    old = "int counter = 1;\nint f(void) { return counter; }\n"
    sides = {
        "old": old,
        "new": old + "int g(void) { return 2; }\nchar extra[3] = {1, 2, 3};\n",
    }
    dumps = []
    for name, source in sides.items():
        so = _build_c(tmp_path, name, "gcc", source, ["-O2"], "int f(void);\n")
        out = so.with_suffix(".json")
        run = subprocess.run(
            abicheck_command("dump", so, "-o", out),
            capture_output=True,
            text=True,
            env=abicheck_env(),
            check=False,
        )
        assert run.returncode == 0, run.stderr
        dumps.append(out)
    report = tmp_path / "report.json"
    run = subprocess.run(
        abicheck_command("compare", *dumps, "-o", f"json={report}"),
        capture_output=True,
        text=True,
        env=abicheck_env(),
        check=False,
    )
    doc = json.loads(report.read_text())
    flagged = [
        c for c in doc["changes"] if c["kind"] == "exported_object_alignment_reduced"
    ]
    assert [c["symbol"] for c in flagged] == ["counter"]
    assert flagged[0]["modulation_rule"] == (
        "address-alignment-without-declared-requirement"
    )
    # Dumped without headers or -g, and the two libraries differ: ADR-090
    # labels the claim UNCONFIRMED (exit 1, that axis alone), and the
    # demoted alignment drop leaves the observed verdict COMPATIBLE.
    assert doc["verdict"] == "UNCONFIRMED"
    assert doc["unconfirmed"]["observed_verdict"] == "COMPATIBLE"
    assert doc["exit"]["reasons"] == ["unconfirmed_compatibility"]
    assert run.returncode == 1, run.stdout


@pytest.mark.parametrize("cc", ["gcc", "clang"])
def test_an_object_linked_without_debug_info_keeps_the_canary_risk(tmp_path, cc):
    """A unit built without ``-g`` has no producer to record: its exported
    function lies in no unit's code range, so the census cannot claim every
    unit kept the protector, and the lost canary keeps its RISK. With every
    object built ``-g`` the same rebuild is demoted (control)."""
    _driver(cc)
    record = ["-grecord-command-line"] if cc == "clang" else []
    extra = "int other(int x) { return x * 7; }\n"
    results = {}
    for debug_both in (True, False):
        snaps = []
        for name, opt in (("old", "-O0"), ("new", "-O2")):
            d = tmp_path / f"{name}-{debug_both}"
            d.mkdir()
            (d / "a.c").write_text(_CANARY_SOURCE)
            (d / "b.c").write_text(extra)
            objs = []
            for src, debug in (("a.c", True), ("b.c", debug_both)):
                obj = d / (src + ".o")
                flags = ["-g", *record] if debug else []
                cmd = [cc, opt, "-fstack-protector-strong", "-fPIC", *flags]
                cmd += ["-c", str(d / src), "-o", str(obj)]
                proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
                assert proc.returncode == 0, f"{cmd}\n{proc.stderr}"
                objs.append(str(obj))
            so = d / "lib.so"
            cmd = [cc, "-shared", "-o", str(so), *objs]
            proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
            assert proc.returncode == 0, f"{cmd}\n{proc.stderr}"
            snaps.append(dump(so, [], compiler="cc", lang="c"))
        found = [
            c for c in compare(*snaps).changes if c.kind.value == "stack_canary_removed"
        ]
        assert [c.kind.value for c in found] == ["stack_canary_removed"]
        results[debug_both] = (snaps[1].cu_stack_protector, found[0])
    census, finding = results[False]
    assert census == {"strong": 1, "unrecorded": 1}
    assert finding.effective_verdict is None
    assert finding.review_evidence["stack_protector"] == "unknown"
    census, finding = results[True]
    assert census == {"strong": 2}
    assert finding.modulation_rule == "stack-protector-unchanged-in-every-unit"


@pytest.mark.parametrize("cc", ["gcc", "clang"])
def test_an_alignas_defined_in_a_later_unit_survives_deduplication(tmp_path, cc):
    """``DW_AT_alignment`` sits only in the unit that defines the object. gcc
    also writes a declaration DIE, without it, in a unit that merely references
    ``extern int g_data``; that DIE wins the de-duplication, and the
    definition's alignment must still be recorded, so a dropped
    ``_Alignas(16)`` keeps its RISK under DWARF."""
    _driver(cc)
    snaps = []
    for name, spec in (("old", "_Alignas(16) "), ("new", "")):
        d = tmp_path / name
        d.mkdir()
        (d / "use.c").write_text(
            "extern int g_data;\nint get(void) { return g_data; }\n"
        )
        (d / "def.c").write_text(f"int pad = 2;\n{spec}int g_data = 1;\n")
        so = d / "lib.so"
        cmd = [cc, "-O0", "-g", "-shared", "-fPIC", "-o", str(so)]
        cmd += [str(d / "use.c"), str(d / "def.c")]
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
        assert proc.returncode == 0, f"{cmd}\n{proc.stderr}"
        snaps.append(dump(so, [], compiler="cc", lang="c"))
    old_var = snaps[0].var_by_mangled("g_data")
    assert old_var is not None and old_var.alignment_bits == 128
    new_sym = next(s for s in snaps[1].elf.symbols if s.name == "g_data")
    if new_sym.value_alignment >= 16:
        pytest.skip("the linker kept g_data 16-aligned; nothing was lost")
    found = [
        c
        for c in compare(*snaps).changes
        if c.kind.value == "exported_object_alignment_reduced" and c.symbol == "g_data"
    ]
    assert [c.kind.value for c in found] == ["exported_object_alignment_reduced"]
    assert found[0].effective_verdict is None


@pytest.mark.parametrize("cc", ["gcc", "clang"])
def test_an_ifunc_from_an_object_without_debug_info_keeps_the_canary_risk(tmp_path, cc):
    """An IFUNC (``STT_GNU_IFUNC``, which pyelftools may spell ``STT_LOOS``)
    whose resolver comes from an object built without ``-g`` is code no unit
    describes, so the census records an unrecorded unit."""
    from abicheck.extract.build_mode_capture import read_elf_build_signals

    _driver(cc)
    record = ["-grecord-command-line"] if cc == "clang" else []
    d = tmp_path / "lib"
    d.mkdir()
    (d / "a.c").write_text(_CANARY_SOURCE)
    (d / "b.c").write_text(
        "static int impl(void) { return 1; }\n"
        "static void *resolve(void) { return (void *)impl; }\n"
        'int picked(void) __attribute__((ifunc("resolve")));\n'
    )
    objs = []
    for src, debug in (("a.c", True), ("b.c", False)):
        obj = d / (src + ".o")
        flags = ["-g", *record] if debug else []
        cmd = [cc, "-O0", "-fstack-protector-strong", "-fPIC", *flags]
        cmd += ["-c", str(d / src), "-o", str(obj)]
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
        assert proc.returncode == 0, f"{cmd}\n{proc.stderr}"
        objs.append(str(obj))
    so = d / "lib.so"
    proc = subprocess.run(
        [cc, "-shared", "-o", str(so), *objs],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    signals = read_elf_build_signals(so)
    assert signals is not None
    assert signals.stack_protector == {"strong": 1, "unrecorded": 1}


@pytest.mark.parametrize("cxx", ["g++", "clang++"])
def test_a_class_static_member_keeps_its_dwarf_alignment(tmp_path, cxx):
    """An out-of-line ``alignas(16) int A::x`` definition: clang writes the
    alignment (and the linkage name) only on the definition DIE, which has no
    name of its own. Read with its ``DW_AT_specification`` declaration, the
    variable is recorded as ``A::x`` with 128 bits declared; gcc emits no
    ``DW_AT_alignment`` for it, so its declared alignment stays unknown."""
    _driver(cxx)
    snaps = []
    for name, spec in (("old", "alignas(16) "), ("new", "")):
        d = tmp_path / name
        d.mkdir()
        (d / "a.cpp").write_text(
            "struct A { static int x; };\n"
            f"int pad = 2;\n{spec}int A::x = 1;\n"
            "int get() { return A::x; }\n"
        )
        so = d / "lib.so"
        cmd = [cxx, "-O0", "-g", "-shared", "-fPIC", "-o", str(so), str(d / "a.cpp")]
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
        assert proc.returncode == 0, f"{cmd}\n{proc.stderr}"
        snaps.append(dump(so, [], compiler="c++", lang="c++"))
    old_var = snaps[0].var_by_mangled("_ZN1A1xE")
    assert old_var is not None and old_var.name == "A::x"
    expected = 128 if cxx == "clang++" else None
    assert old_var.alignment_bits == expected
    new_sym = next(s for s in snaps[1].elf.symbols if s.name == "_ZN1A1xE")
    if new_sym.value_alignment >= 16:
        pytest.skip("the linker kept A::x 16-aligned; nothing was lost")
    found = [
        c
        for c in compare(*snaps).changes
        if c.kind.value == "exported_object_alignment_reduced"
        and c.symbol == "_ZN1A1xE"
    ]
    assert [c.kind.value for c in found] == ["exported_object_alignment_reduced"]
    if cxx == "clang++":
        assert found[0].effective_verdict is None
    else:
        assert found[0].modulation_rule == (
            "address-alignment-without-declared-requirement"
        )
