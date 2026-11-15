"""Template detectors key a declaration on its *name*, whatever spelling the
header backend produced.

A top-level C++ function is named by its bare identifier (castxml: ``kput``),
so its qualified name is read from the demangling, which carries the
parameter list -- and, for a function-template instantiation, a leading
return type. A class template may be modelled as its *pattern* (clang and
gcc-plugin backends), which spells no template arguments at all. Each case
below uses real Itanium manglings or hand-written C++ rules as its oracle.
"""

from __future__ import annotations

import pytest

from abicheck.checker_policy import ChangeKind
from abicheck.diff_templates import (
    detect_mandatory_template_param_added,
    detect_overload_set_rerouted,
)
from abicheck.model import AbiSnapshot, Function, Param, RecordType, Visibility
from abicheck.model.fact import Fact


def _snap(funcs=None, types=None) -> AbiSnapshot:
    return AbiSnapshot(
        library="libt.so",
        version="0",
        functions=list(funcs or []),
        types=list(types or []),
    )


def _fn(name: str, mangled: str, params: list[tuple[str, str]]) -> Function:
    return Function(
        name=name,
        mangled=mangled,
        return_type="int",
        params=[Param(name=n, type=t) for n, t in params],
        visibility=Visibility.PUBLIC,
    )


class TestOverloadSetIsTheName:
    # Castxml/DWARF spell a top-level C++ function's name unqualified
    # (``kput``), so its qualified name is recovered by demangling -- which
    # yields the whole signature ``kput(int)``. Each sibling below is a real
    # Itanium mangling of an overload set whose membership shifts; the set is
    # the *name*, so every one must re-route (independent oracle: the stems
    # are written out by hand, not derived from the implementation).
    @pytest.mark.parametrize(
        ("name", "old_members", "new_members", "stem"),
        [
            (  # free function, int -> long (the abiforge overload_retyped cell)
                "kput",
                [("_Z4kputi", "int"), ("_Z4kputd", "double")],
                [("_Z4kputl", "long int"), ("_Z4kputd", "double")],
                "kput",
            ),
            (  # namespaced free function reported unqualified
                "put",
                [("_ZN2ns3putEi", "int"), ("_ZN2ns3putEPKc", "const char*")],
                [("_ZN2ns3putEs", "short int"), ("_ZN2ns3putEPKc", "const char*")],
                "ns::put",
            ),
            (  # three-member set, one member retyped
                "f",
                [("_Z1fi", "int"), ("_Z1ff", "float"), ("_Z1fc", "char")],
                [("_Z1fi", "int"), ("_Z1fd", "double"), ("_Z1fc", "char")],
                "f",
            ),
        ],
    )
    def test_demangled_signature_names_share_one_overload_set(
        self,
        name: str,
        old_members: list[tuple[str, str]],
        new_members: list[tuple[str, str]],
        stem: str,
    ) -> None:
        old = _snap(
            funcs=[_fn(name, mangled=m, params=[("a", t)]) for m, t in old_members]
        )
        new = _snap(
            funcs=[_fn(name, mangled=m, params=[("a", t)]) for m, t in new_members]
        )
        changes = detect_overload_set_rerouted(old, new)
        assert [c.kind for c in changes] == [ChangeKind.OVERLOAD_SET_REROUTED]
        assert changes[0].symbol == stem

    def test_demangled_single_function_retype_still_no_finding(self) -> None:
        """The 1->1 guard still holds once names are reduced to their stem."""
        old = _snap(funcs=[_fn("kput", mangled="_Z4kputi", params=[("a", "int")])])
        new = _snap(funcs=[_fn("kput", mangled="_Z4kputl", params=[("a", "long int")])])
        assert detect_overload_set_rerouted(old, new) == []


class TestMandatoryTemplateParamAddedFromPattern:
    """A backend that models a class template as its pattern (``KMb`` with
    ``is_template_pattern``) records the parameter defaults, not a spelled
    instantiation. The oracle is the C++ rule: the mandatory arity is the
    number of parameters with no default, written by hand per row."""

    @staticmethod
    def _pattern(defaults: tuple[str | None, ...] | None) -> RecordType:
        rec = RecordType(name="KMb", kind="struct", is_template_pattern=True)
        rec.template_param_defaults_fact = (
            None if defaults is None else Fact.present(defaults)
        )
        return rec

    @pytest.mark.parametrize(
        ("old", "new", "fires"),
        [
            ((None,), (None, None), True),  # <T> -> <T, A>
            ((None,), (None, "int"), False),  # <T> -> <T, A = int>
            ((None, "int"), (None, None), True),  # <T, A = int> -> <T, A>
            ((None, None), (None,), False),  # a parameter dropped
            ((None,), (None,), False),  # unchanged
            (("int",), (None, None), True),  # <T = int> -> <T, A>
        ],
    )
    def test_mandatory_arity_from_defaults(
        self,
        old: tuple[str | None, ...],
        new: tuple[str | None, ...],
        fires: bool,
    ) -> None:
        changes = detect_mandatory_template_param_added(
            _snap(types=[self._pattern(old)]), _snap(types=[self._pattern(new)])
        )
        assert [c.symbol for c in changes] == (["KMb"] if fires else [])

    def test_unrecorded_defaults_state_nothing(self) -> None:
        """CastXML records no defaults: a pattern without the fact is no
        arity evidence on either side."""
        changes = detect_mandatory_template_param_added(
            _snap(types=[self._pattern(None)]), _snap(types=[self._pattern(None)])
        )
        assert changes == []


