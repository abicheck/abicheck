# pylint: disable=too-many-branches,too-many-statements,too-many-locals,too-many-arguments,too-many-return-statements
"""B2: `<built-in>` types polluting dump (abi-dumper #38, abicc PR#124).

castxml output can include entries referencing `<builtin>` or `<command-line>`
pseudo-files. These represent compiler built-in type declarations (e.g.
`__builtin_va_list`) and should not appear in the ABI snapshot as public types
or functions, as they are not user-visible ABI surface.

Detection mechanism:
- castxml XML elements carry a ``file`` attribute pointing directly to a
  ``File`` element id (e.g. ``file="f0"``)
- The ``File`` element's ``name`` attribute may be ``<builtin>``, ``<built-in>``,
  or ``<command-line>``
- There are NO separate ``Location`` elements in real castxml output (the
  compound ``location="f0:0"`` attribute is informational only)
- Dumper should skip any element whose resolved file is a pseudo-file

Filtering is applied in: parse_functions(), parse_variables(),
_is_public_record_type(), parse_enums(), parse_typedefs().
"""

from __future__ import annotations

from xml.etree.ElementTree import Element, SubElement


def _make_castxml_xml_with_builtin() -> Element:
    """Build a castxml XML tree using REAL castxml format.

    Uses ``file="fN"`` attributes on elements directly (no Location elements).
    File names match actual castxml output: ``<builtin>`` (no hyphen).
    """
    root = Element("CastXML")

    # File entries — real castxml format
    user_file = SubElement(root, "File")
    user_file.set("id", "f1")
    user_file.set("name", "mylib.h")

    builtin_file = SubElement(root, "File")
    builtin_file.set("id", "f0")
    builtin_file.set("name", "<builtin>")  # real castxml: no hyphen

    cmdline_file = SubElement(root, "File")
    cmdline_file.set("id", "f2")
    cmdline_file.set("name", "<command-line>")

    # User-defined struct (should appear in snapshot)
    user_struct = SubElement(root, "Struct")
    user_struct.set("id", "_1")
    user_struct.set("name", "MyStruct")
    user_struct.set("size", "64")
    user_struct.set("align", "32")
    user_struct.set("file", "f1")  # real castxml: direct file attr
    user_struct.set("location", "f1:5")

    # Built-in function with __ prefix (should NOT appear)
    builtin_func = SubElement(root, "Function")
    builtin_func.set("id", "_2")
    builtin_func.set("name", "__builtin_va_list")
    builtin_func.set("mangled", "__builtin_va_list")
    builtin_func.set("returns", "")
    builtin_func.set("file", "f0")
    builtin_func.set("location", "f0:0")

    # User-defined function (should appear in snapshot)
    user_func = SubElement(root, "Function")
    user_func.set("id", "_3")
    user_func.set("name", "my_func")
    user_func.set("mangled", "my_func")
    user_func.set("returns", "")
    user_func.set("file", "f1")
    user_func.set("location", "f1:1")

    # Built-in struct with __ prefix (should NOT appear — name filter catches it)
    builtin_struct = SubElement(root, "Struct")
    builtin_struct.set("id", "_4")
    builtin_struct.set("name", "__va_list_tag")
    builtin_struct.set("size", "32")
    builtin_struct.set("file", "f0")
    builtin_struct.set("location", "f0:0")

    # Command-line struct (should NOT appear)
    cmdline_struct = SubElement(root, "Struct")
    cmdline_struct.set("id", "_5")
    cmdline_struct.set("name", "__CMDLINE_DEFINE__")
    cmdline_struct.set("size", "32")
    cmdline_struct.set("file", "f2")
    cmdline_struct.set("location", "f2:0")

    return root


