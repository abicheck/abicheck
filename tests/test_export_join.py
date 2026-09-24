"""The observed ``exports`` join (evidence-entity-model Phase 2, I2).

Every expectation below is stated by hand from the fixture -- never
recomputed through ``model/export_index.py``'s projections or the join's own
helpers -- so the oracle cannot share a bug with the implementation.
"""

from __future__ import annotations

from abicheck.elf_metadata import ElfMetadata, ElfSymbol, SymbolType
from abicheck.macho_metadata import MachoExport, MachoMetadata
from abicheck.model import AbiSnapshot, Function, Param, Variable, Visibility
from abicheck.model.graph_evidence_class import EdgeEvidenceClass
from abicheck.model.graph_join import JOIN_SPECS, JoinState
from abicheck.pe_metadata import PeExport, PeMetadata


def _join(snap):
    from abicheck.compare.export_join import join_exports

    return join_exports(snap)


def _fn(name, mangled, *params):
    return Function(
        name=name,
        mangled=mangled,
        return_type="int",
        params=[Param(name=f"a{i}", type=t) for i, t in enumerate(params)],
        visibility=Visibility.PUBLIC,
    )


def _snap(functions=(), variables=(), *, elf=None, pe=None, macho=None):
    return AbiSnapshot(
        library="libx.so",
        version="1",
        functions=list(functions),
        variables=list(variables),
        elf=elf,
        pe=pe,
        macho=macho,
    )


def _elf(*names, non_default=()):
    syms = [ElfSymbol(name=n) for n in names]
    syms += [ElfSymbol(name=n, version="V1", is_default=False) for n in non_default]
    return ElfMetadata(symbols=syms)


def _state(join, node):
    return join.declaration(node).state


class TestExportJoinStates:
    def test_exported_cpp_function_joins_its_declaration(self):
        j = _join(_snap([_fn("ns::run", "_ZN2ns3runEv")], elf=_elf("_ZN2ns3runEv")))
        rec = j.declaration("decl://_ZN2ns3runEv")
        assert rec.state is JoinState.MATCHED
        assert rec.candidates == ("binary_symbol://elf/_ZN2ns3runEv",)
        exp = j.export("elf", "_ZN2ns3runEv")
        assert exp.state is JoinState.MATCHED
        assert exp.candidates == ("decl://_ZN2ns3runEv",)
        assert j.complete

    def test_extern_c_export_joins(self):
        j = _join(_snap([_fn("cfunc", "cfunc")], elf=_elf("cfunc")))
        assert _state(j, "decl://cfunc") is JoinState.MATCHED
        assert j.export("elf", "cfunc").candidates == ("decl://cfunc",)

    def test_versioned_symbol_default_and_compat_versions_join_once(self):
        # foo@@V2 (default) and foo@V1 (compat) are one symbol name, so one
        # export entry, reachable by an unversioned link.
        elf = ElfMetadata(
            symbols=[
                ElfSymbol(name="foo", version="V2", is_default=True),
                ElfSymbol(name="foo", version="V1", is_default=False),
            ]
        )
        j = _join(_snap([_fn("foo", "foo")], elf=elf))
        assert _state(j, "decl://foo") is JoinState.MATCHED
        (entry,) = j.exports_of("decl://foo")
        assert (entry.platform, entry.spelling, entry.default_version) == (
            "elf",
            "foo",
            True,
        )

    def test_compat_only_version_still_joins_but_is_not_default(self):
        # Only foo@V1 exists: still an observed export (an old consumer binds
        # to it), but not one an unversioned link can use.
        j = _join(_snap([_fn("foo", "foo")], elf=_elf(non_default=("foo",))))
        assert _state(j, "decl://foo") is JoinState.MATCHED
        (entry,) = j.exports_of("decl://foo")
        assert entry.default_version is False

    def test_public_inline_declaration_without_export_is_unmatched(self):
        j = _join(
            _snap(
                [_fn("ns::inl", "_ZN2ns3inlEv"), _fn("ns::run", "_ZN2ns3runEv")],
                elf=_elf("_ZN2ns3runEv"),
            )
        )
        rec = j.declaration("decl://_ZN2ns3inlEv")
        assert rec.state is JoinState.UNMATCHED
        assert rec.reason == "no_export"
        assert rec.candidates == ()

    def test_export_without_declaration_is_unmatched(self):
        j = _join(_snap([_fn("run", "run")], elf=_elf("run", "_ZN4impl3useEv")))
        rec = j.export("elf", "_ZN4impl3useEv")
        assert rec.state is JoinState.UNMATCHED
        assert rec.reason == "no_declaration"

    def test_two_declarations_competing_for_one_export_are_ambiguous(self):
        # A declaration recorded with no linker name (spelled by its plain
        # name) and one recorded with that linker name are two identities;
        # the table cannot say which of them the export is.
        j = _join(_snap([_fn("foo", ""), _fn("foo", "foo", "int")], elf=_elf("foo")))
        exp = j.export("elf", "foo")
        assert exp.state is JoinState.AMBIGUOUS
        assert len(exp.candidates) == 2
        assert "decl://foo" in exp.candidates
        other = next(c for c in exp.candidates if c != "decl://foo")
        assert other.startswith("unresolved://")
        for node in exp.candidates:
            assert j.declaration(node).reason == "export_contested"

    def test_macho_decorated_itanium_name_joins_through_phase1_alias(self):
        # clang keeps Darwin's underscore (`__Z...`); the trie parser strips
        # it (`_Z...`). Phase 1 records `decl://__Z3fooi` as an alias of the
        # canonical `decl://_Z3fooi`.
        j = _join(
            _snap(
                [_fn("foo", "__Z3fooi", "int")],
                macho=MachoMetadata(exports=[MachoExport(name="_Z3fooi")]),
            )
        )
        rec = j.declaration("decl://_Z3fooi")
        assert rec.state is JoinState.MATCHED
        assert rec.candidates == ("binary_symbol://macho/_Z3fooi",)

    def test_macho_shift_refused_when_another_declaration_owns_the_spelling(self):
        j = _join(
            _snap(
                [_fn("foo", "foo"), _fn("_foo", "_foo")],
                macho=MachoMetadata(exports=[MachoExport(name="_foo")]),
            )
        )
        assert j.export("macho", "_foo").candidates == ("decl://_foo",)
        assert _state(j, "decl://foo") is JoinState.UNMATCHED

    def test_macho_shift_never_applies_to_elf(self):
        j = _join(_snap([_fn("foo", "foo")], elf=_elf("_foo")))
        assert _state(j, "decl://foo") is JoinState.UNMATCHED
        assert j.export("elf", "_foo").state is JoinState.UNMATCHED

    def test_pe_msvc_decorated_name_joins_exactly(self):
        j = _join(
            _snap(
                [_fn("ns::run", "?run@ns@@YAHXZ")],
                pe=PeMetadata(exports=[PeExport(name="?run@ns@@YAHXZ", ordinal=1)]),
            )
        )
        assert _state(j, "decl://?run@ns@@YAHXZ") is JoinState.MATCHED

    def test_pe_x86_stdcall_decoration_has_no_alias_record(self):
        # `_foo@8` is not a spelling any declaration records, and no alias
        # record proves it names `foo`: no join without evidence.
        j = _join(
            _snap(
                [_fn("foo", "foo")],
                pe=PeMetadata(exports=[PeExport(name="_foo@8", ordinal=1)]),
            )
        )
        assert _state(j, "decl://foo") is JoinState.UNMATCHED
        assert j.export("pe", "_foo@8").state is JoinState.UNMATCHED

    def test_pe_ordinal_only_export_is_an_observed_entry(self):
        j = _join(
            _snap(
                [_fn("f", "f")],
                pe=PeMetadata(
                    exports=[
                        PeExport(name="f", ordinal=1),
                        PeExport(name="", ordinal=7),
                    ]
                ),
            )
        )
        rec = j.export("pe", "ordinal:7")
        assert rec.state is JoinState.UNMATCHED
        assert j.entries[rec.subject].default_version is False

    def test_same_symbol_in_two_tables_is_one_entity_not_ambiguous(self):
        j = _join(
            _snap(
                [_fn("foo", "foo")],
                elf=_elf("foo"),
                macho=MachoMetadata(exports=[MachoExport(name="foo")]),
            )
        )
        rec = j.declaration("decl://foo")
        assert rec.state is JoinState.MATCHED
        assert len(rec.candidates) == 2

    def test_variable_joins(self):
        var = Variable(name="g", mangled="g", type="int", visibility=Visibility.PUBLIC)
        elf = ElfMetadata(symbols=[ElfSymbol(name="g", sym_type=SymbolType.OBJECT)])
        j = _join(_snap(variables=[var], elf=elf))
        assert _state(j, "decl://g") is JoinState.MATCHED

    def test_linker_name_alone_is_not_an_export(self):
        # I2: a declaration's own mangled name proves nothing. With a table
        # that does not list it, the join is unmatched.
        j = _join(_snap([_fn("run", "_Z3runv")], elf=_elf("other")))
        assert _state(j, "decl://_Z3runv") is JoinState.UNMATCHED


