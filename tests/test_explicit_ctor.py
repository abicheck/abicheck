"""Tests for CTOR_EXPLICIT_ADDED / CTOR_EXPLICIT_REMOVED.

Synthetic snapshots — no compiler needed. Exercises the `is_explicit` flag
captured from DW_AT_explicit and the diff logic in diff_symbols.py.
"""

from xml.etree.ElementTree import Element

from abicheck.checker import compare
from abicheck.checker_policy import API_BREAK_KINDS, RISK_KINDS, ChangeKind, Verdict
from abicheck.dumper import _CastxmlParser
from abicheck.model import (
    AbiSnapshot,
    AccessLevel,
    Function,
    Param,
    RecordType,
    Visibility,
)


def _snap(version: str, functions: list[Function]) -> AbiSnapshot:
    return AbiSnapshot(
        library="libtest.so.1",
        version=version,
        functions=functions,
        variables=[],
        types=[],
    )


def _ctor(mangled: str, is_explicit: bool | None) -> Function:
    return Function(
        name="Foo::Foo",
        mangled=mangled,
        return_type="void",
        params=[Param(name="x", type="int")],
        visibility=Visibility.PUBLIC,
        is_explicit=is_explicit,
    )


class TestExplicitCtor:
    def test_implicit_to_explicit_is_api_break(self) -> None:
        old = _snap("1.0", [_ctor("_ZN3FooC1Ei", is_explicit=False)])
        new = _snap("2.0", [_ctor("_ZN3FooC1Ei", is_explicit=True)])
        r = compare(old, new)
        assert r.verdict == Verdict.API_BREAK
        assert any(c.kind == ChangeKind.CTOR_EXPLICIT_ADDED for c in r.changes)
        assert ChangeKind.CTOR_EXPLICIT_ADDED in API_BREAK_KINDS

    def test_explicit_to_implicit_is_risk(self) -> None:
        old = _snap("1.0", [_ctor("_ZN3FooC1Ei", is_explicit=True)])
        new = _snap("2.0", [_ctor("_ZN3FooC1Ei", is_explicit=False)])
        r = compare(old, new)
        assert r.verdict == Verdict.COMPATIBLE_WITH_RISK
        assert any(c.kind == ChangeKind.CTOR_EXPLICIT_REMOVED for c in r.changes)
        assert ChangeKind.CTOR_EXPLICIT_REMOVED in RISK_KINDS

    def test_no_change_when_explicit_matches(self) -> None:
        old = _snap("1.0", [_ctor("_ZN3FooC1Ei", is_explicit=True)])
        new = _snap("2.0", [_ctor("_ZN3FooC1Ei", is_explicit=True)])
        r = compare(old, new)
        assert not any(
            c.kind in (ChangeKind.CTOR_EXPLICIT_ADDED, ChangeKind.CTOR_EXPLICIT_REMOVED)
            for c in r.changes
        )

    def test_mangled_name_unchanged(self) -> None:
        """The explicit specifier never changes the mangled name; both
        directions must rely on `is_explicit` rather than symbol churn."""
        old = _ctor("_ZN3FooC1Ei", is_explicit=False)
        new = _ctor("_ZN3FooC1Ei", is_explicit=True)
        assert old.mangled == new.mangled

    def test_none_on_either_side_suppresses_detector(self) -> None:
        """Tri-state: a missing `is_explicit` field (older snapshot, or a
        Function tag where the attribute is N/A) must NOT produce a finding
        when compared against a fresh snapshot. Defaulting unknown→implicit
        would cause spurious CTOR_EXPLICIT_ADDED findings on every consumer
        upgrading abicheck.
        """
        # old has unknown explicitness; new is explicit
        old = _snap("1.0", [_ctor("_ZN3FooC1Ei", is_explicit=None)])
        new = _snap("2.0", [_ctor("_ZN3FooC1Ei", is_explicit=True)])
        r = compare(old, new)
        assert not any(
            c.kind in (ChangeKind.CTOR_EXPLICIT_ADDED, ChangeKind.CTOR_EXPLICIT_REMOVED)
            for c in r.changes
        )
        # Symmetric: old explicit, new unknown
        old = _snap("1.0", [_ctor("_ZN3FooC1Ei", is_explicit=True)])
        new = _snap("2.0", [_ctor("_ZN3FooC1Ei", is_explicit=None)])
        r = compare(old, new)
        assert not any(
            c.kind in (ChangeKind.CTOR_EXPLICIT_ADDED, ChangeKind.CTOR_EXPLICIT_REMOVED)
            for c in r.changes
        )

    def test_stale_snapshot_no_field_loads_as_none(self) -> None:
        """Loader contract: an older snapshot JSON without the
        `is_explicit` key must load as None, not False, so the diff
        does not produce stale-baseline false positives."""
        from abicheck.serialization import snapshot_from_dict

        d = {
            "library": "libtest.so.1",
            "version": "1.0",
            "functions": [
                {
                    "name": "Foo::Foo",
                    "mangled": "_ZN3FooC1Ei",
                    "return_type": "void",
                    "params": [{"name": "x", "type": "int"}],
                    "visibility": "public",
                    # NB: no is_explicit key — simulates a pre-v5 snapshot.
                },
            ],
            "variables": [],
            "types": [],
        }
        snap = snapshot_from_dict(d)
        assert snap.functions[0].is_explicit is None

    def test_castxml_converter_fallback_reads_multiline_explicit_operator(self, tmp_path) -> None:
        source = tmp_path / "v2.h"
        source.write_text(
            "struct Token {\n"
            "    explicit\n"
            "    operator int() const;\n"
            "};\n",
            encoding="utf-8",
        )
        root = Element("GCC_XML")
        root.append(Element("File", id="_1", name=str(source)))
        root.append(Element("FundamentalType", id="_2", name="int"))
        parser = _CastxmlParser(root, exported_dynamic=set(), exported_static=set())
        loc_el = Element("Location", file="_1", line="3")

        assert parser._source_line_has_explicit(loc_el) is True

        declaration_el = Element("Converter", file="_1", line="3")
        assert parser._source_line_has_explicit(None, declaration_el) is True
        assert str(source) in parser._source_lines_cache

    def test_castxml_converter_parse_functions_reads_operator_line_fallback(self, tmp_path) -> None:
        source = tmp_path / "v2.h"
        source.write_text(
            "struct Token {\n"
            "    explicit\n"
            "    operator int() const;\n"
            "};\n",
            encoding="utf-8",
        )
        root = Element("GCC_XML")
        root.append(Element("File", id="_1", name=str(source)))
        root.append(Element("FundamentalType", id="_2", name="int"))
        root.append(
            Element(
                "Converter",
                id="_3",
                file="_1",
                line="3",
                returns="_2",
                mangled="_ZNK5TokencviEv",
            )
        )
        parser = _CastxmlParser(root, exported_dynamic={"_ZNK5TokencviEv"}, exported_static=set())

        funcs = parser.parse_functions()

        assert len(funcs) == 1
        assert funcs[0].name == "operator int"
        assert funcs[0].is_explicit is True

    def test_castxml_converter_fallback_preserves_unknown_on_missing_source(self) -> None:
        root = Element("GCC_XML")
        root.append(Element("File", id="_1", name=""))
        root.append(Element("File", id="_2", name="/does/not/exist.h"))
        parser = _CastxmlParser(root, exported_dynamic=set(), exported_static=set())

        assert parser._source_line_has_explicit(None) is None
        assert parser._source_line_has_explicit(Element("Location", file="_missing", line="1")) is None
        assert parser._source_line_has_explicit(Element("Location", file="_1", line="1")) is None
        assert parser._source_line_has_explicit(Element("Location", file="_2", line="not-int")) is None
        assert parser._source_line_has_explicit(Element("Location", file="_2", line="1")) is None


