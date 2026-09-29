"""Header-text evidence for exports (``buildsource/export_declaration_evidence``)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from abicheck.buildsource.export_declaration_evidence import (
    ExportDeclarationEvidence,
    build_export_declaration_evidence,
    instantiated_over_owned_types,
    public_template_for_export,
    textual_declaration_hint,
)
from abicheck.model.vocabulary import ScopeOrigin


def _d(name, header=None, mangled=None, origin=ScopeOrigin.PUBLIC_HEADER):
    return SimpleNamespace(
        name=name, source_header=header, mangled=mangled, origin=origin
    )


def _snapshot(tmp_path: Path, **kw) -> SimpleNamespace:
    inc = tmp_path / "include"
    inc.mkdir()
    (inc / "api.h").write_text(
        "#ifndef API_H\n#define API_H\n"
        "namespace fmt { template <typename T> class Box { void get(); };\n"
        "template <class T> T make_it(int);\n"
        "#ifdef FMT_EXTRA\nint extra_fn(int);\n#else\nint other_fn();\n#endif\n"
        "int plain_fn(int);\n}\n#endif\n"
    )
    (inc / "detail").mkdir()
    (inc / "detail" / "hidden.hpp").write_text(
        "namespace fmt { int hidden_fn(int); }\n"
    )
    (inc / "notes.txt").write_text("hidden_fn(\n")  # not a header suffix
    snap = SimpleNamespace(
        functions=[
            _d("fmt::plain_fn", str(inc / "api.h"), "_ZN3fmt8plain_fnEi"),
            _d("std::sort", None, "_ZNSt4sortEv", ScopeOrigin.SYSTEM_HEADER),
            _d("fmt::gone", str(tmp_path / "missing.h")),
        ],
        variables=[],
        types=[_d("acme::Thing", str(inc / "api.h"))],
        enums=[],
        excluded_header_patterns=kw.get("patterns", ("detail/*",)),
        excluded_header_matching=kw.get("matching", "glob"),
    )
    return snap


def test_build_collects_owned_namespaces_templates_and_headers(tmp_path: Path) -> None:
    ev = build_export_declaration_evidence(_snapshot(tmp_path))
    assert {"fmt", "acme"} <= ev.owned_namespaces
    assert "std" not in ev.owned_namespaces  # system header + runtime namespace
    assert {"Box", "make_it"} <= ev.textual_templates
    names = {Path(h.path).name: h for h in ev.headers}
    assert set(names) == {"api.h", "hidden.hpp"}
    assert names["hidden.hpp"].excluded and not names["api.h"].excluded


def test_exact_matching_mode(tmp_path: Path) -> None:
    ev = build_export_declaration_evidence(
        _snapshot(tmp_path, patterns=("detail/hidden.hpp",), matching="exact")
    )
    excluded = {Path(h.path).name for h in ev.headers if h.excluded}
    assert excluded == {"hidden.hpp"}


def test_textual_hints_excluded_conditional_and_real_absence(tmp_path: Path) -> None:
    ev = build_export_declaration_evidence(_snapshot(tmp_path))
    hint = textual_declaration_hint("_ZN3fmt9hidden_fnEi", ev)
    assert hint is not None and hint.reason == "excluded_header"
    assert hint.header.endswith("hidden.hpp")
    cond = textual_declaration_hint("_ZN3fmt8extra_fnEi", ev)
    assert cond is not None and cond.reason == "conditional"
    assert cond.guard == "#ifdef FMT_EXTRA"
    other = textual_declaration_hint("_ZN3fmt8other_fnEv", ev)
    assert other is not None and other.guard.startswith("#else")
    # unconditional in an included header: not a hint (real evidence of absence)
    assert textual_declaration_hint("_ZN3fmt8plain_fnEi", ev) is None
    # C symbol, wrong scope, unparseable
    assert textual_declaration_hint("hidden_fn", ev) is not None
    assert textual_declaration_hint("_ZN3zzz9hidden_fnEi", ev) is None
    assert textual_declaration_hint("@@bad", ev) is None
    assert textual_declaration_hint("x", ExportDeclarationEvidence()) is None


def test_public_template_for_export_from_header_text() -> None:
    ev = ExportDeclarationEvidence(
        owned_namespaces=frozenset({"fmt"}), textual_templates=frozenset({"Box"})
    )
    assert public_template_for_export("_ZN3fmt3BoxIiE3getEv", ev) == "fmt::Box"
    assert public_template_for_export("_ZN3fmt3BoxIiEC2Ev", ev) == "fmt::Box"
    assert public_template_for_export("_ZN3fmt3BagIiE3getEv", ev) is None
    assert public_template_for_export("_ZN3oth3BoxIiE3getEv", ev) is None
    assert public_template_for_export("_ZN3fmt3Box3getEv", ev) is None


def test_instantiated_over_owned_types() -> None:
    owned = frozenset({"dnnl"})
    sym = "_ZNSt19_Sp_counted_deleterIPN4dnnl6streamELi3ESaIiEE10_M_disposeEv"
    assert instantiated_over_owned_types(sym, owned)
    assert not instantiated_over_owned_types(sym, frozenset({"other"}))
    assert not instantiated_over_owned_types(sym, frozenset())
    assert not instantiated_over_owned_types("plain_c", owned)
    assert not instantiated_over_owned_types("_ZNSt6vectorIiE5clearEv", owned)


@pytest.mark.parametrize("sep", ["/", "\\"])
@pytest.mark.parametrize("pattern_sep", ["/", "\\"])
def test_exact_exclusion_is_separator_independent(sep: str, pattern_sep: str) -> None:
    """Exact matching must not depend on which separator the platform or the
    pattern uses: every combination of POSIX/Windows spelling of the header
    path and of the pattern agrees with the separator-free oracle."""
    from abicheck.buildsource.export_declaration_evidence import exact_exclusion_matches

    parts = ["C:" if sep == "\\" else "", "inc", "detail", "hidden.hpp"]
    path = sep.join(parts)
    cases = {
        ("detail", "hidden.hpp"): True,
        ("hidden.hpp",): True,
        ("inc", "detail", "hidden.hpp"): True,
        ("etail", "hidden.hpp"): False,  # a suffix of a component is not a match
        ("detail", "hidden.h"): False,
        ("other", "hidden.hpp"): False,
    }
    for comps, expected in cases.items():
        pattern = pattern_sep.join(comps)
        assert exact_exclusion_matches(path, (pattern,)) is expected, (path, pattern)
    assert exact_exclusion_matches(path, ()) is False
