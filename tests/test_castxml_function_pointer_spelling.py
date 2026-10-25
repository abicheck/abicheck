"""castxml spells a function type as its full declarator (known gap 2026-10-15).

Bug class ``extraction.castxml_function_type_collapsed_to_opaque_tag``: an
unnamed castxml ``FunctionType`` used to spell ``FunctionType``, so every
callback compared equal to every other one. The oracles here are literal C
declarator strings and the signature tuples themselves, never the renderer.
"""

from __future__ import annotations

import itertools
from xml.etree.ElementTree import Element, SubElement

import pytest

from abicheck.checker import compare
from abicheck.dumper import _CastxmlParser
from abicheck.idioms import Idiom, recognise_idioms
from abicheck.model import AbiSnapshot, Function, Param, Visibility
from abicheck.model.change_catalog.kinds import ChangeKind
from abicheck.model.change_catalog.registry import Verdict
from abicheck.policy.generation_drift_demotion import (
    GENERATION_DRIFT_RULE,
    is_spelling_only_drift,
)
from abicheck.serialization import snapshot_from_dict, snapshot_to_dict
from abicheck.storage.generation_migrations import FACT_GENERATIONS_KEY
from abicheck.storage.versioning import EXTRACTOR_GENERATION
from abicheck.surface_graph import build_surface_graph

_FUND = {"void": "f_void", "int": "f_int", "long": "f_long", "double": "f_dbl"}


def _root(*extra: Element) -> Element:
    root = Element("GCC_XML")
    for name, id_ in _FUND.items():
        root.append(Element("FundamentalType", id=id_, name=name))
    for el in extra:
        root.append(el)
    return root


def _fn_type(
    id_: str,
    ret: str,
    args: tuple[str, ...],
    *,
    variadic: bool = False,
    attributes: str = "",
) -> Element:
    el = Element("FunctionType", id=id_, returns=_FUND[ret])
    if attributes:
        el.set("attributes", attributes)
    for a in args:
        SubElement(el, "Argument", type=_FUND[a])
    if variadic:
        SubElement(el, "Ellipsis")
    return el


def _spell(root: Element, id_: str) -> str:
    return _CastxmlParser(root, set(), set())._type_name(id_)


@pytest.mark.parametrize(
    ("wrappers", "expected"),
    [
        ((), "void (int, long)"),
        ((("PointerType", {}),), "void (*)(int, long)"),
        ((("PointerType", {}), ("PointerType", {})), "void (**)(int, long)"),
        ((("ReferenceType", {}),), "void (&)(int, long)"),
        (
            (("PointerType", {}), ("CvQualifiedType", {"const": "1"})),
            "void (*const)(int, long)",
        ),
        ((("PointerType", {}), ("ArrayType", {"max": "3"})), "void (*[4])(int, long)"),
    ],
    ids=["bare", "pointer", "pointer_to_pointer", "reference", "const_ptr", "array"],
)
def test_wrapped_function_type_spells_its_declarator(
    wrappers: tuple[tuple[str, dict[str, str]], ...], expected: str
) -> None:
    els = [_fn_type("fn", "void", ("int", "long"))]
    inner = "fn"
    for index, (tag, attrs) in enumerate(wrappers):
        id_ = f"w{index}"
        els.append(Element(tag, id=id_, type=inner, **attrs))
        inner = id_
    assert _spell(_root(*els), inner) == expected


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({"ret": "int", "args": ()}, "int (*)(void)"),
        ({"ret": "int", "args": ("int",), "variadic": True}, "int (*)(int, ...)"),
        (
            {"ret": "int", "args": ("int",), "attributes": "__stdcall__"},
            "int (*)(int) __attribute__((__stdcall__))",
        ),
    ],
    ids=["no_params", "variadic", "calling_convention"],
)
def test_signature_parts_are_spelled(kwargs: dict, expected: str) -> None:
    root = _root(_fn_type("fn", **kwargs), Element("PointerType", id="p", type="fn"))
    assert _spell(root, "p") == expected


def test_typedef_name_stops_the_walk() -> None:
    root = _root(
        _fn_type("fn", "int", ("int",)),
        Element("PointerType", id="p", type="fn"),
        Element("Typedef", id="td", name="cb_t", type="p"),
        Element("PointerType", id="pp", type="td"),
    )
    assert _spell(root, "td") == "cb_t"
    assert _spell(root, "pp") == "cb_t*"


