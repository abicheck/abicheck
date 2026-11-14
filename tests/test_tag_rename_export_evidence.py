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
