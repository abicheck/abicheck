# Copyright 2026 Nikolay Petrov
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

"""Tests for idiom & anti-pattern recognition (ADR-025 A2)."""

from __future__ import annotations

from abicheck.checker_policy import ChangeKind
from abicheck.idioms import Idiom, detect_antipatterns, recognise_idioms
from abicheck.model import (
    AbiSnapshot,
    AccessLevel,
    Function,
    Param,
    RecordType,
    TypeField,
    Visibility,
)
from abicheck.surface_graph import build_surface_graph


def _tags(snap: AbiSnapshot) -> dict[str, list]:
    return recognise_idioms(build_surface_graph(snap))


def test_opaque_pointer_requires_hidden_definition() -> None:
    # Opaque: incomplete struct, only ever crossed by pointer.
    snap = AbiSnapshot(
        library="l",
        version="1",
        from_headers=True,
        functions=[
            Function(
                name="open",
                mangled="open",
                return_type="Ctx*",
                params=[],
                visibility=Visibility.PUBLIC,
                return_pointer_depth=1,
            ),
            Function(
                name="use",
                mangled="use",
                return_type="void",
                params=[Param(name="c", type="Ctx*", pointer_depth=1)],
                visibility=Visibility.PUBLIC,
            ),
        ],
        types=[RecordType(name="Ctx", kind="struct", is_opaque=True)],
    )
    tags = _tags(snap)
    assert any(t.idiom == Idiom.OPAQUE_POINTER for t in tags.get("Ctx", []))
    tag = next(t for t in tags["Ctx"] if t.idiom == Idiom.OPAQUE_POINTER)
    assert tag.definition_hidden is True


def test_complete_public_type_is_not_opaque() -> None:
    # A complete struct with a public field is observable (sizeof) → NOT opaque,
    # even though the only public function takes it by pointer (Codex P1).
    snap = AbiSnapshot(
        library="l",
        version="1",
        from_headers=True,
        functions=[
            Function(
                name="use",
                mangled="use",
                return_type="void",
                params=[Param(name="c", type="Vec*", pointer_depth=1)],
                visibility=Visibility.PUBLIC,
            ),
        ],
        types=[
            RecordType(
                name="Vec",
                kind="struct",
                size_bits=128,
                fields=[TypeField(name="len", type="int", access=AccessLevel.PUBLIC)],
            )
        ],
    )
    assert not any(t.idiom == Idiom.OPAQUE_POINTER for t in _tags(snap).get("Vec", []))


def test_by_value_use_blocks_opaque() -> None:
    # Even an incomplete-looking type used by value somewhere is observable.
    snap = AbiSnapshot(
        library="l",
        version="1",
        from_headers=True,
        functions=[
            Function(
                name="byval",
                mangled="byval",
                return_type="Ctx",
                params=[],
                visibility=Visibility.PUBLIC,
            ),
            Function(
                name="byptr",
                mangled="byptr",
                return_type="void",
                params=[Param(name="c", type="Ctx*", pointer_depth=1)],
                visibility=Visibility.PUBLIC,
            ),
        ],
        types=[RecordType(name="Ctx", kind="struct", is_opaque=True)],
    )
    assert not any(t.idiom == Idiom.OPAQUE_POINTER for t in _tags(snap).get("Ctx", []))


def test_pimpl_records_wrapper_layout_and_pointee() -> None:
    snap = AbiSnapshot(
        library="l",
        version="1",
        from_headers=True,
        functions=[
            Function(
                name="w_use",
                mangled="w_use",
                return_type="void",
                params=[Param(name="w", type="Widget*", pointer_depth=1)],
                visibility=Visibility.PUBLIC,
            ),
        ],
        types=[
            RecordType(
                name="Widget",
                kind="class",
                size_bits=64,
                fields=[TypeField(name="impl", type="WidgetImpl*")],
            ),
            RecordType(name="WidgetImpl", kind="struct", is_opaque=True),
        ],
    )
    tags = _tags(snap)
    pimpl = next(t for t in tags["Widget"] if t.idiom == Idiom.PIMPL)
    assert pimpl.hidden_pointee == "WidgetImpl"
    assert pimpl.layout_signature is not None and "size=64" in pimpl.layout_signature


