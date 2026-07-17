"""B1: `= delete` detection (abicc #100).

Tests for FUNC_DELETED change kind: a function previously callable becomes
`= delete`, which is a binary ABI break (callers will fail to link or crash).

Detection mechanism:
- castxml emits `deleted="1"` on Function/Method elements for `= delete`
- abicheck/dumper.py _CastxmlParser.parse_functions() maps this to Function.is_deleted
- abicheck/checker.py _diff_functions() emits FUNC_DELETED when is_deleted flips
- FUNC_DELETED is in BREAKING_KINDS → verdict=BREAKING

castxml status (verified): castxml DOES emit deleted="1" on deleted functions.
The dumper already reads el.get("deleted") == "1" → is_deleted=True.
"""
from __future__ import annotations

from abicheck.checker import ChangeKind, Verdict, compare
from abicheck.checker_policy import BREAKING_KINDS
from abicheck.elf_metadata import ElfMetadata, ElfSymbol, SymbolBinding, SymbolType
from abicheck.model import AbiSnapshot, Function, Visibility


def _snap(**kwargs: object) -> AbiSnapshot:
    defaults: dict[str, object] = dict(library="lib.so", version="1.0")
    defaults.update(kwargs)
    return AbiSnapshot(**defaults)  # type: ignore[arg-type]


def _func(name: str, mangled: str, **kwargs: object) -> Function:
    defaults: dict[str, object] = dict(return_type="void", visibility=Visibility.PUBLIC)
    defaults.update(kwargs)
    return Function(name=name, mangled=mangled, **defaults)  # type: ignore[arg-type]


def _elf_with_syms(*names: str) -> ElfMetadata:
    syms = [
        ElfSymbol(name=n, binding=SymbolBinding.GLOBAL, sym_type=SymbolType.FUNC, size=0)
        for n in names
    ]
    return ElfMetadata(symbols=syms)


class TestFuncDeletedModel:
    """Verify Function.is_deleted field exists and serializes correctly."""

    def test_is_deleted_field_exists(self) -> None:
        """Function dataclass must have is_deleted field."""
        f = _func("foo", "_Zfoo")
        assert hasattr(f, "is_deleted")
        assert f.is_deleted is False  # default

    def test_is_deleted_can_be_set(self) -> None:
        """Function.is_deleted can be set to True."""
        f = _func("foo", "_Zfoo", is_deleted=True)
        assert f.is_deleted is True

    def test_is_deleted_roundtrip(self) -> None:
        """is_deleted=True survives snapshot_to_dict → snapshot_from_dict."""
        from abicheck.serialization import snapshot_from_dict, snapshot_to_dict

        snap = _snap(functions=[_func("bar", "_Zbar", is_deleted=True)])
        d = snapshot_to_dict(snap)
        assert d["functions"][0]["is_deleted"] is True
        snap2 = snapshot_from_dict(d)
        assert snap2.functions[0].is_deleted is True

    def test_is_deleted_false_roundtrip(self) -> None:
        """is_deleted=False (default) must also survive roundtrip."""
        from abicheck.serialization import snapshot_from_dict, snapshot_to_dict

        snap = _snap(functions=[_func("bar", "_Zbar", is_deleted=False)])
        d = snapshot_to_dict(snap)
        assert d["functions"][0]["is_deleted"] is False
        snap2 = snapshot_from_dict(d)
        assert snap2.functions[0].is_deleted is False


class TestFuncDeletedChangeKind:
    """Verify FUNC_DELETED is in checker_policy and BREAKING_KINDS."""

    def test_func_deleted_in_breaking_kinds(self) -> None:
        """FUNC_DELETED must be in BREAKING_KINDS (abicc #100)."""
        assert ChangeKind.FUNC_DELETED in BREAKING_KINDS

    def test_func_deleted_enum_value(self) -> None:
        """FUNC_DELETED enum value is 'func_deleted'."""
        assert ChangeKind.FUNC_DELETED.value == "func_deleted"