class TestBuiltinsFiltered:
    """Verify <built-in> types/functions do not pollute the ABI snapshot."""

    def test_user_struct_in_types(self) -> None:
        """User-defined struct from mylib.h must appear in parse_types()."""
        from abicheck.dumper import _CastxmlParser

        root = _make_castxml_xml_with_builtin()
        parser = _CastxmlParser(root, exported_dynamic=set(), exported_static=set())
        types = parser.parse_types()
        names = {t.name for t in types}
        assert "MyStruct" in names

    def test_va_list_struct_not_in_types(self) -> None:
        """__va_list_tag (name starts with __) must NOT appear in parse_types()."""
        from abicheck.dumper import _CastxmlParser

        root = _make_castxml_xml_with_builtin()
        parser = _CastxmlParser(root, exported_dynamic=set(), exported_static=set())
        types = parser.parse_types()
        names = {t.name for t in types}
        assert "__va_list_tag" not in names

    def test_cmdline_struct_not_in_types(self) -> None:
        """__CMDLINE_DEFINE__ (name starts with __) must NOT appear in parse_types()."""
        from abicheck.dumper import _CastxmlParser

        root = _make_castxml_xml_with_builtin()
        parser = _CastxmlParser(root, exported_dynamic=set(), exported_static=set())
        types = parser.parse_types()
        names = {t.name for t in types}
        assert "__CMDLINE_DEFINE__" not in names

    def test_user_func_in_functions(self) -> None:
        """User-defined function from mylib.h must appear in parse_functions()."""
        from abicheck.dumper import _CastxmlParser

        root = _make_castxml_xml_with_builtin()
        parser = _CastxmlParser(
            root,
            exported_dynamic={"my_func"},
            exported_static={"my_func"},
        )
        funcs = parser.parse_functions()
        names = {f.name for f in funcs}
        assert "my_func" in names

    def test_builtin_func_filtered_from_snapshot(self) -> None:
        """__builtin_va_list must NOT appear in functions."""
        from abicheck.dumper import _CastxmlParser

        root = _make_castxml_xml_with_builtin()
        parser = _CastxmlParser(
            root,
            exported_dynamic={"my_func"},
            exported_static={"my_func"},
        )
        funcs = parser.parse_functions()
        names = {f.name for f in funcs}
        assert "__builtin_va_list" not in names

    def test_snapshot_no_builtin_types_in_public_api(self) -> None:
        """End-to-end: comparing two snapshots with only user types → no noise changes."""
        from abicheck.checker import compare
        from abicheck.model import AbiSnapshot, RecordType

        old = AbiSnapshot(
            library="lib.so",
            version="1.0",
            types=[
                RecordType(name="MyStruct", kind="struct"),
            ],
        )
        new = AbiSnapshot(
            library="lib.so",
            version="2.0",
            types=[
                RecordType(name="MyStruct", kind="struct"),
            ],
        )
        result = compare(old, new)
        assert not result.changes


