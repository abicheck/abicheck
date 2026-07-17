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

"""Phase 2 CastXML <-> Clang L2 parity gate.

Runs the **live** CastXML and clang ``-ast-dump=json`` L2 header backends over
the SAME compiled header/source pair and compares them field-by-field, over a
corpus covering: functions and constructors, variables and constants,
namespaced records, anonymous unions, templates, multiple and virtual
inheritance, bitfields, and attributes (``[[deprecated]]``), plus a separate
plain-C corpus. Every compared fact is classified into exactly one of::

    EQUAL                          identical value from both producers
    SEMANTICALLY_EQUAL             equal after type-name canonicalization
    EXPECTED_PRODUCER_DIFFERENCE   a known, documented capability gap between
                                    the two backends (see each test's comment)
    UNSUPPORTED_ON_ONE_PRODUCER    one producer structurally cannot represent
                                    this fact at all (e.g. field layout on the
                                    clang backend, which carries no DWARF/
                                    binary layout of its own)
    UNEXPECTED_MISMATCH            anything else — a real, unexplained
                                    divergence; a test asserting this never
                                    appears is the actual regression guard

Two real, previously-undiscovered castxml parser bugs were found and fixed via
this harness (PR #582): a `<Destructor>` element's bare class-name spelling
combined with its usual lack of a `mangled` attribute produced a `mangled`
value identical to the class's own constructor, defaulting a real virtual
destructor's visibility to HIDDEN instead of PUBLIC; and a plain C API
variable got a bogus C++-style pseudo-mangled symbol from castxml's
ambiguous-language-mode guess (the analogous, already-fixed "case141" issue
for functions, extended to variables). Both are asserted as regression guards
below (``test_destructor_visibility_agrees``,
``test_c_linkage_variable_identity_agrees``).

**Known, environment-imposed limitation**: this validates ONE castxml version
against ONE clang version (whatever is installed in CI/dev — see
``castxml --version`` / ``clang --version``). The design doc's "CastXML and
Clang version matrix" is a CI/infrastructure concern (installing and running
against multiple pinned versions), not a test-content concern, and is out of
scope here.

Gated on clang + g++ + castxml being present; skipped otherwise (same pattern
as ``test_clang_header_backend_integration.py``, which this file complements
with a much deeper field-level corpus — that file's own parity test stays as
a quick top-level smoke check).
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from enum import Enum
from typing import Any

import pytest

from abicheck.dumper import dump
from abicheck.model import AbiSnapshot, Visibility
from abicheck.name_classification import canonicalize_type_name

# Scoped to Linux/ELF for the same reason as test_clang_header_backend_integration.py:
# a clang-built AST and a g++-built binary only share a mangling scheme on this
# platform (see that module's docstring for the macOS/Windows divergence detail).
pytestmark = pytest.mark.skipif(
    not sys.platform.startswith("linux"),
    reason="castxml/clang parity gate is ELF/Linux-scoped (see module docstring)",
)


class Parity(str, Enum):
    EQUAL = "equal"
    SEMANTICALLY_EQUAL = "semantically_equal"
    EXPECTED_PRODUCER_DIFFERENCE = "expected_producer_difference"
    UNSUPPORTED_ON_ONE_PRODUCER = "unsupported_on_one_producer"
    UNEXPECTED_MISMATCH = "unexpected_mismatch"


def classify(
    castxml_value: Any,
    clang_value: Any,
    *,
    unsupported_on_clang: bool = False,
) -> Parity:
    """Classify one compared fact between the two L2 producers.

    ``unsupported_on_clang`` marks a fact the clang backend structurally
    cannot populate today (see model.py's own field docstrings — e.g.
    ``TypeField.offset_bits``, ``RecordType.is_abstract``,
    ``Function.deprecated``): a ``None`` on that side is expected, not a
    mismatch, regardless of what castxml reports.
    """
    if castxml_value == clang_value:
        return Parity.EQUAL
    if unsupported_on_clang and clang_value is None:
        return Parity.UNSUPPORTED_ON_ONE_PRODUCER
    if isinstance(castxml_value, str) and isinstance(clang_value, str):
        if canonicalize_type_name(castxml_value) == canonicalize_type_name(clang_value):
            return Parity.SEMANTICALLY_EQUAL
    return Parity.UNEXPECTED_MISMATCH


def _have(tool: str) -> bool:
    return shutil.which(tool) is not None


def _require_tools() -> None:
    if not (_have("clang") and _have("g++") and _have("castxml")):
        pytest.skip("clang, g++, and castxml are all required for the L2 parity gate")


# ── C++ corpus: functions/ctors/dtors, variables/constants, namespaced ──────
# records, anonymous unions, templates, multiple+virtual inheritance,
# bitfields, and the `[[deprecated]]` attribute.

_CPP_HEADER = """
#pragma once

