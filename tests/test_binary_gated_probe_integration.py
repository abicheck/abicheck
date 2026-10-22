"""ADR-084: probe the ``BINARY_GATED`` / ``HEADER_OR_SOURCE_GATED`` split on real binaries.

``policy/binary_gated_contract.py`` sorts every ``API_BREAK`` kind by reading
its producer. This module checks that sorting empirically. For each kind in
:data:`PROBES` it compiles a minimal old/new shared library pair with ``-g``,
runs ``abicheck compare`` twice through the CLI (binary plus DWARF only, then
with the headers), and asserts:

* (a) every ``API_BREAK`` kind a binary-only run produces is in
  ``BINARY_GATED``. This is the direction the gate cannot recover from: a
  ``HEADER_OR_SOURCE_GATED`` kind produced binary-only would keep a false
  ``API_BREAK``. It is checked over *every* finding of every probe, not only
  the probed kind;
* (b) a ``BINARY_GATED`` kind the binary-only run produced ends at
  ``COMPATIBLE_WITH_RISK`` there and is ``API_BREAK`` in the header run.

A ``HEADER_OR_SOURCE_GATED`` probe additionally asserts its kind is absent
binary-only and, where the fixture reaches it, present with headers (the
positive control: the fixture really is a trigger).

:data:`UNPROBED` lists every ``API_BREAK`` kind without a probe and why. It is
a shrink-only ratchet: the completeness test fails on a kind in neither table,
and on a kind in both.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

from abicheck.change_registry import API_BREAK_KINDS
from abicheck.policy.binary_gated_contract import (
    BINARY_GATED,
    HEADER_OR_SOURCE_GATED,
    OUT_OF_AXIS,
)

pytestmark = [
    pytest.mark.integration,
    # REACHED_BINARY_ONLY records what an ELF + DWARF binary-only run produces.
    # On macOS the DWARF stays in the object files unless dsymutil runs, so a
    # binary-only Mach-O run reaches a different set; the table is ELF-specific.
    pytest.mark.skipif(sys.platform != "linux", reason="ELF + DWARF probe table"),
]

_REPO = Path(__file__).resolve().parent.parent
_CASES = _REPO / "catalog" / "cases"

_API_BREAK_VALUES = {k.value for k in API_BREAK_KINDS}
_BINARY_GATED_VALUES = {k.value for k in BINARY_GATED}
_HEADER_GATED_VALUES = {k.value for k in HEADER_OR_SOURCE_GATED}


@dataclass(frozen=True)
class Probe:
    """An old/new library pair expected to trigger one kind.

    Either ``case`` names a ``catalog/cases`` directory (``v1.h``/``v1.cpp``,
    ``v2.h``/``v2.cpp``), or the four inline texts are used. Inline sources
    are prefixed with ``#include "a.h"``.
    """

    case: str | None = None
    old_h: str = ""
    new_h: str = ""
    old_src: str = ""
    new_src: str | None = None
    #: Whether the header run is expected to reach the kind (positive control).
    header_reaches: bool = True


def _inline(
    old_h: str,
    new_h: str,
    old_src: str = "",
    new_src: str | None = None,
    *,
    header_reaches: bool = True,
) -> Probe:
    return Probe(
        old_h=old_h,
        new_h=new_h,
        old_src=old_src,
        new_src=new_src,
        header_reaches=header_reaches,
    )


#: Fixture table keyed by ``ChangeKind`` value.
PROBES: dict[str, Probe] = {
    # -- BINARY_GATED: DWARF-level type, field, enum, member, function facts --
    "type_became_abstract": _inline(
        "struct S { virtual ~S(); virtual int f(); int x; };\nint use(S*);",
        "struct S { virtual ~S(); virtual int f() = 0; int x; };\nint use(S*);",
        "S::~S() {}\nint S::f() { return 1; }\nint use(S* s) { return s->x; }",
        "S::~S() {}\nint use(S* s) { return s->x; }",
    ),
    "type_became_final": Probe(case="case125_class_became_final"),
    "typedef_removed": _inline(
        "typedef int Handle;\nstruct S { Handle h; };\nint use(S*);",
        "struct S { int h; };\nint use(S*);",
        "int use(S* s) { return s->h; }",
    ),
    "enum_became_scoped": _inline(
        "enum E { A, B };\nstruct S { E e; };\nint use(S*);",
        "enum class E { A, B };\nstruct S { E e; };\nint use(S*);",
        "int use(S* s) { return (int)s->e; }",
    ),
    "enum_member_renamed": Probe(case="case31_enum_rename"),
    "field_access_changed": _inline(
        "class S { public: int a; int b; int get(); };\nint use(S*);",
        "class S { public: int a; int get(); private: int b; };\nint use(S*);",
        "int S::get() { return b; }\nint use(S* s) { return s->a; }",
    ),
    "field_became_const": _inline(
        "struct S { int a; int b; };\nint use(S*);",
        "struct S { const int a; int b; };\nint use(S*);",
        "int use(S* s) { return s->a; }",
    ),
    "field_lost_mutable": _inline(
        "struct S { mutable int a; int b; };\nint use(S*);",
        "struct S { int a; int b; };\nint use(S*);",
        "int use(S* s) { return s->a; }",
    ),
    "field_renamed": Probe(case="case35_field_rename"),
    "method_access_changed": Probe(case="case34_access_level"),
    "var_access_changed": _inline(
        "class S { public: static int v; int get(); };\nint use(S*);",
        "class S { public: int get(); private: static int v; };\nint use(S*);",
        "int S::v = 1;\nint S::get() { return v; }\nint use(S* s) { return s->get(); }",
    ),
    "ctor_explicit_added": Probe(case="case106_ctor_became_explicit"),
    "func_became_inline": _inline(
        "int f(int);\nint g(int);",
        "inline int f(int x) { return x + 1; }\nint g(int);",
        "int f(int x) { return x + 1; }\nint g(int x) { return f(x); }",
        "int g(int x) { return f(x); }",
    ),
    "func_return_pointee_qualifier_added": Probe(
        case="case211_return_pointee_gained_const"
    ),
    "func_return_pointee_qualifier_changed": Probe(
        case="case215_return_double_pointer_lost_const"
    ),
    "param_pointee_qualifier_changed": _inline(
        'extern "C" int f(const int* p);',
        'extern "C" int f(volatile int* p);',
        'extern "C" int f(const int* p) { return *p; }',
        'extern "C" int f(volatile int* p) { return *p; }',
    ),
    "hidden_friend_removed": Probe(case="case96_hidden_friend_removed"),
    "removed_const_overload": _inline(
        "struct S { int g(); int g() const; int x; };",
        "struct S { int g(); int x; };",
        "int S::g() { return x; }\nint S::g() const { return x; }",
        "int S::g() { return x; }",
    ),
    "source_level_kind_changed": _inline(
        "struct S { int a; };\nint use(S*);",
        "class S { public: int a; };\nint use(S*);",
        "int use(S* s) { return s->a; }",
    ),
    "tag_type_renamed": Probe(case="case86_tag_struct_renamed"),
    "type_removed": _inline(
        'struct S { int a; };\nextern "C" int use(S*);',
        'extern "C" int use(void*);',
        "int use(S* s) { return s->a; }",
        "int use(void* s) { return *(int*)s; }",
    ),
    "inline_function_removed": _inline(
        '__attribute__((visibility("hidden"))) inline int helper(int x) { return x * 2; }\nint f(int);',
        "int f(int);",
        "int f(int x) { return helper(x); }",
        "int f(int x) { return x * 2; }",
    ),
    "experimental_removed_without_replacement": Probe(
        case="case100_experimental_removed_without_replacement"
    ),
    # -- HEADER_OR_SOURCE_GATED: the NON_BINARY_REASONS rows to probe first --
    "param_default_value_removed": Probe(case="case123_default_argument_removed"),
    "constant_changed": Probe(case="case124_header_constant_value_changed"),
    "constant_removed": _inline(
        "namespace lib { constexpr int kMax = 8;\nint use(int); }",
        "namespace lib { int use(int); }",
        "namespace lib { int use(int n) { return n * 8; } }",
    ),
    "default_argument_changed": _inline(
        "int f(int x = 3);",
        "int f(int x = 4);",
        "int f(int x) { return x; }",
        header_reaches=False,  # producer is L4 source replay, not castxml
    ),
    "public_macro_removed": _inline(
        "#define LIB_MAX 8\nint f(int);",
        "int f(int);",
        "int f(int x) { return x; }",
        header_reaches=False,  # producer is L4 source replay
    ),
    "public_typedef_removed": _inline(
        "typedef int Handle;\nHandle f(Handle);",
        "int f(int);",
        "int f(int x) { return x; }",
        header_reaches=False,  # producer is L4 source replay
    ),
}

#: ``API_BREAK`` kinds without a probe, and why. Shrink-only: add a probe to
#: :data:`PROBES` and delete the row here; never add a row for a kind that
#: could be probed with a pair of C/C++ libraries.
UNPROBED: dict[str, str] = {
    # BINARY_GATED
    "mandatory_template_param_added": "probed at binary depth by ADR-084's own probes; needs exported template instantiations whose arity grows, covered in test_binary_gated_contract",
    "unspecified_return_now_named": "probed at binary depth by ADR-084's own probes (exported-name evidence); no catalog case",
    "std_reexport_removed": "needs a public `using std::x;` re-export whose detector works on header-observed namespace shape; no binary-only trigger is known",
    # HEADER_OR_SOURCE_GATED needing inputs a compare of two .so files cannot carry
    "concept_tightened": "L4 source replay (--sources); not reachable from a .so pair",
    "template_param_default_changed": "header-only: needs class-template default arguments recorded from the clang header parse (ADR-085 D1), which a .so pair does not carry; covered by test_template_param_defaults and test_default_template_arg_l2_matrix",
    "constexpr_value_changed": "L4 source replay (--sources); not reachable from a .so pair",
    "public_typedef_target_changed": "L4 source replay (--sources); not reachable from a .so pair",
    "public_macro_value_changed": "L4 source replay (--sources); not reachable from a .so pair",
    "uninstantiated_template_removed": "L4 source replay (--sources); not reachable from a .so pair",
    "header_build_context_mismatch": "needs build data (compile_commands) next to headers",
    "odr_type_variant": "needs per-TU source ABI from L4 replay",
    "cxx_standard_floor_raised": "needs build configuration input",
}

#: ``BINARY_GATED`` probes whose binary-only (DWARF, no headers) run produces
#: the kind (probed with gcc at -O0 -g). The other ``BINARY_GATED`` probes
#: reach their kind only with headers: DWARF does not carry ``final``,
#: ``explicit``, ``mutable``, ``= 0``, enum scoping, access of a static member,
#: or return/parameter pointee qualifiers in the form the detectors read. Their
#: ``BINARY_GATED`` placement is therefore conservative (a binary-only run
#: never produces them, so the gate never acts) and is kept: moving a kind out
#: needs proof that *no* binary-only input produces it, which one fixture
#: cannot give, while leaving it costs nothing (ADR-084 "Consequences").
REACHED_BINARY_ONLY: frozenset[str] = frozenset(
    {
        "enum_member_renamed",
        "field_access_changed",
        "field_became_const",
        "field_renamed",
        "source_level_kind_changed",
        "tag_type_renamed",
        "type_removed",
        "typedef_removed",
    }
)


def _compile(src: Path, include: Path, out: Path) -> None:
    compiler = "gcc" if src.suffix == ".c" else "g++"
    cmd = [compiler, "-g", "-O0", "-shared", "-fPIC", "-fvisibility=default"]
    if compiler == "g++":
        cmd.append("-std=c++20")
    cmd += ["-I", str(include), "-o", str(out), str(src)]
    r = subprocess.run(cmd, capture_output=True, text=True, check=False)
    # A configured compiler that ran and failed is a fixture bug, not a skip.
    assert r.returncode == 0, f"{' '.join(cmd)}\n{src.read_text()}\n{r.stderr}"


def _materialize(kind: str, probe: Probe, root: Path) -> tuple[Path, Path, Path, Path]:
    """Build the pair; return (old_lib, new_lib, old_header, new_header)."""
    out: list[Path] = []
    headers: list[Path] = []
    for tag in ("v1", "v2"):
        d = root / kind / tag
        d.mkdir(parents=True)
        if probe.case is not None:
            case = _CASES / probe.case
            src = next(
                p for p in (case / f"{tag}.cpp", case / f"{tag}.c") if p.exists()
            )
            header = next(
                p for p in (case / f"{tag}.h", case / f"{tag}.hpp") if p.exists()
            )
            include = case
        else:
            header = d / "a.h"
            header.write_text((probe.old_h if tag == "v1" else probe.new_h) + "\n")
            body = (
                probe.old_src if tag == "v1" or probe.new_src is None else probe.new_src
            )
            src = d / "a.cpp"
            src.write_text('#include "a.h"\n' + body + "\n")
            include = d
        lib = d / "liba.so"
        _compile(src, include, lib)
        out.append(lib)
        headers.append(header)
    return out[0], out[1], headers[0], headers[1]


def _compare(
    old: Path, new: Path, report: Path, headers: tuple[Path, Path] | None
) -> list[dict]:
    cmd = [
        sys.executable,
        "-m",
        "abicheck",
        "compare",
        str(old),
        str(new),
        "-o",
        f"json={report}",
    ]
    if headers is not None:
        cmd += ["--header", f"old={headers[0]}", "--header", f"new={headers[1]}"]
    r = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=600)
    assert r.returncode in (0, 2, 4), f"{cmd}\n{r.stderr[-2000:]}"
    return json.loads(report.read_text())["changes"]


@dataclass(frozen=True)
class Outcome:
    binary_only: list[dict]
    with_headers: list[dict]


@pytest.fixture(scope="module")
def outcomes(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Outcome]:
    if shutil.which("g++") is None or shutil.which("castxml") is None:
        pytest.skip("g++ and castxml are required")
    root = tmp_path_factory.mktemp("binary_gated_probe")
    results: dict[str, Outcome] = {}
    for kind, probe in PROBES.items():
        old, new, old_h, new_h = _materialize(kind, probe, root)
        results[kind] = Outcome(
            binary_only=_compare(old, new, root / kind / "b.json", None),
            with_headers=_compare(old, new, root / kind / "h.json", (old_h, new_h)),
        )
    return results


def _of_kind(changes: list[dict], kind: str) -> list[dict]:
    return [c for c in changes if c["kind"] == kind]


def test_tables_partition_api_break_kinds() -> None:
    """Every API_BREAK kind outside OUT_OF_AXIS is probed or justified, never both."""
    in_scope = _API_BREAK_VALUES - {k.value for k in OUT_OF_AXIS}
    assert set(PROBES) <= in_scope
    assert not set(PROBES) & set(UNPROBED)
    assert set(PROBES) | set(UNPROBED) == in_scope, sorted(
        in_scope - set(PROBES) - set(UNPROBED)
    )
    assert all(UNPROBED.values()), "every unprobed kind needs a justification"


def test_unprobed_ratchet_is_shrink_only() -> None:
    """The unprobed list may only shrink (raise the probe count instead)."""
    assert len(UNPROBED) <= 11, sorted(UNPROBED)


@pytest.mark.parametrize("kind", sorted(PROBES))
def test_binary_only_api_breaks_are_binary_gated(
    kind: str, outcomes: dict[str, Outcome]
) -> None:
    """(a) a binary-only run produces no API_BREAK kind the gate does not cover."""
    leaked = sorted(
        {
            c["kind"]
            for c in outcomes[kind].binary_only
            if c["kind"] in _API_BREAK_VALUES
        }
        - _BINARY_GATED_VALUES
        - {k.value for k in OUT_OF_AXIS}
    )
    assert leaked == [], (
        f"{kind}: header/source-gated kinds produced binary-only: {leaked}"
    )
    kept = [
        c["kind"]
        for c in outcomes[kind].binary_only
        if c.get("compatibility_decision") == "API_BREAK"
    ]
    assert kept == [], f"{kind}: binary-only run kept API_BREAK for {kept}"


@pytest.mark.parametrize("kind", sorted(k for k in PROBES if k in _BINARY_GATED_VALUES))
def test_binary_gated_kind_demoted_then_restored(
    kind: str, outcomes: dict[str, Outcome]
) -> None:
    """(b) produced binary-only -> COMPATIBLE_WITH_RISK; with headers -> API_BREAK.

    Whether the fixture reaches the kind binary-only is pinned by
    :data:`REACHED_BINARY_ONLY`, so a detector that gains or loses binary
    reach shows up here instead of passing silently.
    """
    produced = _of_kind(outcomes[kind].binary_only, kind)
    assert bool(produced) == (kind in REACHED_BINARY_ONLY), (
        f"{kind}: binary-only reach changed; update REACHED_BINARY_ONLY "
        f"(and the known-gaps record) after checking the gate still holds"
    )
    if produced:
        assert {c["compatibility_decision"] for c in produced} == {
            "COMPATIBLE_WITH_RISK"
        }
        assert {c["modulation_rule"] for c in produced} == {"binary_gated_api_break"}
    with_headers = _of_kind(outcomes[kind].with_headers, kind)
    assert with_headers, f"{kind}: fixture is not a trigger even with headers"
    assert {c["compatibility_decision"] for c in with_headers} == {"API_BREAK"}


@pytest.mark.parametrize("kind", sorted(k for k in PROBES if k in _HEADER_GATED_VALUES))
def test_header_gated_kind_absent_binary_only(
    kind: str, outcomes: dict[str, Outcome]
) -> None:
    """A HEADER_OR_SOURCE_GATED kind is not produced binary-only; the fixture is a real trigger."""
    assert _of_kind(outcomes[kind].binary_only, kind) == []
    if PROBES[kind].header_reaches:
        assert {
            c["compatibility_decision"]
            for c in _of_kind(outcomes[kind].with_headers, kind)
        } == {"API_BREAK"}