class TestExportJoinIncompleteEvidence:
    def test_no_export_table_is_unknown_not_unmatched(self):
        j = _join(_snap([_fn("run", "_Z3runv")]))
        assert not j.complete
        assert j.join.incomplete_reason == "no_export_table"
        assert _state(j, "decl://_Z3runv") is JoinState.UNKNOWN
        assert j.join.right == {}

    def test_empty_but_captured_table_is_complete(self):
        # model/export_index.py's structural rule: a parsed table with no
        # entries is "confirmed empty", not "missing".
        j = _join(_snap([_fn("run", "_Z3runv")], elf=ElfMetadata(symbols=[])))
        assert j.complete
        assert _state(j, "decl://_Z3runv") is JoinState.UNMATCHED


class TestExportJoinSpec:
    def test_exports_edge_is_a_resolved_join_with_producer_inputs_and_rule(self):
        spec = JOIN_SPECS["exports"]
        assert spec.evidence_class is EdgeEvidenceClass.RESOLVED_JOIN
        assert spec.producer == "compare.export_join.join_exports"
        assert spec.inputs and spec.recompute_rule

    def test_edges_run_from_export_to_declaration(self):
        j = _join(_snap([_fn("cfunc", "cfunc")], elf=_elf("cfunc", "extra")))
        assert list(j.join.edges()) == [("binary_symbol://elf/cfunc", "decl://cfunc")]


class TestExportJoinAgreesWithBinaryExportedFact:
    """The join and ``binary_exported_fact`` answer the same question from the
    same table; a header-AST producer's recorded fact must never contradict
    the join (model/surface_facts.py)."""

    def test_matched_declarations_are_not_recorded_unexported(self):
        from abicheck.model.fact import Fact

        exported = _fn("run", "_Z3runv")
        exported.binary_exported_fact = Fact.present(True, producer="castxml")
        inline = _fn("inl", "_Z3inlv")
        inline.binary_exported_fact = Fact.present(False, producer="castxml")
        j = _join(_snap([exported, inline], elf=_elf("_Z3runv")))
        assert _state(j, "decl://_Z3runv") is JoinState.MATCHED
        assert _state(j, "decl://_Z3inlv") is JoinState.UNMATCHED