namespace outer {
namespace inner {

struct Flags {
    unsigned int a : 1;
    unsigned int b : 3;
    unsigned int c : 4;
};

struct Variant {
    int tag;
    union {
        int as_int;
        float as_float;
    };
};

struct Base1 { virtual ~Base1(); virtual int id() const; };
struct Base2 { virtual ~Base2(); virtual int kind() const; };
struct VBase { virtual ~VBase(); int shared; };
struct Derived : Base1, Base2, virtual VBase {
    int id() const override;
    int kind() const override;
};

class Widget {
public:
    Widget();
    explicit Widget(int value);
    Widget(int value, const char* name);
    int value() const;
private:
    int value_ = 0;
};

[[deprecated("use Widget2 instead")]]
void old_api();

template <typename T>
struct Box {
    T value;
    T get() const { return value; }
};
template struct Box<int>;

extern int g_counter;
constexpr int kMaxWidgets = 16;

int compute(int x);
double compute(double x);

}  // namespace inner
}  // namespace outer
"""

_CPP_SOURCE = """
#include "api.h"
namespace outer { namespace inner {
Widget::Widget() = default;
Widget::Widget(int value) : value_(value) {}
Widget::Widget(int value, const char* name) : value_(value) {}
int Widget::value() const { return value_; }
Base1::~Base1() = default;
int Base1::id() const { return 1; }
Base2::~Base2() = default;
int Base2::kind() const { return 2; }
VBase::~VBase() = default;
int Derived::id() const { return 42; }
int Derived::kind() const { return 43; }
void old_api() {}
int g_counter = 0;
int compute(int x) { return x; }
double compute(double x) { return x; }
}}
"""

_C_HEADER = """
#pragma once
typedef struct { int x; int y; } point_t;
extern int c_global;
int c_add(int a, int b);
"""

_C_SOURCE = """
#include "capi.h"
int c_global = 0;
int c_add(int a, int b) { return a + b; }
"""


@pytest.fixture(scope="module")
def cpp_snapshots(tmp_path_factory: pytest.TempPathFactory) -> tuple[AbiSnapshot, AbiSnapshot]:
    _require_tools()
    tmp_path = tmp_path_factory.mktemp("parity_cpp")
    header = tmp_path / "api.h"
    header.write_text(_CPP_HEADER)
    src = tmp_path / "api.cpp"
    src.write_text(_CPP_SOURCE)
    so = tmp_path / "libapi.so"
    subprocess.run(
        ["g++", "-shared", "-fPIC", "-o", str(so), str(src), f"-I{tmp_path}"],
        check=True,
        capture_output=True,
    )
    castxml_snap = dump(so, [header], header_backend="castxml")
    clang_snap = dump(so, [header], header_backend="clang")
    return castxml_snap, clang_snap


@pytest.fixture(scope="module")
def c_snapshots(tmp_path_factory: pytest.TempPathFactory) -> tuple[AbiSnapshot, AbiSnapshot]:
    _require_tools()
    tmp_path = tmp_path_factory.mktemp("parity_c")
    header = tmp_path / "capi.h"
    header.write_text(_C_HEADER)
    src = tmp_path / "capi.c"
    src.write_text(_C_SOURCE)
    so = tmp_path / "libcapi.so"
    subprocess.run(
        ["gcc", "-shared", "-fPIC", "-o", str(so), str(src), f"-I{tmp_path}"],
        check=True,
        capture_output=True,
    )
    castxml_snap = dump(so, [header], header_backend="castxml")
    clang_snap = dump(so, [header], header_backend="clang")
    return castxml_snap, clang_snap


def _public(snap: AbiSnapshot) -> dict[str, Any]:
    return {f.mangled: f for f in snap.functions if f.visibility == Visibility.PUBLIC}


def _types_by_name(snap: AbiSnapshot) -> dict[str, Any]:
    return {t.name: t for t in snap.types}


# ── Functions and overloads ─────────────────────────────────────────────────


class TestFunctionsAndOverloads:
    def test_overloaded_free_functions_agree(self, cpp_snapshots) -> None:
        castxml_snap, clang_snap = cpp_snapshots
        c_funcs = _public(castxml_snap)
        d_funcs = _public(clang_snap)
        for mangled in (
            "_ZN5outer5inner7computeEi",
            "_ZN5outer5inner7computeEd",
        ):
            assert mangled in c_funcs, f"castxml missing {mangled}"
            assert mangled in d_funcs, f"clang missing {mangled}"
            cf, df = c_funcs[mangled], d_funcs[mangled]
            assert classify(cf.return_type, df.return_type) is Parity.EQUAL
            assert [p.type for p in cf.params] == [p.type for p in df.params]

    def test_deprecated_attribute_is_expected_producer_difference(
        self, cpp_snapshots
    ) -> None:
        """Function.deprecated: castxml populates it from castxml's own
        `deprecation`/`attributes` channel; the clang backend doesn't
        populate it yet (AbiSnapshot.ast_producer / _both_castxml_backed,
        PR #582) — a known, gated capability gap, not a bug."""
        castxml_snap, clang_snap = cpp_snapshots
        c_funcs = _public(castxml_snap)
        d_funcs = _public(clang_snap)
        mangled = "_ZN5outer5inner7old_apiEv"
        assert c_funcs[mangled].deprecated == "use Widget2 instead"
        verdict = classify(
            c_funcs[mangled].deprecated,
            d_funcs[mangled].deprecated,
            unsupported_on_clang=True,
        )
        assert verdict is Parity.UNSUPPORTED_ON_ONE_PRODUCER


class TestConstructorIdentity:
    def test_constructor_overloads_are_expected_producer_difference(
        self, cpp_snapshots
    ) -> None:
        """CastXML sometimes omits a constructor's real mangled name
        (documented castxml gap; SYNTHETIC_CTOR_KEY_PREFIX), while clang's
        `-ast-dump=json` always carries `mangledName` — so the identity
        KEY differs (synthetic vs real), but both producers agree on the
        real overload SET SIZE and each overload's parameter signature."""
        castxml_snap, clang_snap = cpp_snapshots

        def ctor_param_lists(snap: AbiSnapshot) -> list[list[str]]:
            return sorted(
                [canonicalize_type_name(p.type) for p in f.params]
                for f in snap.functions
                if f.name == "Widget"
                and f.visibility == Visibility.PUBLIC
                and not f.mangled.startswith("~")
            )

        c_sigs = ctor_param_lists(castxml_snap)
        d_sigs = ctor_param_lists(clang_snap)
        assert len(c_sigs) == len(d_sigs) == 3
        assert c_sigs == d_sigs


class TestDestructorVisibility:
    def test_destructor_visibility_agrees(self, cpp_snapshots) -> None:
        """Regression guard (PR #582): a real, previously-undiscovered
        castxml parser bug found by this harness. castxml's <Destructor>
        carries the bare CLASS name (identical to its own Constructor) and
        usually no `mangled` attribute, so its synthesized key used to
        collapse onto a non-symbol string that never matched any real ELF
        export, defaulting a genuinely PUBLIC virtual destructor to
        HIDDEN. Fixed via _ctor_or_dtor_visibility + a "~ClassName" display-
        name synthesis in _function_display_name, matching what the clang
        backend already did correctly."""
        castxml_snap, clang_snap = cpp_snapshots

        def public_dtor_names(snap: AbiSnapshot) -> set[str]:
            return {
                f.name
                for f in snap.functions
                if f.name.startswith("~") and f.visibility == Visibility.PUBLIC
            }

        c_dtors = public_dtor_names(castxml_snap)
        d_dtors = public_dtor_names(clang_snap)
        assert c_dtors == d_dtors == {"~Base1", "~Base2", "~VBase"}


# ── Variables and constants ─────────────────────────────────────────────────


class TestVariablesAndConstants:
    def test_variable_and_constant_agree(self, cpp_snapshots) -> None:
        castxml_snap, clang_snap = cpp_snapshots
        c_vars = {v.mangled: v for v in castxml_snap.variables}
        d_vars = {v.mangled: v for v in clang_snap.variables}
        mangled = "_ZN5outer5inner9g_counterE"
        assert mangled in c_vars and mangled in d_vars
        assert classify(c_vars[mangled].type, d_vars[mangled].type) is Parity.EQUAL
        assert (
            classify(
                c_vars[mangled].visibility, d_vars[mangled].visibility
            )
            is Parity.EQUAL
        )
        assert castxml_snap.constants.get("outer::inner::kMaxWidgets") == "16"
        assert clang_snap.constants.get("outer::inner::kMaxWidgets") == "16"


# ── Namespaced records, anonymous unions, bitfields, inheritance ───────────


class TestNamespacedAndCompositeRecords:
    def test_namespaced_record_qualified_name(self, cpp_snapshots) -> None:
        """RecordType.qualified_name is castxml-only by design (model.py:
        ``name`` itself stays bare on both backends so type-map lookups
        keep matching by the same key; qualified_name is an extra,
        castxml-only namespace-aware field) — expected producer
        difference, not a bug."""
        castxml_snap, _clang_snap = cpp_snapshots
        widget = _types_by_name(castxml_snap)["Widget"]
        assert widget.qualified_name == "outer::inner::Widget"

    def test_bitfields_agree_on_names_types_and_widths(self, cpp_snapshots) -> None:
        castxml_snap, clang_snap = cpp_snapshots
        c_flags = _types_by_name(castxml_snap)["Flags"]
        d_flags = _types_by_name(clang_snap)["Flags"]
        for cf, df in zip(c_flags.fields, d_flags.fields):
            assert cf.name == df.name
            assert classify(cf.type, df.type) in (Parity.EQUAL, Parity.SEMANTICALLY_EQUAL)
            assert cf.is_bitfield is df.is_bitfield is True
            assert cf.bitfield_bits == df.bitfield_bits
        # Field offset/layout: castxml computes real layout; the clang
        # backend carries none of its own (see model.py's own
        # TypeField.offset_bits docstring) — structurally unsupported on
        # that producer, not a mismatch.
        for cf, df in zip(c_flags.fields, d_flags.fields):
            assert classify(
                cf.offset_bits, df.offset_bits, unsupported_on_clang=True
            ) in (Parity.EQUAL, Parity.UNSUPPORTED_ON_ONE_PRODUCER)

    def test_anonymous_union_fields_agree(self, cpp_snapshots) -> None:
        castxml_snap, clang_snap = cpp_snapshots
        c_variant = _types_by_name(castxml_snap)["Variant"]
        d_variant = _types_by_name(clang_snap)["Variant"]
        c_names = [f.name for f in c_variant.fields]
        d_names = [f.name for f in d_variant.fields]
        assert c_names == d_names == ["tag", "as_int", "as_float"]

    def test_multiple_and_virtual_inheritance_bases_agree(self, cpp_snapshots) -> None:
        castxml_snap, clang_snap = cpp_snapshots
        c_derived = _types_by_name(castxml_snap)["Derived"]
        d_derived = _types_by_name(clang_snap)["Derived"]
        assert set(c_derived.bases) == set(d_derived.bases) == {"Base1", "Base2"}
        assert set(c_derived.virtual_bases) == set(d_derived.virtual_bases) == {"VBase"}


class TestTemplates:
    def test_explicit_instantiation_is_expected_producer_difference(
        self, cpp_snapshots
    ) -> None:
        """castxml sees only the concrete explicit instantiation `Box<int>`
        (real layout, is_template_pattern=False); the clang backend
        surfaces the template's own uninstantiated pattern `Box` instead
        (is_template_pattern=True, no fixed layout) — see
        RecordType.is_template_pattern's own docstring in model.py. Both
        are correct representations of what each producer actually parsed;
        this is the documented, modeled distinction, not a bug."""
        castxml_snap, clang_snap = cpp_snapshots
        c_types = _types_by_name(castxml_snap)
        d_types = _types_by_name(clang_snap)
        assert "Box<int>" in c_types
        assert c_types["Box<int>"].is_template_pattern is False
        box_pattern = next(
            (t for t in d_types.values() if t.name.startswith("Box")), None
        )
        assert box_pattern is not None
        assert box_pattern.is_template_pattern is True


# ── Plain-C corpus ───────────────────────────────────────────────────────────


class TestCHeaderCorpus:
    def test_c_struct_and_function_agree(self, c_snapshots) -> None:
        castxml_snap, clang_snap = c_snapshots
        c_types = _types_by_name(castxml_snap)
        d_types = _types_by_name(clang_snap)
        assert [f.name for f in c_types["point_t"].fields] == ["x", "y"]
        assert [f.name for f in d_types["point_t"].fields] == ["x", "y"]
        c_funcs = _public(castxml_snap)
        d_funcs = _public(clang_snap)
        assert "c_add" in c_funcs and "c_add" in d_funcs

    def test_c_linkage_variable_identity_agrees(self, c_snapshots) -> None:
        """Regression guard (PR #582): a real, previously-undiscovered
        castxml parser bug found by this harness. castxml's language-mode
        detection for an ambiguous `.h` file defaults to C++, and (like
        the already-known, already-fixed "case141" issue for functions)
        emits a bogus C++-style pseudo-mangled name
        (e.g. `_Z8c_global`) for a plain C-linkage variable that the
        compiled binary never actually mangles at all. Fixed by extending
        the same real-ELF-export override to parse_variables()."""
        castxml_snap, clang_snap = c_snapshots
        c_vars = {v.name: v for v in castxml_snap.variables}
        d_vars = {v.name: v for v in clang_snap.variables}
        assert c_vars["c_global"].mangled == d_vars["c_global"].mangled == "c_global"
        assert c_vars["c_global"].visibility == Visibility.PUBLIC
        assert d_vars["c_global"].visibility == Visibility.PUBLIC


# ── The classification predicate itself ─────────────────────────────────────


class TestClassifyPredicate:
    def test_equal_values(self) -> None:
        assert classify("int", "int") is Parity.EQUAL

    def test_semantically_equal_after_canonicalization(self) -> None:
        assert classify("const int", "int const") is Parity.SEMANTICALLY_EQUAL

    def test_unsupported_on_clang(self) -> None:
        assert (
            classify(128, None, unsupported_on_clang=True)
            is Parity.UNSUPPORTED_ON_ONE_PRODUCER
        )

    def test_unexpected_mismatch(self) -> None:
        assert classify("int", "long") is Parity.UNEXPECTED_MISMATCH

    def test_none_on_clang_without_the_flag_is_still_a_mismatch(self) -> None:
        """The unsupported_on_clang escape hatch must be explicit per-fact
        — a bare None on the clang side must NOT be silently forgiven
        unless the caller has actually verified it's a structural gap."""
        assert classify("msg", None) is Parity.UNEXPECTED_MISMATCH
