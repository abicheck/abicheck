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

"""Integration tests for the clang L2 header backend (ADR-003 extension).

These run the **live** ``clang -ast-dump=json`` frontend over real headers and a
real ELF shared library, and assert two things the unit lane cannot:

1. A clang-only host still produces a header-aware (``from_headers``) snapshot
   with public-surface scoping — the P1 gap from the UXL field run.
2. The clang- and castxml-derived snapshots are **schema-equivalent enough** to
   act as a parity oracle: the same public functions, types, enums, and typedefs
   surface from both frontends.

Gated on clang + g++ being present; skipped otherwise.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from abicheck.checker import ChangeKind, Verdict, compare
from abicheck.dumper import dump
from abicheck.model import Visibility

# Scoped to **Linux/ELF** — the clang L2 backend's target (P1: clang-only Linux
# CI images). The cross-platform binary-build conventions diverge in ways
# unrelated to the backend logic: on macOS clang/g++ emit Mach-O dylibs, and on
# Windows clang defaults to the **MSVC** ABI (``?add@lib@@…``) while MinGW g++
# exports **Itanium** names, so a clang-built AST and a g++-built binary use
# different mangling schemes and never match. The pure-parser unit suite
# (``test_dumper_clang.py``) covers the backend logic on every platform.
#
# NB: deliberately *not* marked ``integration`` — that marker's Linux gate
# requires castxml (tests/conftest.py ``_integration_skip_reason``), but the
# whole point here is the **castxml-absent** host. Each test instead self-skips
# on its own real tool requirement (clang + g++; the parity test additionally
# needs castxml).
pytestmark = pytest.mark.skipif(
    not sys.platform.startswith("linux"),
    reason="clang L2 backend integration test is ELF/Linux-scoped (see module docstring)",
)

_HEADER = """
#pragma once
namespace lib {

struct Point { int x; int y; };

enum class Color { Red, Green = 5, Blue };

int add(int a, int b) noexcept;
void scale(Point* p, double factor);

constexpr int kVersion = 3;

using handle_t = int;

class Widget {
public:
    int value() const;
private:
    int hidden_;
};

}  // namespace lib
"""

_SOURCE = """
#include "api.h"
namespace lib {
int add(int a, int b) noexcept { return a + b; }
void scale(Point* p, double factor) { p->x = int(p->x * factor); }
int Widget::value() const { return hidden_; }
}  // namespace lib
"""


def _have(tool: str) -> bool:
    return shutil.which(tool) is not None


@pytest.fixture
def built_lib(tmp_path: Path) -> tuple[Path, Path]:
    """Build a tiny ELF .so + its public header, returning (so_path, header)."""
    if not (_have("clang") and _have("g++")):
        pytest.skip(
            "clang and g++ are required for the clang L2 backend integration test"
        )
    header = tmp_path / "api.h"
    header.write_text(_HEADER)
    src = tmp_path / "api.cpp"
    src.write_text(_SOURCE)
    so = tmp_path / "libapi.so"
    subprocess.run(
        ["g++", "-shared", "-fPIC", "-o", str(so), str(src), f"-I{tmp_path}"],
        check=True,
        capture_output=True,
    )
    return so, header


def test_clang_backend_produces_header_aware_snapshot(
    built_lib: tuple[Path, Path],
) -> None:
    so, header = built_lib
    snap = dump(so, [header], header_backend="clang")
    assert snap.from_headers is True
    names = {f.name for f in snap.functions}
    assert {"add", "scale"} <= names
    # noexcept + public scoping flowed through the clang frontend.
    add = next(f for f in snap.functions if f.name == "add")
    assert add.is_noexcept is True
    assert add.visibility == Visibility.PUBLIC
    assert "lib::kVersion" in snap.constants
    assert snap.constants["lib::kVersion"] == "3"
    assert "handle_t" in snap.typedefs


def test_clang_backend_recovers_c_anonymous_typedef_enum(tmp_path: Path) -> None:
    """Regression: clang fallback must not drop ``typedef enum {…} name``.

    Some newer distro toolchains can make castxml fail before abicheck falls
    back to the clang header backend.  case31_enum_rename depends on the enum
    surviving that fallback path, so assert the live clang AST shape here.
    """
    if not (_have("clang") and _have("gcc")):
        pytest.skip("clang and gcc are required for the C enum fallback regression")
    header = tmp_path / "log.h"
    header.write_text(
        """
        typedef enum {
            LOG_NONE = 0,
            LOG_ERR = 1,
            LOG_WARN = 2,
        } log_level_t;
        extern log_level_t default_log_level;
        """
    )
    src = tmp_path / "log.c"
    src.write_text(
        """
        #include "log.h"
        log_level_t default_log_level = LOG_ERR;
        """
    )
    so = tmp_path / "liblog.so"
    subprocess.run(
        ["gcc", "-shared", "-fPIC", "-o", str(so), str(src), f"-I{tmp_path}"],
        check=True,
        capture_output=True,
    )

    snap = dump(so, [header], header_backend="clang")
    enums = {e.name: e for e in snap.enums}
    assert "log_level_t" in enums
    assert [(m.name, m.value) for m in enums["log_level_t"].members] == [
        ("LOG_NONE", 0),
        ("LOG_ERR", 1),
        ("LOG_WARN", 2),
    ]
    assert {v.name: v.type for v in snap.variables}[
        "default_log_level"
    ] == "log_level_t"


def test_clang_and_castxml_snapshots_agree_on_public_surface(
    built_lib: tuple[Path, Path],
) -> None:
    if not _have("castxml"):
        pytest.skip("castxml required for the clang↔castxml parity oracle")
    so, header = built_lib
    clang_snap = dump(so, [header], header_backend="clang")
    castxml_snap = dump(so, [header], header_backend="castxml")

    def public_funcs(snap: object) -> set[str]:
        return {
            f.mangled
            for f in snap.functions  # type: ignore[attr-defined]
            if f.visibility == Visibility.PUBLIC
        }

    # The exported (public) function set must match between frontends.
    assert public_funcs(clang_snap) == public_funcs(castxml_snap)
    # Both see the same named record and enum types.
    assert {t.name for t in clang_snap.types} >= {"Point", "Widget"}
    assert {t.name for t in castxml_snap.types} & {
        t.name for t in clang_snap.types
    } >= {
        "Point",
        "Widget",
    }
    assert {e.name for e in clang_snap.enums} == {e.name for e in castxml_snap.enums}


def test_clang_backend_still_false_positives_case61_alignment_risk(
    tmp_path: Path,
) -> None:
    """Known gap (Codex review): the case61_var_added false-positive fix in
    dumper_castxml.py (_type_alignment_bits, a Variable's natural type
    alignment as declared-alignment corroboration for
    EXPORTED_OBJECT_ALIGNMENT_REDUCED) has no clang-frontend equivalent.

    clang's ``-ast-dump=json`` does not compute type layout at all (see this
    module's and dumper_clang.py's docstrings) -- there is no compiler-derived
    alignment to fall back to the way castxml's XML always carries one, so
    _clang_var_alignment_bits can only ever report an *explicit*
    ``AlignedAttr`` override, never a natural one. This is a pre-existing
    architectural boundary of the clang JSON-AST backend, not a regression
    from the castxml fix. This test locks in the current (undesired but
    honest) behavior so a future attempt to close it changes a real
    assertion instead of silently going unnoticed.
    """
    if not (_have("clang") and _have("gcc")):
        pytest.skip(
            "clang and gcc are required for the clang-frontend case61 regression"
        )

    # Same header basename ("api.h") on both sides, in separate directories
    # (ADR-050 D1: scope_fingerprint is keyed on each dump's own
    # declared-header basename, not its absolute path — so two differently
    # *named* headers, as an earlier "old.h"/"new.h" version of this test
    # used, correctly ScopeMismatchError under the now-live comparability
    # gate; that's not what this test is about, so give both sides the same
    # conceptual header name, the way a real old/new library compare would).
    old_dir = tmp_path / "old"
    old_dir.mkdir()
    old_header = old_dir / "api.h"
    old_header.write_text("extern int lib_version;\nint get_version(void);\n")
    old_src = old_dir / "old.c"
    old_src.write_text(
        "int lib_version = 1;\nint get_version(void) { return lib_version; }\n"
    )
    v1_so = tmp_path / "libv1.so"
    subprocess.run(
        ["gcc", "-O2", "-shared", "-fPIC", "-o", str(v1_so), str(old_src)],
        check=True,
        capture_output=True,
    )

    new_dir = tmp_path / "new"
    new_dir.mkdir()
    new_header = new_dir / "api.h"
    new_header.write_text(
        "extern int lib_version;\nextern int lib_build_number;\nint get_version(void);\n"
    )
    new_src = new_dir / "new.c"
    new_src.write_text(
        "int lib_version = 1;\nint lib_build_number = 1042;\n"
        "int get_version(void) { return lib_version; }\n"
    )
    v2_so = tmp_path / "libv2.so"
    subprocess.run(
        ["gcc", "-O2", "-shared", "-fPIC", "-o", str(v2_so), str(new_src)],
        check=True,
        capture_output=True,
    )

    old_snap = dump(v1_so, [old_header], header_backend="clang")
    new_snap = dump(v2_so, [new_header], header_backend="clang")
    result = compare(old_snap, new_snap)

    # Documents the gap: a purely additive change still trips the address-
    # placement heuristic under clang, because no declared-alignment
    # corroboration is available to suppress it -- castxml (dumper_castxml.py,
    # TestCastxmlExtraction.test_variable_alignment) does not have this gap.
    assert result.verdict == Verdict.COMPATIBLE_WITH_RISK
    assert ChangeKind.EXPORTED_OBJECT_ALIGNMENT_REDUCED in {
        c.kind for c in result.changes
    }


def test_clang_backend_field_default_initializer_removed_end_to_end(
    tmp_path: Path,
) -> None:
    """G31 Phase C: TypeField.default (default member initializer) is now
    extracted for real on the clang L2 header backend
    (dumper_clang_expr._field_initializer_value). Verified end-to-end against a
    real compiled library through the actual dump()/compare() pipeline —
    not just at the parser-unit level (test_dumper_clang.py) or the
    hand-built-snapshot detector level (test_castxml_schema_completeness.py)
    — following the same discipline the G31 plan doc's Phase C section used
    for is_standard_layout/is_trivially_copyable.
    """
    if not (_have("clang") and _have("g++")):
        pytest.skip("clang and g++ are required for this end-to-end regression")

    old_dir = tmp_path / "old"
    old_dir.mkdir()
    old_header = old_dir / "api.h"
    old_header.write_text(
        "struct Cfg { int timeout = 30; };\nint use_cfg(const Cfg&);\n"
    )
    old_src = old_dir / "old.cpp"
    old_src.write_text(
        '#include "api.h"\nint use_cfg(const Cfg& c) { return c.timeout; }\n'
    )
    v1_so = tmp_path / "libv1.so"
    subprocess.run(
        ["g++", "-shared", "-fPIC", "-o", str(v1_so), str(old_src), f"-I{old_dir}"],
        check=True,
        capture_output=True,
    )

    new_dir = tmp_path / "new"
    new_dir.mkdir()
    new_header = new_dir / "api.h"
    new_header.write_text("struct Cfg { int timeout; };\nint use_cfg(const Cfg&);\n")
    new_src = new_dir / "new.cpp"
    new_src.write_text(
        '#include "api.h"\nint use_cfg(const Cfg& c) { return c.timeout; }\n'
    )
    v2_so = tmp_path / "libv2.so"
    subprocess.run(
        ["g++", "-shared", "-fPIC", "-o", str(v2_so), str(new_src), f"-I{new_dir}"],
        check=True,
        capture_output=True,
    )

    old_snap = dump(v1_so, [old_header], header_backend="clang")
    new_snap = dump(v2_so, [new_header], header_backend="clang")
    assert old_snap.clang_field_initializer_facts_reliable is True
    cfg = next(t for t in old_snap.types if t.name == "Cfg")
    assert next(f for f in cfg.fields if f.name == "timeout").default == "30"

    result = compare(old_snap, new_snap)
    assert ChangeKind.FIELD_DEFAULT_INITIALIZER_REMOVED in {
        c.kind for c in result.changes
    }


def test_clang_backend_reconstructs_vtable_and_flags_vptr_introduced(
    tmp_path: Path,
) -> None:
    """G31 Phase C: the clang backend used to hardcode ``vtable=[]``
    unconditionally, so ``VPTR_INTRODUCED``/``TYPE_VTABLE_CHANGED`` were
    silently inert for every clang-only comparison. Real end-to-end repro of
    the specific gap ``dumper_clang_vtable.py``'s docstring documents: a
    derived-class override that repeats neither `virtual` nor `override`
    (only `Widget::paint()` re-declared with the same signature as its base)
    carries no signal in clang's JSON AST distinguishing it from an
    unrelated new method -- must still be recognized as an override via
    signature matching, not silently dropped from the vtable.
    """
    if not (_have("clang") and _have("g++")):
        pytest.skip(
            "clang and g++ are required for the clang L2 backend integration test"
        )
    old_dir = tmp_path / "old"
    old_dir.mkdir()
    old_header = old_dir / "api.h"
    old_header.write_text("struct Widget {\n    int value_;\n};\n")
    old_src = old_dir / "old.cpp"
    old_src.write_text('#include "api.h"\n')
    v1_so = tmp_path / "libv1.so"
    subprocess.run(
        ["g++", "-shared", "-fPIC", "-o", str(v1_so), str(old_src), f"-I{old_dir}"],
        check=True,
        capture_output=True,
    )

    new_dir = tmp_path / "new"
    new_dir.mkdir()
    new_header = new_dir / "api.h"
    new_header.write_text(
        "struct Base { virtual void paint(); };\n"
        # Deliberately repeats NEITHER `virtual` NOR `override` -- pure
        # implicit-virtual-via-signature-match, the one case clang's own
        # JSON AST dump gives no direct signal for.
        "struct Widget : Base {\n    int value_;\n    void paint();\n};\n"
    )
    new_src = new_dir / "new.cpp"
    new_src.write_text(
        '#include "api.h"\nvoid Base::paint() {}\nvoid Widget::paint() {}\n'
    )
    v2_so = tmp_path / "libv2.so"
    subprocess.run(
        ["g++", "-shared", "-fPIC", "-o", str(v2_so), str(new_src), f"-I{new_dir}"],
        check=True,
        capture_output=True,
    )

    old_snap = dump(v1_so, [old_header], header_backend="clang")
    new_snap = dump(v2_so, [new_header], header_backend="clang")
    old_widget = next(t for t in old_snap.types if t.name == "Widget")
    new_widget = next(t for t in new_snap.types if t.name == "Widget")
    assert old_widget.vtable == []
    assert old_widget.vptr_offset_bits is None
    assert new_widget.vtable != []
    assert new_widget.vptr_offset_bits == 0

    result = compare(old_snap, new_snap)
    assert ChangeKind.VPTR_INTRODUCED in {c.kind for c in result.changes}


def test_clang_backend_resolves_concrete_template_specialization_base(
    tmp_path: Path,
) -> None:
    """Codex review, fresh evidence (P1): a class deriving from a CONCRETE
    template specialization (``struct D : A<int> {...};``) used to resolve
    to an empty vtable/no vptr regardless of what ``A<int>`` itself
    provides -- clang emits the usable definition as a
    ``ClassTemplateSpecializationDecl``, a different node kind from the
    ``CXXRecordDecl``/``RecordDecl`` pair the base-lookup index collected,
    so the base was silently unresolvable. Adding a no-keyword override in
    the new side then made the vtable appear to gain its FIRST entry -- a
    false breaking ``VPTR_INTRODUCED``, even though `D` was already
    genuinely polymorphic (via its inherited `A<int>::f`) in the OLD
    snapshot too.
    """
    if not (_have("clang") and _have("g++")):
        pytest.skip(
            "clang and g++ are required for the clang L2 backend integration test"
        )
    old_dir = tmp_path / "old"
    old_dir.mkdir()
    old_header = old_dir / "api.h"
    old_header.write_text(
        "template <typename T>\n"
        "struct A {\n"
        "    virtual void f(T x);\n"
        "};\n"
        "struct D : A<int> {\n"
        "};\n"
    )
    old_src = old_dir / "old.cpp"
    old_src.write_text('#include "api.h"\ntemplate struct A<int>;\n')
    v1_so = tmp_path / "libv1.so"
    subprocess.run(
        ["g++", "-shared", "-fPIC", "-o", str(v1_so), str(old_src), f"-I{old_dir}"],
        check=True,
        capture_output=True,
    )

    new_dir = tmp_path / "new"
    new_dir.mkdir()
    new_header = new_dir / "api.h"
    new_header.write_text(
        "template <typename T>\n"
        "struct A {\n"
        "    virtual void f(T x);\n"
        "};\n"
        # Deliberately repeats NEITHER `virtual` NOR `override` -- the same
        # implicit-virtual-via-signature-match shape the sibling test above
        # exercises for a non-template base, here through a concrete
        # specialization base instead.
        "struct D : A<int> {\n    void f(int x);\n};\n"
    )
    new_src = new_dir / "new.cpp"
    new_src.write_text('#include "api.h"\ntemplate struct A<int>;\n')
    v2_so = tmp_path / "libv2.so"
    subprocess.run(
        ["g++", "-shared", "-fPIC", "-o", str(v2_so), str(new_src), f"-I{new_dir}"],
        check=True,
        capture_output=True,
    )

    old_snap = dump(v1_so, [old_header], header_backend="clang")
    new_snap = dump(v2_so, [new_header], header_backend="clang")
    old_d = next(t for t in old_snap.types if t.name == "D")
    new_d = next(t for t in new_snap.types if t.name == "D")
    # The old side is ALREADY polymorphic via its inherited A<int>::f --
    # base resolution must find that slot even though A<int> is a concrete
    # template specialization, not an ordinary record.
    assert old_d.vtable != []
    assert old_d.vptr_offset_bits == 0
    assert new_d.vtable != []

    result = compare(old_snap, new_snap)
    assert ChangeKind.VPTR_INTRODUCED not in {c.kind for c in result.changes}
    assert ChangeKind.TYPE_VTABLE_CHANGED not in {c.kind for c in result.changes}


def test_clang_backend_narrowed_dynamic_exception_spec_is_not_a_new_slot(
    tmp_path: Path,
) -> None:
    """Codex review, fresh evidence: a C++14-and-earlier dynamic exception
    specification participated in the override signature key unstripped,
    so a base ``virtual void f() throw(int);`` overridden by a derived
    ``void f() throw() override;`` (a legal narrowing) compared as a
    different signature and appended a spurious second vtable slot instead
    of replacing the inherited one.
    """
    if not (_have("clang") and _have("g++")):
        pytest.skip(
            "clang and g++ are required for the clang L2 backend integration test"
        )
    old_dir = tmp_path / "old"
    old_dir.mkdir()
    old_header = old_dir / "api.h"
    old_header.write_text(
        "struct A {\n    virtual void f() throw(int);\n};\nstruct D : A {\n};\n"
    )
    old_src = old_dir / "old.cpp"
    old_src.write_text('#include "api.h"\nvoid A::f() throw(int) {}\n')
    v1_so = tmp_path / "libv1.so"
    subprocess.run(
        [
            "g++",
            "-std=c++14",
            "-shared",
            "-fPIC",
            "-o",
            str(v1_so),
            str(old_src),
            f"-I{old_dir}",
        ],
        check=True,
        capture_output=True,
    )

    new_dir = tmp_path / "new"
    new_dir.mkdir()
    new_header = new_dir / "api.h"
    new_header.write_text(
        "struct A {\n    virtual void f() throw(int);\n};\n"
        "struct D : A {\n    void f() throw() override;\n};\n"
    )
    new_src = new_dir / "new.cpp"
    new_src.write_text(
        '#include "api.h"\nvoid A::f() throw(int) {}\nvoid D::f() throw() {}\n'
    )
    v2_so = tmp_path / "libv2.so"
    subprocess.run(
        [
            "g++",
            "-std=c++14",
            "-shared",
            "-fPIC",
            "-o",
            str(v2_so),
            str(new_src),
            f"-I{new_dir}",
        ],
        check=True,
        capture_output=True,
    )

    old_snap = dump(
        v1_so, [old_header], header_backend="clang", gcc_options="-std=c++14"
    )
    new_snap = dump(
        v2_so, [new_header], header_backend="clang", gcc_options="-std=c++14"
    )
    old_d = next(t for t in old_snap.types if t.name == "D")
    new_d = next(t for t in new_snap.types if t.name == "D")
    # old_d has no override of its own -- its one slot is A::f. new_d's
    # OWN f() replaces that slot in place (still exactly one slot, not
    # two) -- the override must be recognized despite the narrowed
    # exception spec, not appended as a spurious second entry.
    assert old_d.vtable == ["_ZN1A1fEv"]
    assert new_d.vtable == ["_ZN1D1fEv"]

    result = compare(old_snap, new_snap)
    assert ChangeKind.TYPE_VTABLE_CHANGED not in {c.kind for c in result.changes}


def test_clang_backend_resolves_base_with_omitted_default_template_argument(
    tmp_path: Path,
) -> None:
    """Codex review, fresh evidence (P1): a specialization always carries a
    ``TemplateArgument`` for every parameter, including one a base
    reference omitted because it equals its own default -- joining all of
    them unconditionally produced a spelling the referring site's own
    (defaults-collapsed) spelling never matches, leaving the base
    unresolvable.
    """
    if not (_have("clang") and _have("g++")):
        pytest.skip(
            "clang and g++ are required for the clang L2 backend integration test"
        )
    old_dir = tmp_path / "old"
    old_dir.mkdir()
    old_header = old_dir / "api.h"
    old_header.write_text(
        "template <class T, class U = int>\n"
        "struct A {\n    virtual void f();\n};\n"
        "struct D : A<double> {\n};\n"
    )
    old_src = old_dir / "old.cpp"
    old_src.write_text('#include "api.h"\ntemplate struct A<double>;\n')
    v1_so = tmp_path / "libv1.so"
    subprocess.run(
        ["g++", "-shared", "-fPIC", "-o", str(v1_so), str(old_src), f"-I{old_dir}"],
        check=True,
        capture_output=True,
    )

    new_dir = tmp_path / "new"
    new_dir.mkdir()
    new_header = new_dir / "api.h"
    new_header.write_text(
        "template <class T, class U = int>\n"
        "struct A {\n    virtual void f();\n};\n"
        # No `virtual`/`override` keyword -- the flagship no-keyword-
        # override scenario, through a defaulted-argument specialization
        # base this time.
        "struct D : A<double> {\n    void f();\n};\n"
    )
    new_src = new_dir / "new.cpp"
    new_src.write_text('#include "api.h"\ntemplate struct A<double>;\n')
    v2_so = tmp_path / "libv2.so"
    subprocess.run(
        ["g++", "-shared", "-fPIC", "-o", str(v2_so), str(new_src), f"-I{new_dir}"],
        check=True,
        capture_output=True,
    )

    old_snap = dump(v1_so, [old_header], header_backend="clang")
    new_snap = dump(v2_so, [new_header], header_backend="clang")
    old_d = next(t for t in old_snap.types if t.name == "D")
    new_d = next(t for t in new_snap.types if t.name == "D")
    assert old_d.vtable != []
    assert old_d.vptr_offset_bits == 0
    assert new_d.vtable != []

    result = compare(old_snap, new_snap)
    assert ChangeKind.VPTR_INTRODUCED not in {c.kind for c in result.changes}
    assert ChangeKind.TYPE_VTABLE_CHANGED not in {c.kind for c in result.changes}


def test_clang_backend_bool_specialization_base_override_does_not_false_positive(
    tmp_path: Path,
) -> None:
    """Codex review, fresh evidence (P1): a `bool` non-type template
    argument's specialization is deliberately left unresolvable (its raw
    JSON `value` doesn't reliably print the way a base reference spells
    it), but an old snapshot alone already reads that as an empty vtable
    -- adding only an EXPLICIT `override` on the new side used to be
    fabricated as a brand-new slot rather than recognized as ambiguous,
    producing a real `VPTR_INTRODUCED`/`TYPE_VTABLE_CHANGED` false
    positive.
    """
    if not (_have("clang") and _have("g++")):
        pytest.skip(
            "clang and g++ are required for the clang L2 backend integration test"
        )
    old_dir = tmp_path / "old"
    old_dir.mkdir()
    old_header = old_dir / "api.h"
    old_header.write_text(
        "template <bool B>\nstruct A {\n    virtual void f();\n};\n"
        "struct D : A<true> {\n};\n"
    )
    old_src = old_dir / "old.cpp"
    old_src.write_text('#include "api.h"\ntemplate struct A<true>;\n')
    v1_so = tmp_path / "libv1.so"
    subprocess.run(
        ["g++", "-shared", "-fPIC", "-o", str(v1_so), str(old_src), f"-I{old_dir}"],
        check=True,
        capture_output=True,
    )

    new_dir = tmp_path / "new"
    new_dir.mkdir()
    new_header = new_dir / "api.h"
    new_header.write_text(
        "template <bool B>\nstruct A {\n    virtual void f();\n};\n"
        "struct D : A<true> {\n    void f() override;\n};\n"
    )
    new_src = new_dir / "new.cpp"
    new_src.write_text('#include "api.h"\ntemplate struct A<true>;\n')
    v2_so = tmp_path / "libv2.so"
    subprocess.run(
        ["g++", "-shared", "-fPIC", "-o", str(v2_so), str(new_src), f"-I{new_dir}"],
        check=True,
        capture_output=True,
    )

    old_snap = dump(v1_so, [old_header], header_backend="clang")
    new_snap = dump(v2_so, [new_header], header_backend="clang")
    old_d = next(t for t in old_snap.types if t.name == "D")
    new_d = next(t for t in new_snap.types if t.name == "D")
    assert old_d.vtable == []
    assert new_d.vtable == []  # ambiguous own override, suppressed on both sides

    result = compare(old_snap, new_snap)
    assert ChangeKind.VPTR_INTRODUCED not in {c.kind for c in result.changes}
    assert ChangeKind.TYPE_VTABLE_CHANGED not in {c.kind for c in result.changes}


def test_clang_backend_resolves_dependent_default_template_argument(
    tmp_path: Path,
) -> None:
    """Codex review, fresh evidence (P1): a default that depends on an
    earlier parameter (`template <class T, class U = T> struct A;`)
    reports the literal, unsubstituted default spelling (`"T"`), which
    never equals the real resolved argument by plain string comparison,
    leaving the base unresolvable.
    """
    if not (_have("clang") and _have("g++")):
        pytest.skip(
            "clang and g++ are required for the clang L2 backend integration test"
        )
    old_dir = tmp_path / "old"
    old_dir.mkdir()
    old_header = old_dir / "api.h"
    old_header.write_text(
        "template <class T, class U = T>\n"
        "struct A {\n    virtual void f();\n};\n"
        "struct D : A<double> {\n};\n"
    )
    old_src = old_dir / "old.cpp"
    old_src.write_text('#include "api.h"\ntemplate struct A<double>;\n')
    v1_so = tmp_path / "libv1.so"
    subprocess.run(
        ["g++", "-shared", "-fPIC", "-o", str(v1_so), str(old_src), f"-I{old_dir}"],
        check=True,
        capture_output=True,
    )

    new_dir = tmp_path / "new"
    new_dir.mkdir()
    new_header = new_dir / "api.h"
    new_header.write_text(
        "template <class T, class U = T>\n"
        "struct A {\n    virtual void f();\n};\n"
        "struct D : A<double> {\n    void f() override;\n};\n"
    )
    new_src = new_dir / "new.cpp"
    new_src.write_text('#include "api.h"\ntemplate struct A<double>;\n')
    v2_so = tmp_path / "libv2.so"
    subprocess.run(
        ["g++", "-shared", "-fPIC", "-o", str(v2_so), str(new_src), f"-I{new_dir}"],
        check=True,
        capture_output=True,
    )

    old_snap = dump(v1_so, [old_header], header_backend="clang")
    new_snap = dump(v2_so, [new_header], header_backend="clang")
    old_d = next(t for t in old_snap.types if t.name == "D")
    new_d = next(t for t in new_snap.types if t.name == "D")
    assert old_d.vtable != []
    assert old_d.vptr_offset_bits == 0
    assert new_d.vtable != []

    result = compare(old_snap, new_snap)
    assert ChangeKind.VPTR_INTRODUCED not in {c.kind for c in result.changes}
    assert ChangeKind.TYPE_VTABLE_CHANGED not in {c.kind for c in result.changes}


def test_clang_backend_does_not_widen_extern_c_function_virtuality(
    tmp_path: Path,
) -> None:
    """Codex review, fresh evidence (P2): an uninstantiated template method
    carries no `mangledName` at all, so `_collect_virtual_slots` falls back
    to its bare, unmangled `name` as the slot's "mangled" identity (e.g.
    `"f"`). A free `extern "C"` function sharing that same bare name mangles
    to the identical string by C-linkage design, so
    `_virtual_mangled_names()`'s plain membership test must not widen that
    unrelated free function's `is_virtual` to True purely from the name
    collision -- confirmed with a real clang dump of a template `f()` and an
    unrelated `extern "C" void f();` sharing the same TU.
    """
    if not (_have("clang") and _have("g++")):
        pytest.skip(
            "clang and g++ are required for the clang L2 backend integration test"
        )
    old_dir = tmp_path / "old"
    old_dir.mkdir()
    old_header = old_dir / "api.h"
    old_header.write_text(
        "template <class T>\nstruct A {\n    virtual void f();\n};\n"
        'extern "C" void f();\n'
    )
    old_src = old_dir / "old.cpp"
    old_src.write_text('#include "api.h"\nvoid f() {}\n')
    v1_so = tmp_path / "libv1.so"
    subprocess.run(
        ["g++", "-shared", "-fPIC", "-o", str(v1_so), str(old_src), f"-I{old_dir}"],
        check=True,
        capture_output=True,
    )

    old_snap = dump(v1_so, [old_header], header_backend="clang")
    # Both the template's own uninstantiated method and the unrelated free
    # extern "C" function share the identical bare name/fallback-mangled
    # identity ("f"/"f") -- there is nothing in the Function model to pick
    # one out from the other by name/mangled alone, so assert on the set:
    # exactly one of the two is genuinely virtual (the template method,
    # which carries clang's own `virtual` keyword directly, node.get
    # ("virtual") -- untouched by this fix). Before the fix, BOTH read as
    # virtual (the free function widened purely from the name collision).
    f_funcs = [fn for fn in old_snap.functions if fn.name == "f" and fn.mangled == "f"]
    assert len(f_funcs) == 2
    assert sum(fn.is_virtual for fn in f_funcs) == 1


def test_clang_backend_resolves_fully_defaulted_specialization_base(
    tmp_path: Path,
) -> None:
    """Codex review, fresh evidence (P1): when every template argument
    equals its own default, joining the (fully-popped) argument list left
    the specialization unindexed as if unresolvable -- but clang still
    prints an explicit, empty angle-bracket pair (`"A<>"`) on the base
    reference, never a bare `"A"`.
    """
    if not (_have("clang") and _have("g++")):
        pytest.skip(
            "clang and g++ are required for the clang L2 backend integration test"
        )
    old_dir = tmp_path / "old"
    old_dir.mkdir()
    old_header = old_dir / "api.h"
    old_header.write_text(
        "template <class T = int>\nstruct A {\n    virtual void f();\n};\n"
        "struct D : A<> {\n};\n"
    )
    old_src = old_dir / "old.cpp"
    old_src.write_text('#include "api.h"\ntemplate struct A<>;\n')
    v1_so = tmp_path / "libv1.so"
    subprocess.run(
        ["g++", "-shared", "-fPIC", "-o", str(v1_so), str(old_src), f"-I{old_dir}"],
        check=True,
        capture_output=True,
    )

    new_dir = tmp_path / "new"
    new_dir.mkdir()
    new_header = new_dir / "api.h"
    new_header.write_text(
        "template <class T = int>\nstruct A {\n    virtual void f();\n};\n"
        "struct D : A<> {\n    void f() override;\n};\n"
    )
    new_src = new_dir / "new.cpp"
    new_src.write_text('#include "api.h"\ntemplate struct A<>;\n')
    v2_so = tmp_path / "libv2.so"
    subprocess.run(
        ["g++", "-shared", "-fPIC", "-o", str(v2_so), str(new_src), f"-I{new_dir}"],
        check=True,
        capture_output=True,
    )

    old_snap = dump(v1_so, [old_header], header_backend="clang")
    new_snap = dump(v2_so, [new_header], header_backend="clang")
    old_d = next(t for t in old_snap.types if t.name == "D")
    new_d = next(t for t in new_snap.types if t.name == "D")
    assert old_d.vtable != []
    assert old_d.vptr_offset_bits == 0
    assert new_d.vtable != []

    result = compare(old_snap, new_snap)
    assert ChangeKind.VPTR_INTRODUCED not in {c.kind for c in result.changes}
    assert ChangeKind.TYPE_VTABLE_CHANGED not in {c.kind for c in result.changes}


def test_clang_backend_resolves_nested_specialization_base(tmp_path: Path) -> None:
    """Codex review, fresh evidence (P2): a base that is a NESTED template
    specialization (`Outer<int>::A<double>`) must index under the outer
    specialization's own SPELLED qualname, not its bare name -- otherwise
    the base resolves to the wrong (unrelated, or nonexistent) entry and
    an override on `D` goes completely undetected.
    """
    if not (_have("clang") and _have("g++")):
        pytest.skip(
            "clang and g++ are required for the clang L2 backend integration test"
        )
    def _header(extra: str) -> str:
        return (
            "template <class T>\nstruct Outer {\n"
            "    template <class U>\n"
            "    struct A {\n        virtual void f();\n    };\n};\n"
            "struct D : Outer<int>::A<double> {\n" + extra + "};\n"
        )

    old_dir = tmp_path / "old"
    old_dir.mkdir()
    old_header = old_dir / "api.h"
    old_header.write_text(_header(""))
    old_src = old_dir / "old.cpp"
    old_src.write_text(
        '#include "api.h"\ntemplate struct Outer<int>::A<double>;\n'
    )
    v1_so = tmp_path / "libv1.so"
    subprocess.run(
        ["g++", "-shared", "-fPIC", "-o", str(v1_so), str(old_src), f"-I{old_dir}"],
        check=True,
        capture_output=True,
    )

    new_dir = tmp_path / "new"
    new_dir.mkdir()
    new_header = new_dir / "api.h"
    new_header.write_text(_header("    void f() override;\n"))
    new_src = new_dir / "new.cpp"
    new_src.write_text(
        '#include "api.h"\ntemplate struct Outer<int>::A<double>;\n'
    )
    v2_so = tmp_path / "libv2.so"
    subprocess.run(
        ["g++", "-shared", "-fPIC", "-o", str(v2_so), str(new_src), f"-I{new_dir}"],
        check=True,
        capture_output=True,
    )

    old_snap = dump(v1_so, [old_header], header_backend="clang")
    new_snap = dump(v2_so, [new_header], header_backend="clang")
    old_d = next(t for t in old_snap.types if t.name == "D")
    new_d = next(t for t in new_snap.types if t.name == "D")
    assert old_d.vtable != []
    assert old_d.vptr_offset_bits == 0
    assert new_d.vtable != []

    result = compare(old_snap, new_snap)
    assert ChangeKind.VPTR_INTRODUCED not in {c.kind for c in result.changes}
    assert ChangeKind.TYPE_VTABLE_CHANGED not in {c.kind for c in result.changes}


def test_clang_backend_resolves_nested_specialization_with_defaulted_argument(
    tmp_path: Path,
) -> None:
    """Codex review, fresh evidence (P1, second round): a NESTED template's
    own defaulted argument (`Outer<int>::A<>`) needs its param_kinds/
    param_defaults/param_names looked up under the SAME unspelled scope
    the whole-AST index functions use for a nested template's own
    `ClassTemplateDecl` -- both in `build_specialization_index` (the base
    lookup) AND in `dumper_clang.py`'s own `_walk` (the override's OWN
    owner-qualification, for `vtable_slot_is_override_reuse`'s
    owner-descends-from check). Fixing only the former still left a
    residual false `TYPE_VTABLE_CHANGED`, since the override's own owner
    couldn't be matched against `RecordType.bases` either.
    """
    if not (_have("clang") and _have("g++")):
        pytest.skip(
            "clang and g++ are required for the clang L2 backend integration test"
        )

    def _header(extra: str) -> str:
        return (
            "template <class T>\nstruct Outer {\n"
            "    template <class U = int>\n"
            "    struct A {\n        virtual void f();\n    };\n};\n"
            "struct D : Outer<int>::A<> {\n" + extra + "};\n"
        )

    old_dir = tmp_path / "old"
    old_dir.mkdir()
    old_header = old_dir / "api.h"
    old_header.write_text(_header(""))
    old_src = old_dir / "old.cpp"
    old_src.write_text('#include "api.h"\ntemplate struct Outer<int>::A<>;\n')
    v1_so = tmp_path / "libv1.so"
    subprocess.run(
        ["g++", "-shared", "-fPIC", "-o", str(v1_so), str(old_src), f"-I{old_dir}"],
        check=True,
        capture_output=True,
    )

    new_dir = tmp_path / "new"
    new_dir.mkdir()
    new_header = new_dir / "api.h"
    new_header.write_text(_header("    void f() override;\n"))
    new_src = new_dir / "new.cpp"
    new_src.write_text('#include "api.h"\ntemplate struct Outer<int>::A<>;\n')
    v2_so = tmp_path / "libv2.so"
    subprocess.run(
        ["g++", "-shared", "-fPIC", "-o", str(v2_so), str(new_src), f"-I{new_dir}"],
        check=True,
        capture_output=True,
    )

    old_snap = dump(v1_so, [old_header], header_backend="clang")
    new_snap = dump(v2_so, [new_header], header_backend="clang")
    old_d = next(t for t in old_snap.types if t.name == "D")
    new_d = next(t for t in new_snap.types if t.name == "D")
    assert old_d.vtable != []
    assert old_d.vptr_offset_bits == 0
    assert new_d.vtable != []

    # An explicit `override` on a nested-specialization base must reuse
    # the same slot rather than reading as a brand-new one -- confirms
    # the owner-qualification path (dumper_clang.py's _walk), not just
    # the base-lookup path (build_specialization_index).
    result = compare(old_snap, new_snap)
    assert ChangeKind.VPTR_INTRODUCED not in {c.kind for c in result.changes}
    assert ChangeKind.TYPE_VTABLE_CHANGED not in {c.kind for c in result.changes}


def test_clang_backend_safely_degrades_on_conflicting_nested_template_defaults(
    tmp_path: Path,
) -> None:
    """Codex review, fresh evidence (P1, third round): two different
    explicit outer specializations each defining their OWN same-named
    nested member template with DIFFERING defaults (`Outer<int>`'s
    `template<class U=int> struct A` vs. `Outer<double>`'s `template
    <class U=double> struct A`) must not let one specialization's default
    silently leak into the other's. Before the fix this left the base
    unresolvable in a way that, combined with the pre-existing empty-
    vtable-on-both-sides shape, could mask a real virtual-method addition
    entirely (NO_CHANGE) -- the fix's job is to keep this a safe,
    unresolvable-base degradation (no crash, no wrong resolution), not to
    make it fully resolvable (which would need per-specialization keying,
    a larger change).
    """
    if not (_have("clang") and _have("g++")):
        pytest.skip(
            "clang and g++ are required for the clang L2 backend integration test"
        )

    def _header(extra: str) -> str:
        return (
            "template <class T>\nstruct Outer;\n\n"
            "template <>\nstruct Outer<int> {\n"
            "    template <class U = int>\n"
            "    struct A {\n        virtual void f();\n    };\n};\n\n"
            "template <>\nstruct Outer<double> {\n"
            "    template <class U = double>\n"
            "    struct A {\n        virtual void f();\n    };\n};\n\n"
            "struct D : Outer<double>::A<> {\n" + extra + "};\n"
        )

    old_dir = tmp_path / "old"
    old_dir.mkdir()
    old_header = old_dir / "api.h"
    old_header.write_text(_header(""))
    old_src = old_dir / "old.cpp"
    old_src.write_text('#include "api.h"\ntemplate struct Outer<double>::A<>;\n')
    v1_so = tmp_path / "libv1.so"
    subprocess.run(
        ["g++", "-shared", "-fPIC", "-o", str(v1_so), str(old_src), f"-I{old_dir}"],
        check=True,
        capture_output=True,
    )

    new_dir = tmp_path / "new"
    new_dir.mkdir()
    new_header = new_dir / "api.h"
    new_header.write_text(_header("    void f() override;\n"))
    new_src = new_dir / "new.cpp"
    new_src.write_text('#include "api.h"\ntemplate struct Outer<double>::A<>;\n')
    v2_so = tmp_path / "libv2.so"
    subprocess.run(
        ["g++", "-shared", "-fPIC", "-o", str(v2_so), str(new_src), f"-I{new_dir}"],
        check=True,
        capture_output=True,
    )

    old_snap = dump(v1_so, [old_header], header_backend="clang")
    new_snap = dump(v2_so, [new_header], header_backend="clang")
    old_d = next(t for t in old_snap.types if t.name == "D")
    new_d = next(t for t in new_snap.types if t.name == "D")
    # Safe degradation: unresolvable on BOTH sides (not a crash, not a
    # fabricated resolution using the wrong default) -- symmetric, so no
    # false VPTR_INTRODUCED/TYPE_VTABLE_CHANGED fires either.
    assert old_d.vtable == []
    assert new_d.vtable == []

    result = compare(old_snap, new_snap)
    assert ChangeKind.VPTR_INTRODUCED not in {c.kind for c in result.changes}
    assert ChangeKind.TYPE_VTABLE_CHANGED not in {c.kind for c in result.changes}


def test_clang_backend_resolves_dependent_default_across_legal_redeclaration(
    tmp_path: Path,
) -> None:
    """Codex review, fresh evidence (fourth round): an ordinary, LEGAL C++
    redeclaration of the same template with renamed parameters
    (`template<class T, class U=T> struct A;` followed by `template<class
    X, class Y> struct A {...};`) must not be treated as ambiguous just
    because the literal parameter names differ across declarations --
    that would break dependent-default substitution for this ordinary
    shape, previously masking a real virtual-method addition entirely.
    """
    if not (_have("clang") and _have("g++")):
        pytest.skip(
            "clang and g++ are required for the clang L2 backend integration test"
        )

    def _header(extra: str) -> str:
        return (
            "template <class T, class U = T>\nstruct A;\n\n"
            "template <class X, class Y>\nstruct A {\n"
            "    virtual void f();\n};\n\n"
            "struct D : A<double> {\n" + extra + "};\n"
        )

    old_dir = tmp_path / "old"
    old_dir.mkdir()
    old_header = old_dir / "api.h"
    old_header.write_text(_header(""))
    old_src = old_dir / "old.cpp"
    old_src.write_text('#include "api.h"\ntemplate struct A<double>;\n')
    v1_so = tmp_path / "libv1.so"
    subprocess.run(
        ["g++", "-shared", "-fPIC", "-o", str(v1_so), str(old_src), f"-I{old_dir}"],
        check=True,
        capture_output=True,
    )

    new_dir = tmp_path / "new"
    new_dir.mkdir()
    new_header = new_dir / "api.h"
    new_header.write_text(_header("    void f() override;\n"))
    new_src = new_dir / "new.cpp"
    new_src.write_text('#include "api.h"\ntemplate struct A<double>;\n')
    v2_so = tmp_path / "libv2.so"
    subprocess.run(
        ["g++", "-shared", "-fPIC", "-o", str(v2_so), str(new_src), f"-I{new_dir}"],
        check=True,
        capture_output=True,
    )

    old_snap = dump(v1_so, [old_header], header_backend="clang")
    new_snap = dump(v2_so, [new_header], header_backend="clang")
    old_d = next(t for t in old_snap.types if t.name == "D")
    new_d = next(t for t in new_snap.types if t.name == "D")
    assert old_d.vtable != []
    assert old_d.vptr_offset_bits == 0
    assert new_d.vtable != []

    result = compare(old_snap, new_snap)
    assert ChangeKind.VPTR_INTRODUCED not in {c.kind for c in result.changes}
    assert ChangeKind.TYPE_VTABLE_CHANGED not in {c.kind for c in result.changes}