def test_pimpl_rejects_second_member() -> None:
    # A wrapper with a second data member is a real layout type, not PIMPL.
    snap = AbiSnapshot(
        library="l",
        version="1",
        from_headers=True,
        types=[
            RecordType(
                name="W",
                kind="class",
                size_bits=128,
                fields=[
                    TypeField(name="impl", type="Impl*"),
                    TypeField(name="flags", type="int"),
                ],
            ),
            RecordType(name="Impl", kind="struct", is_opaque=True),
        ],
    )
    assert "W" not in _tags(snap)


def test_handle_typedef() -> None:
    snap = AbiSnapshot(
        library="l",
        version="1",
        from_headers=True,
        typedefs={"Handle": "void *", "ImplPtr": "struct Impl *"},
        types=[RecordType(name="Impl", kind="struct", is_opaque=True)],
    )
    tags = _tags(snap)
    assert any(t.idiom == Idiom.HANDLE for t in tags.get("Handle", []))
    assert any(t.idiom == Idiom.HANDLE for t in tags.get("ImplPtr", []))


def test_handle_ambiguous_short_pointee_not_tagged() -> None:
    # ADR-027 review: when an unqualified handle pointee short name matches
    # several namespaced types — one incomplete, one complete — the typedef is
    # not provably opaque (it may point at the complete one), so it must not be
    # tagged HANDLE (which would drive a synthetic handle_type_changed break).
    snap = AbiSnapshot(
        library="l",
        version="1",
        from_headers=True,
        typedefs={"h": "Ctx *"},
        types=[
            RecordType(name="ns1::Ctx", kind="struct", is_opaque=True),
            RecordType(name="ns2::Ctx", kind="struct", is_opaque=False),
        ],
    )
    assert not any(t.idiom == Idiom.HANDLE for t in _tags(snap).get("h", []))


def test_handle_unambiguous_short_pointee_still_tagged() -> None:
    # Control: a single incomplete type with that short name is unambiguous, so
    # the short-name fallback still recognises the opaque handle.
    snap = AbiSnapshot(
        library="l",
        version="1",
        from_headers=True,
        typedefs={"h": "Ctx *"},
        types=[RecordType(name="ns1::Ctx", kind="struct", is_opaque=True)],
    )
    assert any(t.idiom == Idiom.HANDLE for t in _tags(snap).get("h", []))


def test_factory_returns_polymorphic_pointer() -> None:
    snap = AbiSnapshot(
        library="l",
        version="1",
        from_headers=True,
        functions=[
            Function(
                name="make_shape",
                mangled="make_shape",
                return_type="Shape*",
                visibility=Visibility.PUBLIC,
                return_pointer_depth=1,
            ),
        ],
        types=[RecordType(name="Shape", kind="class", vtable=["_ZN5Shape4areaEv"])],
    )
    assert any(t.idiom == Idiom.FACTORY for t in _tags(snap).get("make_shape", []))


def test_create_destroy_pair() -> None:
    snap = AbiSnapshot(
        library="l",
        version="1",
        from_headers=True,
        functions=[
            Function(
                name="thing_new",
                mangled="thing_new",
                return_type="Thing*",
                visibility=Visibility.PUBLIC,
                return_pointer_depth=1,
            ),
            Function(
                name="thing_free",
                mangled="thing_free",
                return_type="void",
                params=[Param(name="t", type="Thing*", pointer_depth=1)],
                visibility=Visibility.PUBLIC,
            ),
        ],
    )
    tags = _tags(snap)
    assert any(t.idiom == Idiom.CREATE_DESTROY for t in tags.get("thing_new", []))
    assert any(t.idiom == Idiom.CREATE_DESTROY for t in tags.get("thing_free", []))


def test_callback_abi_param() -> None:
    snap = AbiSnapshot(
        library="l",
        version="1",
        from_headers=True,
        functions=[
            Function(
                name="set_cb",
                mangled="set_cb",
                return_type="void",
                params=[Param(name="cb", type="void (*)(int)")],
                visibility=Visibility.PUBLIC,
            ),
        ],
    )
    assert any(t.idiom == Idiom.CALLBACK_ABI for t in _tags(snap).get("set_cb", []))


