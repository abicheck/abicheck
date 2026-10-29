"""A value-bearing declaration form must not be silently skipped (bug class
``extraction.value_bearing_form_silently_skipped``).

Each form below carries a value a consumer can observe (an initializer the
header or the object defines). Changing that value from 1 to 2 must produce a
finding that names the qualified entity; an identical value must produce none.

Oracle: the compiler, not an abicheck helper. Each version is compiled with a
probe that prints the value, and the test first checks the probe output
differs for the changed pair and matches for the identical pair. Only then does
it assert what abicheck reports.

Lanes: ``headers-no-g`` (headers, binary without DWARF), ``headers-g`` (headers,
binary with DWARF), and ``dump+compare`` (``abicheck dump`` each side, then
``abicheck compare`` the two snapshots). Every lane runs the public CLI in a
subprocess with the checkout under test on ``PYTHONPATH`` and a private cache.

Every form, the variable template included, must yield exactly one finding that
names the qualified entity: the constant channel and the value channel for the
same declaration are one observation (``compare.finding_dedup``).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

from tests._abicheck_subprocess import abicheck_command

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not sys.platform.startswith("linux"), reason="g++ ELF fixture"),
]

_REPO_ROOT = Path(__file__).resolve().parents[1]

#: Change kinds that carry a value entity. ``constant_changed`` is the constant
#: channel; ``var_value_changed`` is the variable value channel.
_VALUE_KINDS = frozenset({"constant_changed", "var_value_changed"})

_LANES = ("headers-no-g", "headers-g", "dump+compare")


@dataclass(frozen=True)
class Form:
    """One declaration form. ``{V}`` in *header* and *source* is the value."""

    key: str
    qualified: str  # the qualified entity a finding must name
    header: str
    source: str  # #includes the header; defines the accessor
    expr: str  # C++ expression whose value the probe prints
    fmt: str  # printf conversion for expr
    values: tuple[str, str]  # (old, new)


FORMS: tuple[Form, ...] = (
    Form(
        key="ns_constexpr",
        qualified="ns::kLimit",
        header="namespace ns { constexpr int kLimit = {V}; }\n",
        source='#include "{h}"\nint use_limit() { return ns::kLimit; }\n',
        expr="ns::kLimit",
        fmt="%d",
        values=("1", "2"),
    ),
    Form(
        key="static_constexpr_member",
        qualified="Cfg::kMax",
        header="struct Cfg { static constexpr int kMax = {V}; };\n",
        source='#include "{h}"\nint use_max() { return Cfg::kMax; }\n',
        expr="Cfg::kMax",
        fmt="%d",
        values=("1", "2"),
    ),
    Form(
        key="inline_var",
        qualified="iv_var",
        header="inline int iv_var = {V};\n",
        source='#include "{h}"\nint use_iv() { return iv_var; }\n',
        expr="iv_var",
        fmt="%d",
        values=("1", "2"),
    ),
    Form(
        key="inline_const_var",
        qualified="icv",
        header="inline const int icv = {V};\n",
        source='#include "{h}"\nint use_icv() { return icv; }\n',
        expr="icv",
        fmt="%d",
        values=("1", "2"),
    ),
    Form(
        key="extern_const_defined_in_cpp",
        qualified="ecv",
        header="extern const int ecv;\n",
        source='#include "{h}"\nconst int ecv = {V};\nint use_ecv() { return ecv; }\n',
        expr="ecv",
        fmt="%d",
        values=("1", "2"),
    ),
    Form(
        key="static_const_member_out_of_line",
        qualified="Oc::kOut",
        header="struct Oc { static const int kOut; };\n",
        source=(
            '#include "{h}"\nconst int Oc::kOut = {V};\n'
            "int use_oc() { return Oc::kOut; }\n"
        ),
        expr="Oc::kOut",
        fmt="%d",
        values=("1", "2"),
    ),
    Form(
        key="nested_ns_constexpr",
        qualified="a::b::kDeep",
        header="namespace a { namespace b { constexpr int kDeep = {V}; } }\n",
        source='#include "{h}"\nint use_deep() { return a::b::kDeep; }\n',
        expr="a::b::kDeep",
        fmt="%d",
        values=("1", "2"),
    ),
    Form(
        key="variable_template",
        qualified="vt",
        header="template <class T> inline constexpr int vt = {V};\n",
        source='#include "{h}"\nint use_vt() { return vt<int>; }\n',
        expr="vt<int>",
        fmt="%d",
        values=("1", "2"),
    ),
    Form(
        key="enum_class_constexpr",
        qualified="ns::kColor",
        header=(
            "namespace ns { enum class Color : int { Red = 1, Blue = 2 }; "
            "constexpr Color kColor = static_cast<Color>({V}); }\n"
        ),
        source=(
            '#include "{h}"\nint use_color() { return static_cast<int>(ns::kColor); }\n'
        ),
        expr="static_cast<int>(ns::kColor)",
        fmt="%d",
        values=("1", "2"),
    ),
    Form(
        key="const_char_ptr_const",
        qualified="ns::kName",
        header='namespace ns { constexpr const char* const kName = "{V}"; }\n',
        source='#include "{h}"\nconst char* use_name() { return ns::kName; }\n',
        expr="ns::kName",
        fmt="%s",
        values=("one", "two"),
    ),
)


_CASES = [
    pytest.param(form, lane, case, id=f"{form.key}-{lane}-{case}")
    for form in FORMS
    for lane in _LANES
    for case in ("changed", "identical")
]


def _toolchain_or_skip() -> None:
    for tool in ("g++", "castxml"):
        if shutil.which(tool) is None:
            pytest.skip(f"{tool} not on PATH")


def _run(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=cwd,
        env={
            **os.environ,
            "ABICHECK_AST_FRONTEND": "castxml",
            # A private cache: a whole-snapshot hit from another build of this
            # code would return facts this run's producer never computed.
            "ABICHECK_CACHE_DIR": str(cwd / ".abicheck-cache"),
            "PYTHONPATH": os.pathsep.join(
                p for p in (str(_REPO_ROOT), os.environ.get("PYTHONPATH", "")) if p
            ),
        },
        capture_output=True,
        text=True,
        timeout=600,
    )


def _side(form: Form, d: Path, name: str, value: str, debug: bool) -> tuple[Path, Path]:
    """Write ``name.h``/``name.cpp`` for *value* and build ``name.so``."""
    header = d / f"{name}.h"
    source = d / f"{name}.cpp"
    header.write_text(form.header.replace("{V}", value))
    source.write_text(form.source.replace("{h}", header.name).replace("{V}", value))
    so = d / f"{name}.so"
    cmd = ["g++", "-std=c++17", "-shared", "-fPIC", str(source), "-o", str(so)]
    if debug:
        cmd.insert(1, "-g")
    built = _run(cmd, d)
    assert built.returncode == 0, built.stderr
    return header, so


def _probe_output(form: Form, d: Path, name: str, value: str) -> str:
    """The value the compiler sees, printed by a probe built from the same source."""
    header = d / f"{name}.h"
    probe_src = d / f"{name}_probe.cpp"
    probe_src.write_text(
        form.source.replace("{h}", header.name).replace("{V}", value)
        + "\n#include <cstdio>\n"
        + f'int main() {{ std::printf("{form.fmt}\\n", {form.expr}); }}\n'
    )
    exe = d / f"{name}_probe"
    built = _run(["g++", "-std=c++17", str(probe_src), "-o", str(exe)], d)
    assert built.returncode == 0, built.stderr
    ran = _run([str(exe)], d)
    assert ran.returncode == 0, ran.stderr
    return ran.stdout.strip()


def _findings_naming(changes: list[dict], qualified: str) -> list[dict]:
    return [
        c
        for c in changes
        if c.get("kind") in _VALUE_KINDS
        and qualified in (c.get("symbol"), c.get("demangled_symbol"))
    ]


def _compare(
    form: Form, lane: str, d: Path, old: tuple[Path, Path], new: tuple[Path, Path]
) -> list[dict]:
    (old_h, old_so), (new_h, new_so) = old, new
    if lane == "dump+compare":
        for name, header, so in (("old", old_h, old_so), ("new", new_h, new_so)):
            dumped = _run(
                abicheck_command(
                    "dump",
                    str(so),
                    "-H",
                    str(header),
                    "-o",
                    str(d / f"{name}.json"),
                ),
                d,
            )
            assert dumped.returncode == 0, dumped.stderr
        args = abicheck_command(
            "compare",
            str(d / "old.json"),
            str(d / "new.json"),
        )
    else:
        args = abicheck_command(
            "compare",
            str(old_so),
            str(new_so),
            "-H",
            f"old={old_h}",
            "-H",
            f"new={new_h}",
        )
    out = d / "report.json"
    result = _run([*args, "-o", f"json={out}"], d)
    assert out.exists(), result.stderr[-800:]
    return json.loads(out.read_text())["changes"]


@pytest.mark.parametrize(("form", "lane", "case"), _CASES)
def test_value_change_is_reported_once_and_identical_value_is_not(
    form: Form, lane: str, case: str, tmp_path: Path
) -> None:
    _toolchain_or_skip()
    debug = lane != "headers-no-g"
    old_value, new_value = form.values
    if case == "identical":
        new_value = old_value
    d = tmp_path
    old = _side(form, d, "old", old_value, debug)
    new = _side(form, d, "new", new_value, debug)

    # Oracle: the compiler agrees the value really differs (or really does not).
    probe_old = _probe_output(form, d, "old", old_value)
    probe_new = _probe_output(form, d, "new", new_value)
    if case == "changed":
        assert probe_old != probe_new, "the probe must observe the value change"
    else:
        assert probe_old == probe_new

    matched = _findings_naming(_compare(form, lane, d, old, new), form.qualified)
    if case == "identical":
        assert matched == [], f"identical value reported: {matched}"
    else:
        assert len(matched) == 1, (
            f"expected exactly one finding naming {form.qualified}, got {matched}"
        )
