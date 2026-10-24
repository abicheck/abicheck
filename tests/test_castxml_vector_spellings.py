"""castxml vector types joined from the clang AST (typedef, field, param, return).

Oracles are hand-computed: ``float x8`` is 8 * 4 = 32 bytes, ``char x32`` is
32 bytes, ``float x4`` is 16 bytes. The spelling is the one the DWARF path records
(``T __attribute__((vector_size(N)))``), so both backends agree on one type.
"""

from __future__ import annotations

from xml.etree.ElementTree import Element

import pytest

from abicheck.extract.headers.castxml.vector_join import (
    VECTOR_SIDECAR_BACKEND,
    _valid_sidecar,
    join_vector_spellings,
)
from abicheck.extract.headers.castxml.vector_spellings import (
    VECTOR_SPELLINGS_ATTR,
    castxml_vector_spellings,
    clang_vector_spellings,
    has_vector_nodes,
    parse_vector_qualtype,
    resolve_vector_spellings,
    with_vector_spellings,
)

_F8 = "float __attribute__((vector_size(32)))"
_C32 = "char __attribute__((vector_size(32)))"


# ── clang qualType parsing (unit) ────────────────────────────────────────────


@pytest.mark.parametrize(
    ("qual_type", "spelled"),
    [
        ("__attribute__((__vector_size__(8 * sizeof(float)))) float", _F8),
        ("__attribute__((__vector_size__(32 * sizeof(char)))) char", _C32),
        ("__attribute__((__vector_size__(32))) float", _F8),
        ("__attribute__((ext_vector_type(8))) float", _F8),
    ],
    ids=["lanes_float", "lanes_char", "literal_bytes", "ext_vector_type"],
)
def test_clang_vector_qualtype_is_spelled_with_total_bytes(
    qual_type: str, spelled: str
) -> None:
    assert parse_vector_qualtype(qual_type) == spelled


@pytest.mark.parametrize(
    "qual_type",
    [
        "float[8]",
        "int",
        "__attribute__((__vector_size__(8 * sizeof(__float128)))) __float128",
    ],
    ids=["plain_array", "fundamental", "unknown_element"],
)
def test_non_vector_or_unknown_element_has_no_spelling(qual_type: str) -> None:
    assert parse_vector_qualtype(qual_type) is None


# ── clang declaration keys (unit) ────────────────────────────────────────────


def _clang_tree() -> dict:
    """A clang AST: a typedef, an inline field, an inline parameter, and a typedef field."""
    vec = "__attribute__((__vector_size__(8 * sizeof(float)))) float"
    return {
        "kind": "TranslationUnitDecl",
        "inner": [
            {"kind": "TypedefDecl", "name": "f8", "type": {"qualType": vec}},
            {
                "kind": "RecordDecl",
                "name": "Pack",
                "inner": [
                    {"kind": "FieldDecl", "name": "v", "type": {"qualType": vec}},
                    {"kind": "FieldDecl", "name": "t", "type": {"qualType": "f8"}},
                ],
            },
            {
                "kind": "FunctionDecl",
                "name": "take",
                "inner": [
                    {
                        "kind": "ParmVarDecl",
                        "name": "x",
                        "type": {"qualType": vec},
                    },
                ],
            },
        ],
    }


def test_clang_keys_cover_typedef_inline_field_and_param() -> None:
    keys = clang_vector_spellings(_clang_tree())
    assert keys["typedef:f8"] == _F8
    assert keys["field:Pack::v"] == _F8
    assert keys["param:take#0"] == _F8


def test_typedef_named_field_is_not_keyed_separately() -> None:
    # ``f8 t;`` is spelled through the typedef name, so it needs no field key.
    keys = clang_vector_spellings(_clang_tree())
    assert "field:Pack::t" not in keys


# ── castxml vector nodes and the join (unit) ─────────────────────────────────


def _castxml_root() -> Element:
    """castxml XML for the typedef, inline field and inline parameter declarations."""
    root = Element("GCC_XML")
    root.append(Element("Namespace", id="_1", name="::"))
    root.append(Element("Unimplemented", id="_19", type_class="Vector"))
    root.append(Element("Typedef", id="_7", name="f8", type="_19", context="_1"))
    root.append(Element("Struct", id="_10", name="Pack", context="_1"))
    root.append(Element("Field", id="_23", name="v", type="_19", context="_10"))
    root.append(Element("Unimplemented", id="_26", type_class="Vector"))
    fn = Element("Function", id="_13", name="take", context="_1", returns="_30")
    # castxml lists a function's parameters as child <Argument> elements.
    fn.append(Element("Argument", id="_40", name="x", type="_26"))
    root.append(fn)
    return root


