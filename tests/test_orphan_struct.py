"""B3: Struct not used in func args but in public header (abi-dumper #31).

abicheck should capture structs declared in public headers even when NOT used
as any function parameter or return type ("orphan types").

This is important because:
1. Orphan structs in public headers are part of the public ABI
2. Size/layout changes to them affect any code that allocates them
3. Their absence from function signatures doesn't remove them from the ABI surface

Current status (verified via code inspection):
- The CastxmlParser.parse_types() parses ALL Struct/Class/Union elements
  that pass _is_public_record_type() — regardless of whether they appear
  in any function signature
- Therefore abicheck DOES capture orphan structs from public headers
- This test serves as a regression test to prevent future regressions

Gap note: the "reachability" model (only types reachable from function args)
is intentionally NOT implemented — all public header types are captured.
See abi-dumper #31 for context on tools that missed this case.
"""

from __future__ import annotations

from xml.etree.ElementTree import Element, SubElement


def _make_xml_with_orphan_struct() -> Element:
    """Build castxml XML with an orphan struct (not used in any function)."""
    root = Element("CastXML")

    file_el = SubElement(root, "File")
    file_el.set("id", "f1")
    file_el.set("name", "mylib.h")

    loc1 = SubElement(root, "Location")
    loc1.set("id", "l1")
    loc1.set("file", "f1")
    loc1.set("line", "10")

    loc2 = SubElement(root, "Location")
    loc2.set("id", "l2")
    loc2.set("file", "f1")
    loc2.set("line", "20")

    # Orphan struct — not referenced by any function
    orphan = SubElement(root, "Struct")
    orphan.set("id", "_1")
    orphan.set("name", "Orphan")
    orphan.set("size", "32")
    orphan.set("align", "32")
    orphan.set("location", "l1")

    field1 = SubElement(orphan, "Field")
    field1.set("id", "_1f1")
    field1.set("name", "x")
    field1.set("type", "_int")
    field1.set("offset", "0")

    # Also add a FundamentalType for int
    int_type = SubElement(root, "FundamentalType")
    int_type.set("id", "_int")
    int_type.set("name", "int")

    # A function that uses a different struct (not Orphan)
    func_el = SubElement(root, "Function")
    func_el.set("id", "_2")
    func_el.set("name", "do_something")
    func_el.set("mangled", "do_something")
    func_el.set("returns", "_int")
    func_el.set("location", "l2")

    return root


class TestOrphanStruct:
    """Verify orphan structs (not used in function args) are captured."""

    def test_orphan_struct_captured_by_parser(self) -> None:
        """CastxmlParser.parse_types() must include Orphan struct."""
        from abicheck.dumper import _CastxmlParser

        root = _make_xml_with_orphan_struct()
        parser = _CastxmlParser(
            root,
            exported_dynamic={"do_something"},
            exported_static={"do_something"},
        )
        types = parser.parse_types()
        names = {t.name for t in types}
        assert "Orphan" in names, (
            "Orphan struct declared in public header must be captured "
            "even if not used in any function parameter/return type"
        )

    def test_orphan_struct_has_correct_kind(self) -> None:
        """Orphan struct must have kind='struct'."""
        from abicheck.dumper import _CastxmlParser

        root = _make_xml_with_orphan_struct()
        parser = _CastxmlParser(
            root,
            exported_dynamic={"do_something"},
            exported_static={"do_something"},
        )
        types = parser.parse_types()
        orphans = [t for t in types if t.name == "Orphan"]
        assert len(orphans) == 1
        assert orphans[0].kind == "struct"

    def test_orphan_struct_size_captured(self) -> None:
        """Orphan struct size must be captured."""
        from abicheck.dumper import _CastxmlParser

        root = _make_xml_with_orphan_struct()
        parser = _CastxmlParser(
            root,
            exported_dynamic={"do_something"},
            exported_static={"do_something"},
        )
        types = parser.parse_types()
        orphan = next(t for t in types if t.name == "Orphan")
        assert orphan.size_bits == 32

    def test_orphan_struct_change_detected(self) -> None:
        """ABI change in orphan struct (size changed) must be detected."""
        from abicheck.checker import ChangeKind, Verdict, compare
        from abicheck.model import AbiSnapshot, RecordType

        old = AbiSnapshot(
            library="lib.so",
            version="1.0",
            types=[
                RecordType(name="Orphan", kind="struct", size_bits=32),
            ],
        )
        new = AbiSnapshot(
            library="lib.so",
            version="2.0",
            types=[
                RecordType(name="Orphan", kind="struct", size_bits=64),
            ],
        )
        result = compare(old, new)
        kinds = {c.kind for c in result.changes}
        assert ChangeKind.TYPE_SIZE_CHANGED in kinds
        assert result.verdict == Verdict.BREAKING

    def test_orphan_struct_removal_detected(self) -> None:
        """Removing an orphan struct from public API must be detected."""
        from abicheck.checker import ChangeKind, Verdict, compare
        from abicheck.model import AbiSnapshot, RecordType

        old = AbiSnapshot(
            library="lib.so",
            version="1.0",
            types=[
                RecordType(name="Orphan", kind="struct", size_bits=32),
            ],
        )
        new = AbiSnapshot(library="lib.so", version="2.0", types=[])
        result = compare(old, new)
        kinds = {c.kind for c in result.changes}
        assert ChangeKind.TYPE_REMOVED in kinds
        assert result.verdict == Verdict.BREAKING

    def test_orphan_struct_model_roundtrip(self) -> None:
        """Orphan struct survives snapshot serialization roundtrip."""
        from abicheck.model import AbiSnapshot, RecordType, TypeField
        from abicheck.serialization import snapshot_from_dict, snapshot_to_dict

        snap = AbiSnapshot(
            library="lib.so",
            version="1.0",
            types=[
                RecordType(
                    name="Orphan",
                    kind="struct",
                    size_bits=32,
                    fields=[TypeField(name="x", type="int", offset_bits=0)],
                )
            ],
        )
        d = snapshot_to_dict(snap)
        snap2 = snapshot_from_dict(d)
        assert len(snap2.types) == 1
        assert snap2.types[0].name == "Orphan"
        assert snap2.types[0].fields[0].name == "x"