class TestFuncDeletedDetection:
    """Verify that FUNC_DELETED is emitted when is_deleted changes."""

    def test_func_becomes_deleted_is_breaking(self) -> None:
        """v1: callable; v2: = delete → FUNC_DELETED, BREAKING."""
        old = _snap(functions=[_func("process", "_Zprocess")])
        new = _snap(functions=[_func("process", "_Zprocess", is_deleted=True)])
        result = compare(old, new)
        kinds = {c.kind for c in result.changes}
        assert ChangeKind.FUNC_DELETED in kinds
        assert result.verdict == Verdict.BREAKING

    def test_func_already_deleted_no_change(self) -> None:
        """v1: = delete; v2: = delete → no change emitted."""
        old = _snap(functions=[_func("process", "_Zprocess", is_deleted=True)])
        new = _snap(functions=[_func("process", "_Zprocess", is_deleted=True)])
        result = compare(old, new)
        kinds = {c.kind for c in result.changes}
        assert ChangeKind.FUNC_DELETED not in kinds

    def test_func_undeleted_is_compatible(self) -> None:
        """v1: = delete; v2: callable → function made available again (compatible extension)."""
        old = _snap(functions=[_func("process", "_Zprocess", is_deleted=True)])
        new = _snap(functions=[_func("process", "_Zprocess", is_deleted=False)])
        result = compare(old, new)
        # Should not be FUNC_DELETED (it went the other way)
        kinds = {c.kind for c in result.changes}
        assert ChangeKind.FUNC_DELETED not in kinds

    def test_method_becomes_deleted(self) -> None:
        """Class method becoming = delete is BREAKING."""
        old = _snap(functions=[
            _func("Foo::process", "_ZN3Foo7processEv", is_virtual=False)
        ])
        new = _snap(functions=[
            _func("Foo::process", "_ZN3Foo7processEv", is_virtual=False, is_deleted=True)
        ])
        result = compare(old, new)
        kinds = {c.kind for c in result.changes}
        assert ChangeKind.FUNC_DELETED in kinds
        assert result.verdict == Verdict.BREAKING


class TestFuncDeletedCastxmlMock:
    """Mock castxml XML to verify dumper correctly parses deleted="1".

    Tests the dumper's _CastxmlParser without running castxml binary.
    """

    def _make_xml_root(
        self,
        *,
        deleted: str = "0",
        tag: str = "Function",
        name: str = "doWork",
        mangled: str = "_Z6doWorkv",
    ) -> object:
        """Build a minimal castxml XML tree with one callable element."""
        from xml.etree.ElementTree import Element, SubElement

        root = Element("CastXML")
        # File entry
        file_el = SubElement(root, "File")
        file_el.set("id", "f1")
        file_el.set("name", "test.h")
        # Location entry
        loc_el = SubElement(root, "Location")
        loc_el.set("id", "l1")
        loc_el.set("file", "f1")
        loc_el.set("line", "1")
        # Callable entry
        func_el = SubElement(root, tag)
        func_el.set("id", "_1")
        func_el.set("name", name)
        func_el.set("mangled", mangled)
        func_el.set("returns", "")
        func_el.set("location", "l1")
        func_el.set("deleted", deleted)
        return root

    def test_dumper_parses_deleted_true(self) -> None:
        """CastxmlParser must set is_deleted=True when deleted='1' in XML."""
        from abicheck.dumper import _CastxmlParser

        root = self._make_xml_root(deleted="1")
        parser = _CastxmlParser(
            root,
            exported_dynamic={"_Z6doWorkv"},
            exported_static={"_Z6doWorkv"},
        )
        funcs = parser.parse_functions()
        assert len(funcs) == 1
        assert funcs[0].is_deleted is True

    def test_dumper_parses_deleted_false(self) -> None:
        """CastxmlParser must set is_deleted=False when deleted='0' (not present)."""
        from abicheck.dumper import _CastxmlParser

        root = self._make_xml_root(deleted="0")
        parser = _CastxmlParser(
            root,
            exported_dynamic={"_Z6doWorkv"},
            exported_static={"_Z6doWorkv"},
        )
        funcs = parser.parse_functions()
        assert len(funcs) == 1
        assert funcs[0].is_deleted is False

    def test_dumper_parses_deleted_constructor_true(self) -> None:
        """Constructor tag must preserve deleted='1' marker."""
        from abicheck.dumper import _CastxmlParser

        root = self._make_xml_root(
            deleted="1",
            tag="Constructor",
            name="Foo",
            mangled="_ZN3FooC1Ev",
        )
        parser = _CastxmlParser(
            root,
            exported_dynamic={"_ZN3FooC1Ev"},
            exported_static={"_ZN3FooC1Ev"},
        )
        funcs = parser.parse_functions()
        assert len(funcs) == 1
        assert funcs[0].name == "Foo"
        assert funcs[0].is_deleted is True

    def test_dumper_parses_deleted_destructor_true(self) -> None:
        """Destructor tag must preserve deleted='1' marker."""
        from abicheck.dumper import _CastxmlParser

        # castxml's real <Destructor name="..."> is the bare CLASS name (no
        # "~"), confirmed against a live castxml dump (Phase 2 parity gate,
        # PR #582) — the parser synthesizes the "~Foo" display name.
        root = self._make_xml_root(
            deleted="1",
            tag="Destructor",
            name="Foo",
            mangled="_ZN3FooD1Ev",
        )
        parser = _CastxmlParser(
            root,
            exported_dynamic={"_ZN3FooD1Ev"},
            exported_static={"_ZN3FooD1Ev"},
        )
        funcs = parser.parse_functions()
        assert len(funcs) == 1
        assert funcs[0].name == "~Foo"
        assert funcs[0].is_deleted is True

    def test_dumper_destructor_display_name_gets_tilde_synthesized(self) -> None:
        """castxml emits the bare class name on <Destructor>; the parser
        must synthesize the leading `~` rather than expecting it in the
        XML (Phase 2 parity gate, PR #582)."""
        from abicheck.dumper import _CastxmlParser

        root = self._make_xml_root(tag="Destructor", name="Foo", mangled="")
        parser = _CastxmlParser(root, exported_dynamic=set(), exported_static=set())
        funcs = parser.parse_functions()
        assert len(funcs) == 1
        assert funcs[0].name == "~Foo"
        assert funcs[0].mangled == "~Foo"