_SIGNATURES = [
    (ret, args, variadic)
    for ret in ("void", "int")
    for n in range(3)
    for args in itertools.product(("int", "long"), repeat=n)
    for variadic in (False, True)
]


@pytest.mark.parametrize(
    ("a", "b"), list(itertools.combinations_with_replacement(_SIGNATURES, 2))
)
def test_spellings_are_equal_exactly_when_signatures_are(a: tuple, b: tuple) -> None:
    """Exhaustive small domain: return x arity x parameter type x variadic."""
    root = _root(
        _fn_type("fa", a[0], a[1], variadic=a[2]),
        _fn_type("fb", b[0], b[1], variadic=b[2]),
        Element("PointerType", id="pa", type="fa"),
        Element("PointerType", id="pb", type="fb"),
    )
    assert (_spell(root, "pa") == _spell(root, "pb")) is (a == b)


# ── baselines written before generation 16 ───────────────────────────────────


def _snapshot(param: str, typedefs: dict[str, str] | None = None) -> AbiSnapshot:
    return AbiSnapshot(
        library="libx.so.1",
        version="1",
        functions=[
            Function(
                name="set_cb",
                mangled="set_cb",
                return_type="void",
                params=[Param(name="cb", type=param)],
                visibility=Visibility.PUBLIC,
            )
        ],
        typedefs=dict(typedefs or {}),
    )


def _stamped(snapshot: AbiSnapshot, extractor: int) -> AbiSnapshot:
    doc = snapshot_to_dict(snapshot)
    doc[FACT_GENERATIONS_KEY] = {"extractor": extractor, "resolver": 2, "source": 0}
    return snapshot_from_dict(doc)


@pytest.mark.parametrize(
    ("old", "new", "expected"),
    [
        ("FunctionType*", "void (*)(int)", True),
        ("FunctionType**", "void (**)(int, int)", True),
        ("const FunctionType*", "int (*)(void)", True),
        ("FunctionType*", "FunctionType*", False),
        ("FunctionType*", "int*", False),  # not a function declarator
        ("FunctionType* const", "void (*const)(int)", True),
        ("FunctionType*[4]", "void (*[5])(int)", True),  # extent respelled
        ("FunctionType*", "void (&)(int)", False),  # pointer became reference
        ("FunctionType*", "void (**)(int)", False),  # pointer depth changed
        ("FunctionType**", "void (*)(int)", False),
        ("FunctionType*", "void (*const)(int)", False),  # cv added
        ("FunctionType*[4]", "void (*)(int)", False),  # array dropped
    ],
)
def test_opaque_tag_against_declarator_is_drift(
    old: str, new: str, expected: bool
) -> None:
    assert is_spelling_only_drift(old, new, ()) is expected


def test_opaque_tag_is_not_drift_when_the_old_side_declares_it() -> None:
    assert (
        is_spelling_only_drift("FunctionType*", "void (*)(int)", ("FunctionType",))
        is False
    )


@pytest.mark.parametrize("old_generation", [0, EXTRACTOR_GENERATION - 1])
def test_pre_fix_baseline_against_fresh_declarator_is_not_gating(
    old_generation: int,
) -> None:
    old = _stamped(_snapshot("FunctionType*"), old_generation)
    result = compare(old, _snapshot("void (*)(int)"))
    hits = [c for c in result.changes if c.kind is ChangeKind.FUNC_PARAMS_CHANGED]
    assert [h.modulation_rule for h in hits] == [GENERATION_DRIFT_RULE]
    assert result.verdict is Verdict.COMPATIBLE_WITH_RISK


def test_same_generation_callback_change_is_breaking() -> None:
    old = _stamped(_snapshot("void (*)(int)"), EXTRACTOR_GENERATION)
    result = compare(old, _snapshot("void (*)(int, void*)"))
    kinds = {c.kind for c in result.changes}
    assert ChangeKind.FUNC_PARAMS_CHANGED in kinds
    assert result.verdict is Verdict.BREAKING


def test_identical_callback_is_no_change() -> None:
    result = compare(_snapshot("void (*)(int)"), _snapshot("void (*)(int)"))
    assert result.changes == []
    assert result.verdict is Verdict.NO_CHANGE


@pytest.mark.parametrize("target", ["FunctionType*", "int (*)(int)", "void (*)(void)"])
def test_function_pointer_typedef_is_not_an_opaque_handle(target: str) -> None:
    tags = recognise_idioms(build_surface_graph(_snapshot("cb_t", {"cb_t": target})))
    assert all(t.idiom != Idiom.HANDLE for t in tags.get("cb_t", []))
