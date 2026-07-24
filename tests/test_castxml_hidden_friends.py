"""Unit tests for castxml hidden-friend parsing in the dumper.

These tests construct synthetic castxml XML fragments rather than
shelling out to the real ``castxml`` binary, so they run in the fast
default suite without external tooling. The shapes mirror what
``castxml --castxml-output=1`` actually emits for in-class ``friend``
declarations: an ``OperatorFunction`` (or ``Function``) element at
namespace scope, plus a ``befriending`` attribute on the class element
that lists the friend ids.
"""

from xml.etree.ElementTree import Element, SubElement

from abicheck.dumper import _CastxmlParser


def _make_root_with_hidden_friend() -> Element:
    """Mirror castxml output for a class with an in-class friend ``operator==``."""
    root = Element("CastXML", attrib={"format": "1.4.0"})

    # File element so the parser's _is_builtin_element check passes.
    f1 = SubElement(root, "File")
    f1.set("id", "f1")
    f1.set("name", "mylib.h")

    # Global namespace.
    ns = SubElement(root, "Namespace")
    ns.set("id", "_1")
    ns.set("name", "::")

    # User namespace.
    user_ns = SubElement(root, "Namespace")
    user_ns.set("id", "_7")
    user_ns.set("name", "mylib")
    user_ns.set("context", "_1")

    # Class with `befriending` pointing at the friend operator's id.
    cls = SubElement(root, "Class")
    cls.set("id", "_14")
    cls.set("name", "point")
    cls.set("context", "_7")
    cls.set("file", "f1")
    cls.set("location", "f1:3")
    cls.set("befriending", "_34")
    cls.set("size", "64")
    cls.set("align", "32")

    # Const reference type for arguments.
    SubElement(root, "FundamentalType", attrib={"id": "_b", "name": "bool"})

    # The hidden friend itself — emitted as <OperatorFunction> at namespace scope.
    op = SubElement(root, "OperatorFunction")
    op.set("id", "_34")
    op.set("name", "==")
    op.set("returns", "_b")
    op.set("context", "_7")
    op.set("file", "f1")
    op.set("location", "f1:9")
    op.set("inline", "1")
    op.set("mangled", "_ZN5mylibeqERKNS_5pointES2_")

    return root


def _make_root_with_namespace_operator() -> Element:
    """An ``OperatorFunction`` at namespace scope that is NOT a friend.

    Verifies that ``is_hidden_friend`` defaults to False when the class
    does not list the operator id in its ``befriending`` attribute.
    """
    root = Element("CastXML", attrib={"format": "1.4.0"})

    f1 = SubElement(root, "File")
    f1.set("id", "f1")
    f1.set("name", "mylib.h")

    SubElement(root, "Namespace", attrib={"id": "_1", "name": "::"})
    SubElement(root, "FundamentalType", attrib={"id": "_b", "name": "bool"})

    op = SubElement(root, "OperatorFunction")
    op.set("id", "_44")
    op.set("name", "==")
    op.set("returns", "_b")
    op.set("context", "_1")
    op.set("file", "f1")
    op.set("location", "f1:9")
    op.set("mangled", "_Zeq5pointS_")

    return root