def _castxml_with_param() -> Element:
    root = Element("GCC_XML")
    root.append(Element("Namespace", id="_1", name="::"))
    root.append(Element("Unimplemented", id="_26", type_class="Vector"))
    fn = Element("Function", id="_13", name="take", context="_1", returns="_99")
    fn.append(Element("Argument", id="_40", name="x", type="_26"))
    root.append(fn)
    return root


def test_castxml_vector_nodes_are_keyed_by_their_declaration() -> None:
    keys = castxml_vector_spellings(_castxml_with_param())
    assert keys == {"_26": "param:take#0"}


def test_typedef_and_shared_inline_field_resolve_to_one_node() -> None:
    root = _castxml_root()
    keys = castxml_vector_spellings(root)
    # The typedef claims node _19; the inline field shares it, so it is one key.
    assert keys["_19"] == "typedef:f8"


def test_resolve_maps_nodes_and_leaves_the_parsed_tree_unwritten() -> None:
    root = _castxml_root()
    before = dict(root.attrib)
    resolved = resolve_vector_spellings(root, _clang_tree())
    assert resolved["_19"] == _F8
    assert dict(root.attrib) == before
    assert VECTOR_SPELLINGS_ATTR not in root.attrib


def test_carried_root_shares_children_and_holds_the_mapping() -> None:
    root = _castxml_root()
    carried = with_vector_spellings(root, {"_19": _F8})
    assert carried is not root
    assert list(carried) == list(root)
    assert has_vector_nodes(carried)
    assert VECTOR_SPELLINGS_ATTR in carried.attrib


def test_vector_node_without_a_clang_declaration_stays_unresolved() -> None:
    root = _castxml_with_param()
    clang = {"kind": "TranslationUnitDecl", "inner": []}
    resolved = resolve_vector_spellings(root, clang)
    assert "_26" not in resolved


# ── sidecar validity (unit) ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    "payload",
    [
        None,
        "not a dict",
        {},
        {"spellings": "not a dict"},
        {"spellings": {"_1": 7}},
        {"spellings": {1: "float"}},
    ],
    ids=["none", "str", "empty", "non_dict_spellings", "non_str_value", "non_str_key"],
)
def test_malformed_sidecar_is_not_a_hit(payload: object) -> None:
    assert _valid_sidecar(payload) is None


def test_well_formed_sidecar_is_a_hit() -> None:
    assert _valid_sidecar({"spellings": {"_19": _F8}}) == {"_19": _F8}


# ── sidecar hit / miss / corrupt through the join (unit, hermetic) ───────────


class _FakeStore:
    def __init__(self, payload: object = None) -> None:
        self.payload = payload
        self.published: list[object] = []

    def lookup(self, key: str, memo: object = None) -> object:
        return self.payload

    def publish(
        self, key: str, payload: object, deps: object, memo: object = None
    ) -> None:
        self.published.append(payload)
        self.payload = payload

    def evict(self, key: str) -> None:
        self.payload = None


def _join(monkeypatch: pytest.MonkeyPatch, store: _FakeStore, calls: list[int]):
    import abicheck.extract.headers.castxml.vector_join as vj

    monkeypatch.setattr(vj, "ast_cache_store", lambda key, backend: store)

    def fake_clang(*args: object, **kwargs: object) -> tuple[dict, None, bool]:
        calls.append(1)
        return _clang_tree(), None, False

    monkeypatch.setattr(vj, "clang_header_dump", fake_clang)
    root = _castxml_root()
    return join_vector_spellings(
        root,
        "key-1",
        headers=[],
        extra_includes=[],
        compiler="c++",
        gcc_path=None,
        gcc_prefix=None,
        gcc_options=None,
        gcc_option_tokens=(),
        sysroot=None,
        nostdinc=False,
        lang=None,
        extra_hash_dirs=(),
        exported_symbols=frozenset(),
    )