class TestBuiltinLocationFilter:
    """Verify _is_builtin_element filters by <builtin>/<command-line> file,
    independent of the __ name prefix filter.
    Uses real castxml XML format: file attr on element, File elements in id-map.
    """

    def _make_xml_real_format(self) -> Element:
        """Build XML in real castxml format (file attr on element, no Location elements)."""
        root = Element("CastXML")

        user_file = SubElement(root, "File")
        user_file.set("id", "f1")
        user_file.set("name", "mylib.h")

        builtin_file = SubElement(root, "File")
        builtin_file.set("id", "f0")
        builtin_file.set("name", "<builtin>")  # real castxml: no hyphen

        cmdline_file = SubElement(root, "File")
        cmdline_file.set("id", "f2")
        cmdline_file.set("name", "<command-line>")

        # Normal user struct — should appear
        user_struct = SubElement(root, "Struct")
        user_struct.set("id", "_1")
        user_struct.set("name", "UserType")
        user_struct.set("size", "32")
        user_struct.set("file", "f1")
        user_struct.set("location", "f1:1")

        # Built-in struct WITHOUT __ prefix — must be filtered by file location
        builtin_struct = SubElement(root, "Struct")
        builtin_struct.set("id", "_2")
        builtin_struct.set(
            "name", "va_list"
        )  # no __ prefix — only location filter catches it
        builtin_struct.set("size", "64")
        builtin_struct.set("file", "f0")
        builtin_struct.set("location", "f0:0")

        # Command-line struct WITHOUT __ prefix — must be filtered by location
        cmdline_struct = SubElement(root, "Struct")
        cmdline_struct.set("id", "_3")
        cmdline_struct.set("name", "SomeDefine")
        cmdline_struct.set("size", "32")
        cmdline_struct.set("file", "f2")
        cmdline_struct.set("location", "f2:0")

        # Function in <builtin> without __ prefix
        builtin_func = SubElement(root, "Function")
        builtin_func.set("id", "_4")
        builtin_func.set("name", "compiler_hint")
        builtin_func.set("mangled", "compiler_hint")
        builtin_func.set("returns", "")
        builtin_func.set("file", "f0")
        builtin_func.set("location", "f0:0")

        # Built-in typedef (e.g. size_t from <builtin>)
        builtin_typedef = SubElement(root, "Typedef")
        builtin_typedef.set("id", "_5")
        builtin_typedef.set("name", "size_t")
        builtin_typedef.set("type", "_1")
        builtin_typedef.set("file", "f0")
        builtin_typedef.set("location", "f0:0")

        # User typedef — should appear
        user_typedef = SubElement(root, "Typedef")
        user_typedef.set("id", "_6")
        user_typedef.set("name", "MyAlias")
        user_typedef.set("type", "_1")
        user_typedef.set("file", "f1")
        user_typedef.set("location", "f1:2")

        # Built-in enum without __ prefix (e.g. from <builtin>)
        builtin_enum = SubElement(root, "Enumeration")
        builtin_enum.set("id", "_7")
        builtin_enum.set("name", "compiler_enum")
        builtin_enum.set("file", "f0")
        builtin_enum.set("location", "f0:0")

        # User enum — should appear
        user_enum = SubElement(root, "Enumeration")
        user_enum.set("id", "_8")
        user_enum.set("name", "MyEnum")
        user_enum.set("file", "f1")
        user_enum.set("location", "f1:3")

        # Variable in <builtin> without __ prefix — must be filtered
        builtin_var = SubElement(root, "Variable")
        builtin_var.set("id", "_9")
        builtin_var.set("name", "compiler_global")
        builtin_var.set("mangled", "compiler_global")
        builtin_var.set("type", "_1")
        builtin_var.set("file", "f0")
        builtin_var.set("location", "f0:0")

        return root

    def test_builtin_struct_no_underscore_filtered_by_location(self) -> None:
        """Struct 'va_list' (no __ prefix) from <builtin> must be filtered."""
        from abicheck.dumper import _CastxmlParser

        root = self._make_xml_real_format()
        parser = _CastxmlParser(root, exported_dynamic=set(), exported_static=set())
        types = parser.parse_types()
        names = {t.name for t in types}
        assert "UserType" in names
        assert "va_list" not in names, "va_list from <builtin> must be filtered"
        assert "SomeDefine" not in names, (
            "SomeDefine from <command-line> must be filtered"
        )

    def test_builtin_func_no_underscore_filtered_by_location(self) -> None:
        """Function from <builtin> must not appear even if in exported symbols."""
        from abicheck.dumper import _CastxmlParser

        root = self._make_xml_real_format()
        parser = _CastxmlParser(
            root,
            exported_dynamic={"compiler_hint"},
            exported_static={"compiler_hint"},
        )
        funcs = parser.parse_functions()
        names = {f.name for f in funcs}
        assert "compiler_hint" not in names

    def test_builtin_typedef_filtered(self) -> None:
        """Typedef from <builtin> (e.g. size_t) must not appear in typedefs."""
        from abicheck.dumper import _CastxmlParser

        root = self._make_xml_real_format()
        parser = _CastxmlParser(root, exported_dynamic=set(), exported_static=set())
        typedefs = parser.parse_typedefs()
        assert "size_t" not in typedefs
        assert "MyAlias" in typedefs

    def test_builtin_enum_filtered(self) -> None:
        """Enum from <builtin> without __ prefix must not appear in enums."""
        from abicheck.dumper import _CastxmlParser

        root = self._make_xml_real_format()
        parser = _CastxmlParser(root, exported_dynamic=set(), exported_static=set())
        enums = parser.parse_enums()
        names = {e.name for e in enums}
        assert "compiler_enum" not in names
        assert "MyEnum" in names

    def test_builtin_variable_no_underscore_filtered_by_location(self) -> None:
        """Variable from <builtin> must not appear even if it has a mangled name."""
        from abicheck.dumper import _CastxmlParser

        root = self._make_xml_real_format()
        parser = _CastxmlParser(
            root,
            exported_dynamic={"compiler_global"},
            exported_static={"compiler_global"},
        )
        variables = parser.parse_variables()
        names = {v.name for v in variables}
        assert "compiler_global" not in names, (
            "compiler_global from <builtin> must be filtered even if in exported symbols"
        )