def _snap_with_types(
    version: str, functions: list[Function], types: list[RecordType]
) -> AbiSnapshot:
    return AbiSnapshot(
        library="libtest.so.1",
        version=version,
        functions=functions,
        variables=[],
        types=types,
    )


def _conv_ctor(
    cls: str,
    mangled: str,
    param_type: str,
    is_explicit: bool | None = False,
    default: str | None = None,
    access: AccessLevel = AccessLevel.PUBLIC,
) -> Function:
    # A castxml Constructor's demangled `name` is the bare class name, not
    # `Class::Class` — C++ forbids any other member from sharing that name,
    # which is exactly what the detector relies on (diff_symbols
    # ._converting_ctors_by_class).
    return Function(
        name=cls,
        mangled=mangled,
        return_type="void",
        params=[Param(name="x", type=param_type, default=default)],
        visibility=Visibility.PUBLIC,
        access=access,
        is_explicit=is_explicit,
    )


class TestCtorOverloadAmbiguityRisk:
    """Tests for CTOR_OVERLOAD_AMBIGUITY_RISK (case111 heuristic)."""

    def test_second_converting_ctor_added_is_risk(self) -> None:
        cls = RecordType(name="Widget", kind="class")
        old = _snap_with_types(
            "1.0", [_conv_ctor("Widget", "c1", "int")], [cls]
        )
        new = _snap_with_types(
            "2.0",
            [
                _conv_ctor("Widget", "c1", "int"),
                _conv_ctor("Widget", "c2", "int_factory_t"),
            ],
            [cls],
        )
        r = compare(old, new)
        assert any(
            c.kind == ChangeKind.CTOR_OVERLOAD_AMBIGUITY_RISK for c in r.changes
        )
        assert ChangeKind.CTOR_OVERLOAD_AMBIGUITY_RISK in RISK_KINDS
        assert r.verdict == Verdict.COMPATIBLE_WITH_RISK

    def test_defaulted_first_parameter_still_counts_as_converting(self) -> None:
        """`Widget(int x = 0)` is single-argument-callable (`Widget w = 5;`)
        exactly like `Widget(int x)` — a defaulted first parameter must not
        exempt it from the ambiguity-risk heuristic (Codex review #556)."""
        cls = RecordType(name="Widget", kind="class")
        old = _snap_with_types(
            "1.0", [_conv_ctor("Widget", "c1", "int", default="0")], [cls]
        )
        new = _snap_with_types(
            "2.0",
            [
                _conv_ctor("Widget", "c1", "int", default="0"),
                _conv_ctor("Widget", "c2", "double", default="0.0"),
            ],
            [cls],
        )
        r = compare(old, new)
        assert any(
            c.kind == ChangeKind.CTOR_OVERLOAD_AMBIGUITY_RISK for c in r.changes
        )

    def test_first_converting_ctor_is_not_flagged(self) -> None:
        """0 -> 1 converting constructor cannot be ambiguous by itself."""
        cls = RecordType(name="Widget", kind="class")
        old = _snap_with_types("1.0", [], [cls])
        new = _snap_with_types(
            "2.0", [_conv_ctor("Widget", "c1", "int")], [cls]
        )
        r = compare(old, new)
        assert not any(
            c.kind == ChangeKind.CTOR_OVERLOAD_AMBIGUITY_RISK for c in r.changes
        )

    def test_new_explicit_ctor_is_not_flagged(self) -> None:
        """An explicit constructor never participates in implicit conversion."""
        cls = RecordType(name="Widget", kind="class")
        old = _snap_with_types(
            "1.0", [_conv_ctor("Widget", "c1", "int")], [cls]
        )
        new = _snap_with_types(
            "2.0",
            [
                _conv_ctor("Widget", "c1", "int"),
                _conv_ctor("Widget", "c2", "double", is_explicit=True),
            ],
            [cls],
        )
        r = compare(old, new)
        assert not any(
            c.kind == ChangeKind.CTOR_OVERLOAD_AMBIGUITY_RISK for c in r.changes
        )

    def test_new_copy_ctor_is_not_flagged(self) -> None:
        """The copy constructor is infrastructure, not a converting overload."""
        cls = RecordType(name="Widget", kind="class")
        old = _snap_with_types(
            "1.0", [_conv_ctor("Widget", "c1", "int")], [cls]
        )
        new = _snap_with_types(
            "2.0",
            [
                _conv_ctor("Widget", "c1", "int"),
                _conv_ctor("Widget", "c2", "const Widget &"),
            ],
            [cls],
        )
        r = compare(old, new)
        assert not any(
            c.kind == ChangeKind.CTOR_OVERLOAD_AMBIGUITY_RISK for c in r.changes
        )

    def test_new_private_ctor_is_not_flagged(self) -> None:
        """A private constructor isn't callable at an ordinary consumer call
        site (Codex review #556), so it cannot create the implicit-conversion
        collision this heuristic looks for."""
        cls = RecordType(name="Widget", kind="class")
        old = _snap_with_types(
            "1.0", [_conv_ctor("Widget", "c1", "int")], [cls]
        )
        new = _snap_with_types(
            "2.0",
            [
                _conv_ctor("Widget", "c1", "int"),
                _conv_ctor(
                    "Widget", "c2", "double", access=AccessLevel.PRIVATE
                ),
            ],
            [cls],
        )
        r = compare(old, new)
        assert not any(
            c.kind == ChangeKind.CTOR_OVERLOAD_AMBIGUITY_RISK for c in r.changes
        )

    def test_new_protected_ctor_is_not_flagged(self) -> None:
        """Same reasoning as private — only derived classes can call it."""
        cls = RecordType(name="Widget", kind="class")
        old = _snap_with_types(
            "1.0", [_conv_ctor("Widget", "c1", "int")], [cls]
        )
        new = _snap_with_types(
            "2.0",
            [
                _conv_ctor("Widget", "c1", "int"),
                _conv_ctor(
                    "Widget", "c2", "double", access=AccessLevel.PROTECTED
                ),
            ],
            [cls],
        )
        r = compare(old, new)
        assert not any(
            c.kind == ChangeKind.CTOR_OVERLOAD_AMBIGUITY_RISK for c in r.changes
        )

    def test_new_volatile_copy_ctor_is_not_flagged(self) -> None:
        """A volatile-qualified copy ctor is still copy-ctor infrastructure
        (Codex review #556) — `volatile` must be stripped alongside `const`/`&`
        before the self-type check, or it's mistaken for a converting ctor."""
        cls = RecordType(name="Widget", kind="class")
        old = _snap_with_types(
            "1.0", [_conv_ctor("Widget", "c1", "int")], [cls]
        )
        new = _snap_with_types(
            "2.0",
            [
                _conv_ctor("Widget", "c1", "int"),
                _conv_ctor("Widget", "c2", "volatile Widget &"),
            ],
            [cls],
        )
        r = compare(old, new)
        assert not any(
            c.kind == ChangeKind.CTOR_OVERLOAD_AMBIGUITY_RISK for c in r.changes
        )

    def test_unknown_explicitness_is_not_flagged(self) -> None:
        """Tri-state: unknown is_explicit on the new ctor must not fire."""
        cls = RecordType(name="Widget", kind="class")
        old = _snap_with_types(
            "1.0", [_conv_ctor("Widget", "c1", "int")], [cls]
        )
        new = _snap_with_types(
            "2.0",
            [
                _conv_ctor("Widget", "c1", "int"),
                _conv_ctor("Widget", "c2", "double", is_explicit=None),
            ],
            [cls],
        )
        r = compare(old, new)
        assert not any(
            c.kind == ChangeKind.CTOR_OVERLOAD_AMBIGUITY_RISK for c in r.changes
        )

    def test_already_ambiguous_class_not_reflagged_without_growth(self) -> None:
        """A class already at 2+ converting ctors that stays at the same count
        (one removed, a different one added) is not re-flagged — only a net
        increase across the 1->2+ threshold is reported."""
        cls = RecordType(name="Widget", kind="class")
        old = _snap_with_types(
            "1.0",
            [
                _conv_ctor("Widget", "c1", "int"),
                _conv_ctor("Widget", "c2", "double"),
            ],
            [cls],
        )
        new = _snap_with_types(
            "2.0",
            [
                _conv_ctor("Widget", "c1", "int"),
                _conv_ctor("Widget", "c3", "float"),
            ],
            [cls],
        )
        r = compare(old, new)
        assert not any(
            c.kind == ChangeKind.CTOR_OVERLOAD_AMBIGUITY_RISK for c in r.changes
        )

    def test_brand_new_class_not_flagged(self) -> None:
        """A class that doesn't exist on the old side is a fresh API decision,
        not a regression — even if it starts with 2 converting ctors."""
        new_cls = RecordType(name="Widget", kind="class")
        old = _snap_with_types("1.0", [], [])
        new = _snap_with_types(
            "2.0",
            [
                _conv_ctor("Widget", "c1", "int"),
                _conv_ctor("Widget", "c2", "double"),
            ],
            [new_cls],
        )
        r = compare(old, new)
        assert not any(
            c.kind == ChangeKind.CTOR_OVERLOAD_AMBIGUITY_RISK for c in r.changes
        )

    def test_deleted_ctor_not_counted(self) -> None:
        """A `= delete`d overload can never be called, so it can't create
        ambiguity."""
        cls = RecordType(name="Widget", kind="class")
        old = _snap_with_types(
            "1.0", [_conv_ctor("Widget", "c1", "int")], [cls]
        )
        deleted = _conv_ctor("Widget", "c2", "double")
        deleted.is_deleted = True
        new = _snap_with_types(
            "2.0", [_conv_ctor("Widget", "c1", "int"), deleted], [cls]
        )
        r = compare(old, new)
        assert not any(
            c.kind == ChangeKind.CTOR_OVERLOAD_AMBIGUITY_RISK for c in r.changes
        )
