"""``detect_tag_type_renamed`` reads its re-mangling evidence from the export
table as well as the function model, and never from synthetic keys.

An explicit instantiation over the tag (``kfam<ktag_a>()``) is an export with
no declaration when the template is declared generically, so the gcc-plugin
and clang header backends model no function for it. Each row is an
independent Itanium spelling of an instantiation over the tag.
"""

from __future__ import annotations

import pytest

from abicheck.checker_policy import ChangeKind
from abicheck.diff_cpp_patterns import detect_tag_type_renamed
from abicheck.elf_metadata import ElfMetadata, ElfSymbol, SymbolBinding, SymbolType
from abicheck.model import AbiSnapshot, Function, RecordType, Visibility
from abicheck.model.macho_facts import MachoExport, MachoMetadata
from abicheck.model.pe_facts import PeExport, PeMetadata


def _fn(name: str, mangled: str) -> Function:
    return Function(
        name=name, mangled=mangled, return_type="void", visibility=Visibility.PUBLIC
    )


def _snap(functions: list[Function], types: list[RecordType]) -> AbiSnapshot:
    return AbiSnapshot(library="lib", version="1.0", functions=functions, types=types)


class TestTagTypeRenamedExportEvidence:
    """The instantiation over the tag is an export with no declaration (the
    template is declared generically), so a header backend that models no
    such function still has the export table as evidence. Each row is an
    independent Itanium spelling of an instantiation over the tag."""

    @staticmethod
    def _elf_snap(tag: str, exports: list[str]) -> AbiSnapshot:

        snap = _snap(
            [_fn("keep", "_Z4keepv")],
            [RecordType(name=tag, kind="struct", size_bits=8, fields=[])],
        )
        snap.elf = ElfMetadata(
            soname="lib.so.1",
            symbols=[
                ElfSymbol(
                    name=n,
                    binding=SymbolBinding.GLOBAL,
                    sym_type=SymbolType.FUNC,
                    size=16,
                )
                for n in ["_Z4keepv", *exports]
            ],
        )
        return snap

    @pytest.mark.parametrize(
        ("old_tag", "new_tag", "old_sym", "new_sym"),
        [
            # template<class T> int kfam();  kfam<ktag_a>() -> kfam<ktag_b>()
            ("ktag_a", "ktag_b", "_Z4kfamI6ktag_aEiv", "_Z4kfamI6ktag_bEiv"),
            # template<class T> void run(int);  run<fast_t>(int) -> run<quick_t>(int)
            ("fast_t", "quick_t", "_Z3runI6fast_tEvi", "_Z3runI7quick_tEvi"),
        ],
    )
    def test_export_only_instantiation_is_evidence(
        self, old_tag: str, new_tag: str, old_sym: str, new_sym: str
    ) -> None:
        old = self._elf_snap(old_tag, [old_sym])
        new = self._elf_snap(new_tag, [new_sym])
        (finding,) = detect_tag_type_renamed(old, new)
        assert finding.kind == ChangeKind.TAG_TYPE_RENAMED
        assert (finding.old_value, finding.new_value) == (old_tag, new_tag)
        assert finding.affected_symbols == [old_sym]

    def test_synthetic_special_member_keys_are_not_evidence(self) -> None:
        """castxml's implicit-member placeholders re-key with the tag name for
        any two empty structs; no linker symbol moved, so no rename."""

        def side(tag: str) -> AbiSnapshot:
            return _snap(
                [
                    _fn(f"{tag}", f"__abicheck_ctor__{tag}()"),
                    _fn(f"~{tag}", f"~{tag}"),
                ],
                [RecordType(name=tag, kind="struct", size_bits=8, fields=[])],
            )

        assert detect_tag_type_renamed(side("tagA"), side("tagB")) == []


class TestTagRenameEvidenceIsStructural:
    """Only a mangling that encodes a reference to the tag witnesses its
    rename; a C export or a function merely named with the tag as a prefix
    does not, whatever the export table's format."""

    @staticmethod
    def _side(tag: str, exports: list[str], fmt: str) -> AbiSnapshot:
        snap = _snap([], [RecordType(name=tag, kind="struct", size_bits=8, fields=[])])
        if fmt == "elf":
            snap.elf = ElfMetadata(
                soname="lib.so.1",
                symbols=[
                    ElfSymbol(
                        name=n, binding=SymbolBinding.GLOBAL, sym_type=SymbolType.FUNC
                    )
                    for n in exports
                ],
            )
        elif fmt == "macho":
            snap.macho = MachoMetadata(exports=[MachoExport(name=n) for n in exports])
        else:
            snap.pe = PeMetadata(
                exports=[PeExport(name=n, ordinal=i + 1) for i, n in enumerate(exports)]
            )
        return snap

    @pytest.mark.parametrize(
        ("fmt", "old_exports", "new_exports"),
        [
            # extern "C" int ktag_a_count; -> ktag_b_count
            ("elf", ["ktag_a_count"], ["ktag_b_count"]),
            # int ktag_a_count(); as C++ (named after, not over, the tag)
            ("elf", ["_Z12ktag_a_countv"], ["_Z12ktag_b_countv"]),
            # a function named exactly like the tag
            ("elf", ["_Z6ktag_av"], ["_Z6ktag_bv"]),
            ("macho", ["ktag_a_count"], ["ktag_b_count"]),
            ("pe", ["ktag_a_count"], ["ktag_b_count"]),
            ("pe", ["?ktag_a_count@@3HA"], ["?ktag_b_count@@3HA"]),
        ],
    )
    def test_name_prefixed_exports_are_not_evidence(
        self, fmt: str, old_exports: list[str], new_exports: list[str]
    ) -> None:
        old = self._side("ktag_a", old_exports, fmt)
        new = self._side("ktag_b", new_exports, fmt)
        assert detect_tag_type_renamed(old, new) == []

    @pytest.mark.parametrize(
        ("fmt", "old_sym", "new_sym"),
        [
            ("macho", "_Z4kfamI6ktag_aEiv", "_Z4kfamI6ktag_bEiv"),
            ("macho", "__Z4kfamI6ktag_aEiv", "__Z4kfamI6ktag_bEiv"),
            # MSVC: template<class T> int kfam(); kfam<ktag_a>()
            ("pe", "??$kfam@Uktag_a@@@@YAHXZ", "??$kfam@Uktag_b@@@@YAHXZ"),
            # parameter context: void use(ktag_a)
            ("elf", "_Z3use6ktag_a", "_Z3use6ktag_b"),
            ("pe", "?use@@YAXUktag_a@@@Z", "?use@@YAXUktag_b@@@Z"),
        ],
    )
    def test_type_referencing_exports_are_evidence(
        self, fmt: str, old_sym: str, new_sym: str
    ) -> None:
        old = self._side("ktag_a", [old_sym], fmt)
        new = self._side("ktag_b", [new_sym], fmt)
        (finding,) = detect_tag_type_renamed(old, new)
        assert finding.affected_symbols == [old_sym]