class TestFuncDeletedEdgeCases:
    """Edge-case regression coverage for abicc #100."""

    def test_free_function_deleted(self) -> None:
        """Free function becoming deleted must be BREAKING."""
        old = _snap(functions=[_func("process", "_Z7processv")])
        new = _snap(functions=[_func("process", "_Z7processv", is_deleted=True)])

        result = compare(old, new)
        kinds = {c.kind for c in result.changes}

        assert ChangeKind.FUNC_DELETED in kinds
        assert result.verdict == Verdict.BREAKING

    def test_one_overload_deleted(self) -> None:
        """Only the deleted overload should trigger FUNC_DELETED."""
        old = _snap(functions=[
            _func("process", "_Z7processi"),
            _func("process", "_Z7processf"),
        ])
        new = _snap(functions=[
            _func("process", "_Z7processi"),
            _func("process", "_Z7processf", is_deleted=True),
        ])

        result = compare(old, new)
        deleted_changes = [c for c in result.changes if c.kind == ChangeKind.FUNC_DELETED]
        deleted_symbols = {c.symbol for c in deleted_changes}

        assert deleted_symbols == {"_Z7processf"}
        assert len(deleted_changes) == 1  # must not double-report the same symbol
        assert result.verdict == Verdict.BREAKING

    def test_destructor_deleted(self) -> None:
        """Deleted destructor must be treated as BREAKING."""
        old = _snap(functions=[_func("~Foo", "_ZN3FooD1Ev")])
        new = _snap(functions=[_func("~Foo", "_ZN3FooD1Ev", is_deleted=True)])

        result = compare(old, new)
        kinds = {c.kind for c in result.changes}

        assert ChangeKind.FUNC_DELETED in kinds
        assert result.verdict == Verdict.BREAKING

    def test_template_instantiation_deleted(self) -> None:
        """Deleted template instantiation must be treated as BREAKING."""
        old = _snap(functions=[_func("foo<int>", "_Z3fooIiEvT_")])
        new = _snap(functions=[_func("foo<int>", "_Z3fooIiEvT_", is_deleted=True)])

        result = compare(old, new)
        kinds = {c.kind for c in result.changes}

        assert ChangeKind.FUNC_DELETED in kinds
        assert result.verdict == Verdict.BREAKING

    def test_deleted_to_callable_is_not_breaking(self) -> None:
        """Reverting `= delete` should not emit FUNC_DELETED or BREAKING verdict."""
        old = _snap(functions=[_func("process", "_Z7processv", is_deleted=True)])
        new = _snap(functions=[_func("process", "_Z7processv", is_deleted=False)])

        result = compare(old, new)
        kinds = {c.kind for c in result.changes}

        assert ChangeKind.FUNC_DELETED not in kinds
        assert ChangeKind.FUNC_DELETED_ELF_FALLBACK not in kinds
        assert result.verdict != Verdict.BREAKING

    def test_elf_fallback_not_double_reported(self) -> None:
        """Explicit castxml deletion marker must prevent ELF fallback duplicate.

        ELF metadata is intentionally provided so the fallback detector sees a
        symbol disappear from dynsym — without is_deleted=True it would fire
        FUNC_DELETED_ELF_FALLBACK.  With is_deleted=True the checker must take
        the castxml path (FUNC_DELETED) and skip the ELF path.
        """
        mangled = "_Z7processv"
        old = _snap(
            functions=[_func("process", mangled)],
            elf=_elf_with_syms(mangled),
        )
        new = _snap(
            functions=[_func("process", mangled, is_deleted=True)],
            elf=_elf_with_syms(),  # symbol also gone from dynsym
        )

        result = compare(old, new)
        kinds = {c.kind for c in result.changes}

        assert ChangeKind.FUNC_DELETED in kinds
        assert ChangeKind.FUNC_DELETED_ELF_FALLBACK not in kinds