class TestMandatoryArityEvidenceKinds:
    """A pattern's mandatory count and an instantiation's spelled count are
    different quantities; they are compared only with their own kind. The
    oracle per row is hand-written from the C++ declarations in each comment.
    """

    @staticmethod
    def _pattern(
        defaults: tuple[str | None, ...],
        name: str = "Foo",
        qualified: str | None = None,
    ) -> RecordType:
        rec = RecordType(
            name=name, kind="struct", is_template_pattern=True, qualified_name=qualified
        )
        rec.template_param_defaults_fact = Fact.present(defaults)
        return rec

    @staticmethod
    def _inst(spelling: str, qualified: str | None = None) -> RecordType:
        return RecordType(name=spelling, kind="struct", qualified_name=qualified)

    @pytest.mark.parametrize(
        ("old_types", "new_types", "fires"),
        [
            # clang old: template<T, U = int> Foo; castxml new: only Foo<int, int>
            # -- the template did not change; 1 vs 2 counts different things.
            ("pattern:None,int", "inst:Foo<int, int>", False),
            # Mirror: castxml old, clang new.
            ("inst:Foo<int, int>", "pattern:None,int", False),
            # Pattern on both sides, U lost its default: a real break.
            ("pattern:None,int", "pattern:None,None", True),
            # Instantiations on both sides: min 1 -> 2.
            ("inst:Foo<int>", "inst:Foo<int, char>", True),
            # Both kinds on both sides: the pattern (unchanged) decides, even
            # though the smallest exported instantiation disappeared.
            (
                "pattern:None,int|inst:Foo<int>",
                "pattern:None,int|inst:Foo<int, int>",
                False,
            ),
            # Pattern only old, both kinds new: instantiation is not comparable
            # (old has none), pattern unchanged.
            ("pattern:None", "pattern:None|inst:Foo<int, int>", False),
        ],
    )
    def test_like_with_like(self, old_types: str, new_types: str, fires: bool) -> None:
        def build(spec: str) -> list[RecordType]:
            out: list[RecordType] = []
            for part in spec.split("|"):
                kind, _, val = part.partition(":")
                if kind == "pattern":
                    out.append(
                        self._pattern(
                            tuple(None if d == "None" else d for d in val.split(","))
                        )
                    )
                else:
                    out.append(self._inst(val))
            return out

        changes = detect_mandatory_template_param_added(
            _snap(types=build(old_types)), _snap(types=build(new_types))
        )
        assert [c.symbol for c in changes] == (["Foo"] if fires else [])

    def test_equal_leaf_names_in_different_namespaces(self) -> None:
        """``a::Box<T>`` -> ``a::Box<T, U>`` is a break even while ``b::Box<T>``
        (same bare ``name``) is unchanged; ``b::Box`` gets no finding."""
        old = _snap(
            types=[
                self._pattern((None,), name="Box", qualified="a::Box"),
                self._pattern((None,), name="Box", qualified="b::Box"),
            ]
        )
        new = _snap(
            types=[
                self._pattern((None, None), name="Box", qualified="a::Box"),
                self._pattern((None,), name="Box", qualified="b::Box"),
            ]
        )
        changes = detect_mandatory_template_param_added(old, new)
        assert [(c.symbol, c.old_value, c.new_value) for c in changes] == [
            ("a::Box", "min_arity=1", "min_arity=2")
        ]

    def test_equal_leaf_instantiations_in_different_namespaces(self) -> None:
        old = _snap(
            types=[
                self._inst("Box<int>", "a::Box<int>"),
                self._inst("Box<int>", "b::Box<int>"),
            ]
        )
        new = _snap(
            types=[
                self._inst("Box<int, int>", "a::Box<int, int>"),
                self._inst("Box<int>", "b::Box<int>"),
            ]
        )
        changes = detect_mandatory_template_param_added(old, new)
        assert [c.symbol for c in changes] == ["a::Box"]