def test_callback_abi_castxml_functiontype_encoding() -> None:
    # castxml records a direct function-pointer param as "FunctionType*".
    snap = AbiSnapshot(
        library="l",
        version="1",
        from_headers=True,
        functions=[
            Function(
                name="set_cb",
                mangled="set_cb",
                return_type="void",
                params=[Param(name="cb", type="FunctionType*", pointer_depth=1)],
                visibility=Visibility.PUBLIC,
            ),
        ],
    )
    assert any(t.idiom == Idiom.CALLBACK_ABI for t in _tags(snap).get("set_cb", []))


def test_callback_abi_typedefed_callback() -> None:
    # A typedef'd callback: the param names the alias; its target is a fn ptr.
    snap = AbiSnapshot(
        library="l",
        version="1",
        from_headers=True,
        functions=[
            Function(
                name="on_event",
                mangled="on_event",
                return_type="void",
                params=[Param(name="h", type="Handler")],
                visibility=Visibility.PUBLIC,
            ),
        ],
        typedefs={"Handler": "FunctionType*"},
    )
    assert any(t.idiom == Idiom.CALLBACK_ABI for t in _tags(snap).get("on_event", []))


def test_plain_pointer_param_is_not_callback() -> None:
    # An ordinary non-const pointer param must NOT be tagged as a callback.
    snap = AbiSnapshot(
        library="l",
        version="1",
        from_headers=True,
        functions=[
            Function(
                name="lookup",
                mangled="lookup",
                return_type="int",
                params=[Param(name="key", type="Foo*", pointer_depth=1)],
                visibility=Visibility.PUBLIC,
            ),
        ],
        types=[RecordType(name="Foo", kind="struct")],
    )
    assert not any(t.idiom == Idiom.CALLBACK_ABI for t in _tags(snap).get("lookup", []))


def test_hidden_overload_by_value_does_not_block_opaque() -> None:
    # Public overload takes Ctx*; a HIDDEN overload `use(Ctx)` takes it by value.
    # The hidden overload must NOT suppress the OPAQUE_POINTER tag (Codex P2:
    # use per-function visibility, not demangled-name membership).
    snap = AbiSnapshot(
        library="l",
        version="1",
        from_headers=True,
        functions=[
            Function(
                name="ctx_open",
                mangled="ctx_open",
                return_type="Ctx*",
                visibility=Visibility.PUBLIC,
                return_pointer_depth=1,
            ),
            Function(
                name="use",
                mangled="_Z3useP3Ctx",
                return_type="void",
                params=[Param(name="c", type="Ctx*", pointer_depth=1)],
                visibility=Visibility.PUBLIC,
            ),
            # Hidden overload, same demangled name, by-value param.
            Function(
                name="use",
                mangled="_Z3use3Ctx",
                return_type="void",
                params=[Param(name="c", type="Ctx")],
                visibility=Visibility.HIDDEN,
            ),
        ],
        types=[RecordType(name="Ctx", kind="struct", is_opaque=True)],
    )
    assert any(t.idiom == Idiom.OPAQUE_POINTER for t in _tags(snap).get("Ctx", []))


def test_recognition_is_deterministic() -> None:
    snap = AbiSnapshot(
        library="l",
        version="1",
        from_headers=True,
        functions=[
            Function(
                name="open",
                mangled="open",
                return_type="Ctx*",
                visibility=Visibility.PUBLIC,
                return_pointer_depth=1,
            ),
        ],
        types=[RecordType(name="Ctx", kind="struct", is_opaque=True)],
    )
    assert _tags(snap) == _tags(snap)


def _opaque_snap() -> AbiSnapshot:
    return AbiSnapshot(
        library="libctx.so",
        version="1",
        from_headers=True,
        functions=[
            Function(
                name="ctx_open",
                mangled="ctx_open",
                return_type="Ctx*",
                visibility=Visibility.PUBLIC,
                return_pointer_depth=1,
            ),
            Function(
                name="ctx_close",
                mangled="ctx_close",
                return_type="void",
                params=[Param(name="c", type="Ctx*", pointer_depth=1)],
                visibility=Visibility.PUBLIC,
            ),
        ],
        types=[RecordType(name="Ctx", kind="struct", is_opaque=True)],
    )