def test_sidecar_hit_skips_clang(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []
    store = _FakeStore({"spellings": {"_19": _F8}})
    root = _join(monkeypatch, store, calls)
    assert calls == []
    assert root.get(VECTOR_SPELLINGS_ATTR) is not None


def test_missing_sidecar_runs_clang_and_publishes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[int] = []
    store = _FakeStore(None)
    _join(monkeypatch, store, calls)
    assert calls == [1]
    assert store.published == [{"spellings": {"_19": _F8, "_26": _F8}}]


def test_corrupt_sidecar_is_a_miss_that_reruns_clang(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[int] = []
    store = _FakeStore({"spellings": {"_19": 7}})
    _join(monkeypatch, store, calls)
    assert calls == [1]
    assert store.published == [{"spellings": {"_19": _F8, "_26": _F8}}]


def test_sidecar_namespace_is_distinct_from_the_xml_entry() -> None:
    assert VECTOR_SIDECAR_BACKEND != "castxml"


# ── inline vector returns, overloads, clang launch failure (unit) ─────────────


def test_inline_vector_return_is_keyed_and_spelled() -> None:
    # The return qualType carries its own ``__attribute__((...))`` parentheses.
    vec = "__attribute__((__vector_size__(8 * sizeof(float)))) float"
    clang = {
        "kind": "TranslationUnitDecl",
        "inner": [
            {
                "kind": "FunctionDecl",
                "name": "mk",
                "type": {"qualType": f"{vec} (float)"},
            },
        ],
    }
    assert clang_vector_spellings(clang)["return:mk"] == _F8


def test_overloads_with_inline_vector_params_get_distinct_keys() -> None:
    def overload(mangled: str, elem: str) -> dict:
        vec = f"__attribute__((__vector_size__(4 * sizeof({elem})))) {elem}"
        return {
            "kind": "FunctionDecl",
            "name": "ov",
            "mangledName": mangled,
            "inner": [{"kind": "ParmVarDecl", "name": "p", "type": {"qualType": vec}}],
        }

    clang = {
        "kind": "TranslationUnitDecl",
        "inner": [overload("_Z2ovDv4_f", "float"), overload("_Z2ovDv4_i", "int")],
    }
    keys = clang_vector_spellings(clang)
    assert keys["param:_Z2ovDv4_f#0"] == "float __attribute__((vector_size(16)))"
    assert keys["param:_Z2ovDv4_i#0"] == "int __attribute__((vector_size(16)))"


def test_castxml_overloads_key_by_mangled_name() -> None:
    root = Element("GCC_XML")
    root.append(Element("Namespace", id="_1", name="::"))
    for fn_id, mangled, arg_id in (
        ("_13", "_Z2ovDv4_f", "_16"),
        ("_14", "_Z2ovDv4_i", "_17"),
    ):
        root.append(Element("Unimplemented", id=arg_id, type_class="Vector"))
        fn = Element("Function", id=fn_id, name="ov", mangled=mangled, context="_1")
        fn.append(Element("Argument", id=f"{arg_id}a", name="p", type=arg_id))
        root.append(fn)
    keys = castxml_vector_spellings(root)
    assert keys == {"_16": "param:_Z2ovDv4_f#0", "_17": "param:_Z2ovDv4_i#0"}


def test_clang_launch_failure_leaves_vectors_unresolved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import abicheck.extract.headers.castxml.vector_join as vj

    class _Store:
        published: list[object] = []

        def lookup(self, key: str, memo: object = None) -> object:
            return None

        def publish(
            self, key: str, payload: object, deps: object, memo: object = None
        ) -> None:
            self.published.append(payload)

    store = _Store()
    monkeypatch.setattr(vj, "ast_cache_store", lambda key, backend: store)

    def launch_fails(*args: object, **kwargs: object) -> None:
        raise OSError("clang vanished")

    monkeypatch.setattr(vj, "clang_header_dump", launch_fails)
    root = _castxml_root()
    returned = vj.join_vector_spellings(
        root,
        "key-os",
        headers=[],
        extra_includes=[],
        compiler="c++",
        gcc_path=None,
        gcc_prefix=None,
        gcc_options=None,
        gcc_option_tokens=(),
        sysroot=None,
        nostdinc=False,
        lang=None,
        extra_hash_dirs=(),
        exported_symbols=frozenset(),
    )
    assert returned is root
    assert store.published == []