class TestHiddenFriendDumper:
    def test_operator_function_parsed_as_function(self) -> None:
        root = _make_root_with_hidden_friend()
        parser = _CastxmlParser(root, exported_dynamic=set(), exported_static=set())
        funcs = parser.parse_functions()
        assert len(funcs) == 1
        assert funcs[0].name == "operator=="
        assert funcs[0].mangled == "_ZN5mylibeqERKNS_5pointES2_"

    def test_befriending_marks_hidden_friend(self) -> None:
        root = _make_root_with_hidden_friend()
        parser = _CastxmlParser(root, exported_dynamic=set(), exported_static=set())
        funcs = parser.parse_functions()
        assert funcs[0].is_hidden_friend is True

    def test_namespace_operator_not_marked_as_friend(self) -> None:
        """A free-function ``operator==`` at namespace scope (no class
        ``befriending`` reference) must NOT be flagged as a hidden friend."""
        root = _make_root_with_namespace_operator()
        parser = _CastxmlParser(root, exported_dynamic=set(), exported_static=set())
        funcs = parser.parse_functions()
        assert len(funcs) == 1
        assert funcs[0].is_hidden_friend is False

    def test_multiple_befriending_ids_split_on_whitespace(self) -> None:
        """``befriending`` is whitespace-separated. Verify all listed ids
        are picked up, not just the first."""
        root = Element("CastXML", attrib={"format": "1.4.0"})
        SubElement(root, "File", attrib={"id": "f1", "name": "mylib.h"})
        SubElement(root, "Namespace", attrib={"id": "_1", "name": "::"})
        SubElement(root, "FundamentalType", attrib={"id": "_b", "name": "bool"})
        SubElement(root, "FundamentalType", attrib={"id": "_p", "name": "point"})

        cls = SubElement(root, "Class")
        cls.set("id", "_14")
        cls.set("name", "point")
        cls.set("context", "_1")
        cls.set("file", "f1")
        cls.set("location", "f1:3")
        cls.set("befriending", "_34 _35")
        cls.set("size", "64")
        cls.set("align", "32")

        for fid, opname, mangled in [
            ("_34", "==", "_Zeq5pointS_"),
            ("_35", "+", "_Zpl5pointS_"),
        ]:
            op = SubElement(root, "OperatorFunction")
            op.set("id", fid)
            op.set("name", opname)
            op.set("returns", "_b" if opname == "==" else "_p")
            op.set("context", "_1")
            op.set("file", "f1")
            op.set("location", "f1:9")
            op.set("mangled", mangled)

        parser = _CastxmlParser(root, exported_dynamic=set(), exported_static=set())
        funcs = parser.parse_functions()
        friends = {f.name for f in funcs if f.is_hidden_friend}
        assert friends == {"operator==", "operator+"}

    def test_operator_name_prefix_normalized(self) -> None:
        """castxml emits operator name as bare symbol (e.g. ``+``). The
        parser must normalize to the canonical ``operator+`` form."""
        root = _make_root_with_hidden_friend()
        parser = _CastxmlParser(root, exported_dynamic=set(), exported_static=set())
        funcs = parser.parse_functions()
        assert all(f.name.startswith("operator") for f in funcs)

    def test_hidden_friend_owner_resolved_to_qualified_class_name(self) -> None:
        """``hidden_friend_owner`` is resolved from the befriending class's
        ``context`` chain — the class ``point`` is nested in namespace
        ``mylib``, so the owner must be the qualified ``mylib::point``."""
        root = _make_root_with_hidden_friend()
        parser = _CastxmlParser(root, exported_dynamic=set(), exported_static=set())
        funcs = parser.parse_functions()
        assert funcs[0].is_hidden_friend is True
        assert funcs[0].hidden_friend_owner == "mylib::point"

    def test_union_befriending_marks_hidden_friend_and_owner(self) -> None:
        """A ``friend`` declared inside a union must be tracked the same as
        one declared inside a class/struct (Codex review: _record_els
        already includes Union elements, but the owner-resolution filter
        previously excluded them)."""
        root = Element("CastXML", attrib={"format": "1.4.0"})
        SubElement(root, "File", attrib={"id": "f1", "name": "mylib.h"})
        SubElement(root, "Namespace", attrib={"id": "_1", "name": "::"})
        SubElement(root, "FundamentalType", attrib={"id": "_b", "name": "bool"})

        un = SubElement(root, "Union")
        un.set("id", "_14")
        un.set("name", "variant")
        un.set("context", "_1")
        un.set("file", "f1")
        un.set("location", "f1:3")
        un.set("befriending", "_34")
        un.set("size", "64")
        un.set("align", "32")

        op = SubElement(root, "OperatorFunction")
        op.set("id", "_34")
        op.set("name", "==")
        op.set("returns", "_b")
        op.set("context", "_1")
        op.set("file", "f1")
        op.set("location", "f1:9")
        op.set("mangled", "_ZeqRK7variantS0_")

        parser = _CastxmlParser(root, exported_dynamic=set(), exported_static=set())
        funcs = parser.parse_functions()
        assert len(funcs) == 1
        assert funcs[0].is_hidden_friend is True
        assert funcs[0].hidden_friend_owner == "variant"

    def test_multiple_befriending_classes_public_owner_wins_regardless_of_order(
        self,
    ) -> None:
        """Regression (Codex review): the same free function can legitimately
        be befriended by more than one class. ``hidden_friend_owner`` holds
        only a single owner, so a public befriending class must always win
        over a private one, and once recorded must never be displaced by a
        later private owner — a public ADL function must not look
        privately-owned only because processing happened to visit the
        non-public befriending class last."""
        for public_first in (True, False):
            root = Element("CastXML", attrib={"format": "1.4.0"})
            SubElement(root, "File", attrib={"id": "fpub", "name": "public.h"})
            SubElement(root, "File", attrib={"id": "fpriv", "name": "private.h"})
            SubElement(root, "Namespace", attrib={"id": "_1", "name": "::"})
            SubElement(root, "FundamentalType", attrib={"id": "_b", "name": "bool"})

            pub_cls = Element("Class")
            pub_cls.set("id", "_pub")
            pub_cls.set("name", "PublicType")
            pub_cls.set("context", "_1")
            pub_cls.set("file", "fpub")
            pub_cls.set("line", "3")
            pub_cls.set("befriending", "_34")
            pub_cls.set("size", "8")
            pub_cls.set("align", "8")

            priv_cls = Element("Class")
            priv_cls.set("id", "_priv")
            priv_cls.set("name", "PrivateType")
            priv_cls.set("context", "_1")
            priv_cls.set("file", "fpriv")
            priv_cls.set("line", "3")
            priv_cls.set("befriending", "_34")
            priv_cls.set("size", "8")
            priv_cls.set("align", "8")

            ordered = [pub_cls, priv_cls] if public_first else [priv_cls, pub_cls]
            for cls in ordered:
                root.append(cls)

            op = SubElement(root, "OperatorFunction")
            op.set("id", "_34")
            op.set("name", "==")
            op.set("returns", "_b")
            op.set("context", "_1")
            op.set("file", "fpub")
            op.set("line", "9")
            op.set("mangled", "_Zeq10PublicTypeS_")

            parser = _CastxmlParser(
                root,
                exported_dynamic=set(),
                exported_static=set(),
                public_header_paths=["public.h"],
            )
            funcs = parser.parse_functions()
            assert len(funcs) == 1
            assert funcs[0].is_hidden_friend is True
            assert funcs[0].hidden_friend_owner == "PublicType", (
                f"public_first={public_first}: expected public owner to win"
            )