def test_surface_report_idioms_text(tmp_path) -> None:
    from click.testing import CliRunner

    from abicheck.cli import main
    from abicheck.serialization import save_snapshot

    p = tmp_path / "libctx.abi.json"
    save_snapshot(_opaque_snap(), p)
    result = CliRunner().invoke(main, ["surface-report", str(p), "--idioms"])
    assert result.exit_code == 0, result.output
    assert "idioms recognised:" in result.output
    assert "opaque_pointer" in result.output


def test_pimpl_rejects_complete_pointee() -> None:
    # Wrapper holding a pointer to a *complete* type is not PIMPL (no hidden impl).
    snap = AbiSnapshot(
        library="l",
        version="1",
        from_headers=True,
        types=[
            RecordType(
                name="W",
                kind="class",
                size_bits=64,
                fields=[TypeField(name="p", type="Known*")],
            ),
            RecordType(
                name="Known",
                kind="struct",
                size_bits=32,
                fields=[TypeField(name="x", type="int")],
            ),
        ],
    )
    assert not any(t.idiom == Idiom.PIMPL for t in _tags(snap).get("W", []))


def test_handle_ignores_non_pointer_typedef() -> None:
    snap = AbiSnapshot(
        library="l",
        version="1",
        from_headers=True,
        typedefs={"Count": "unsigned long"},
    )
    assert "Count" not in _tags(snap)


def test_factory_ignores_non_polymorphic_return() -> None:
    # Returns a pointer to a plain struct (no vtable) → not a factory.
    snap = AbiSnapshot(
        library="l",
        version="1",
        from_headers=True,
        functions=[
            Function(
                name="get_buf",
                mangled="get_buf",
                return_type="Buf*",
                visibility=Visibility.PUBLIC,
                return_pointer_depth=1,
            ),
        ],
        types=[
            RecordType(
                name="Buf", kind="struct", fields=[TypeField(name="n", type="int")]
            )
        ],
    )
    assert "get_buf" not in _tags(snap)


def test_opaque_ignores_hidden_functions() -> None:
    # A hidden function passing Ctx by value must NOT block opacity — only
    # public functions are observable to callers.
    snap = AbiSnapshot(
        library="l",
        version="1",
        from_headers=True,
        functions=[
            Function(
                name="ctx_open",
                mangled="ctx_open",
                return_type="Ctx*",
                visibility=Visibility.PUBLIC,
                return_pointer_depth=1,
            ),
            Function(
                name="ctx_impl",
                mangled="_ZL8ctx_impl",
                return_type="Ctx",
                visibility=Visibility.HIDDEN,
            ),
        ],
        types=[RecordType(name="Ctx", kind="struct", is_opaque=True)],
    )
    assert any(t.idiom == Idiom.OPAQUE_POINTER for t in _tags(snap).get("Ctx", []))


def _antipatterns(snap: AbiSnapshot) -> list:
    return detect_antipatterns(build_surface_graph(snap))


def test_detect_stl_by_value_parameter() -> None:
    snap = AbiSnapshot(
        library="l",
        version="1",
        from_headers=True,
        functions=[
            Function(
                name="sink",
                mangled="_Z4sinkNSt6stringE",
                return_type="void",
                params=[Param(name="s", type="std::string", pointer_depth=0)],
                visibility=Visibility.PUBLIC,
            )
        ],
    )
    aps = _antipatterns(snap)
    assert any(
        a.kind == ChangeKind.PUBLIC_API_EXPOSES_STL_BY_VALUE and a.symbol == "sink"
        for a in aps
    )


def test_stl_by_pointer_is_not_flagged() -> None:
    snap = AbiSnapshot(
        library="l",
        version="1",
        from_headers=True,
        functions=[
            Function(
                name="ok",
                mangled="ok",
                return_type="void",
                params=[Param(name="s", type="std::string*", pointer_depth=1)],
                visibility=Visibility.PUBLIC,
            )
        ],
    )
    assert not _antipatterns(snap)


def test_detect_non_virtual_dtor_base() -> None:
    base = RecordType(
        name="Base",
        kind="class",
        vtable=["_ZN4Base3fooEv"],  # a virtual method, but no D0/D1/D2 dtor slot
    )
    derived = RecordType(name="Derived", kind="class", bases=["Base"])
    snap = AbiSnapshot(
        library="l", version="1", from_headers=True, types=[base, derived]
    )
    aps = _antipatterns(snap)
    assert any(
        a.kind == ChangeKind.POLYMORPHIC_TYPE_NON_VIRTUAL_DTOR and a.symbol == "Base"
        for a in aps
    )


def test_antipattern_ambiguous_short_name_not_cross_matched() -> None:
    # Codex P2: a factory returns ns1::Base*; ns2::Base is a different
    # polymorphic type with no virtual dtor. The short name "Base" must NOT make
    # ns2::Base look like a factory return → no false RISK finding.
    snap = AbiSnapshot(
        library="l",
        version="1",
        from_headers=True,
        functions=[
            Function(
                name="make",
                mangled="make",
                return_type="ns1::Base*",
                visibility=Visibility.PUBLIC,
                return_pointer_depth=1,
            ),
        ],
        types=[
            RecordType(name="ns1::Base", kind="class", vtable=["_ZN3ns14BaseD0Ev"]),
            RecordType(name="ns2::Base", kind="class", vtable=["_ZN3ns24Base3fooEv"]),
        ],
    )
    aps = _antipatterns(snap)
    # ns2::Base is unrelated to the ns1::Base factory and must not be flagged.
    assert not any(
        a.kind == ChangeKind.POLYMORPHIC_TYPE_NON_VIRTUAL_DTOR
        and a.symbol == "ns2::Base"
        for a in aps
    )


def test_virtual_dtor_base_not_flagged() -> None:
    base = RecordType(
        name="Base",
        kind="class",
        vtable=["_ZN4BaseD0Ev", "_ZN4Base3fooEv"],  # has a destructor slot
    )
    derived = RecordType(name="Derived", kind="class", bases=["Base"])
    snap = AbiSnapshot(
        library="l", version="1", from_headers=True, types=[base, derived]
    )
    assert not any(
        a.kind == ChangeKind.POLYMORPHIC_TYPE_NON_VIRTUAL_DTOR
        for a in _antipatterns(snap)
    )


def test_surface_report_idioms_json(tmp_path) -> None:
    import json as _json

    from click.testing import CliRunner

    from abicheck.cli import main
    from abicheck.serialization import save_snapshot

    p = tmp_path / "libctx.abi.json"
    save_snapshot(_opaque_snap(), p)
    result = CliRunner().invoke(
        main, ["surface-report", str(p), "--idioms", "--format", "json"]
    )
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert "Ctx" in data["idioms"]
    assert data["idioms"]["Ctx"][0]["idiom"] == "opaque_pointer"
    assert data["idioms"]["Ctx"][0]["definition_hidden"] is True


def test_surface_report_antipatterns_json(tmp_path) -> None:
    import json as _json

    from click.testing import CliRunner

    from abicheck.cli import main
    from abicheck.serialization import save_snapshot

    snap = AbiSnapshot(
        library="l",
        version="1",
        from_headers=True,
        functions=[
            Function(
                name="sink",
                mangled="_Z4sinkNSt6stringE",
                return_type="void",
                params=[Param(name="s", type="std::string", pointer_depth=0)],
                visibility=Visibility.PUBLIC,
            )
        ],
    )
    p = tmp_path / "lib.abi.json"
    save_snapshot(snap, p)
    result = CliRunner().invoke(
        main, ["surface-report", str(p), "--anti-patterns", "--format", "json"]
    )
    assert result.exit_code == 0, result.output
    data = _json.loads(result.output)
    assert any(
        a["kind"] == "public_api_exposes_stl_by_value" for a in data["anti_patterns"]
    )
