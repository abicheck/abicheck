"""Tests for abicheck.binary_fingerprint — function fingerprinting and rename detection.

All test data is synthetic — no real binaries required for the unit tests.
Integration tests that use real ELF binaries are marked @pytest.mark.integration.
"""
from __future__ import annotations

import os

import pytest

from abicheck.binary_fingerprint import (
    BinarySummary,
    FunctionFingerprint,
    SectionSummary,
    compute_function_fingerprints,
    compute_section_summary,
    match_renamed_functions,
)
from abicheck.checker import ChangeKind, compare
from abicheck.diff_symbols import (
    _ctor_dtor_variant,
    _fingerprints_from_elf,
    _param_signature_of,
    _plausible_rename,
    _return_type_of,
    _strip_template_args,
    _unqualified_name,
    _unqualified_name_of,
)
from abicheck.elf_metadata import ElfMetadata, ElfSymbol, SymbolBinding, SymbolType
from abicheck.model import AbiSnapshot, Function, Visibility

# Concrete size values for clarity (avoids importing private _MIN_SYMBOL_SIZE).
_TINY_SIZE = 4    # below minimum threshold — should never match
_NORMAL_SIZE = 100  # comfortably above threshold


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fp(name: str, size: int, code_hash: str = "") -> FunctionFingerprint:
    """Shorthand for creating a FunctionFingerprint."""
    return FunctionFingerprint(name=name, size=size, code_hash=code_hash)


def _snap_elf_only(
    version: str,
    symbols: list[ElfSymbol],
    functions: list[Function] | None = None,
) -> AbiSnapshot:
    """Create an elf_only_mode snapshot with ELF symbols."""
    if functions is None:
        functions = [
            Function(
                name=s.name, mangled=s.name, return_type="void",
                visibility=Visibility.ELF_ONLY,
            )
            for s in symbols
        ]
    return AbiSnapshot(
        library="libtest.so.1",
        version=version,
        functions=functions,
        elf=ElfMetadata(symbols=symbols),
        elf_only_mode=True,
    )


def _func_sym(name: str, size: int = _NORMAL_SIZE) -> ElfSymbol:
    """Create an exported FUNC ElfSymbol."""
    return ElfSymbol(
        name=name,
        binding=SymbolBinding.GLOBAL,
        sym_type=SymbolType.FUNC,
        size=size,
    )


# ---------------------------------------------------------------------------
# FunctionFingerprint model tests
# ---------------------------------------------------------------------------

class TestFunctionFingerprint:
    def test_frozen(self) -> None:
        fp = _fp("foo", 100, "abc")
        with pytest.raises(AttributeError):
            fp.name = "bar"  # type: ignore[misc]

    def test_equality(self) -> None:
        a = _fp("foo", 100, "abc")
        b = _fp("foo", 100, "abc")
        assert a == b

    def test_inequality_name(self) -> None:
        assert _fp("foo", 100) != _fp("bar", 100)

    def test_inequality_size(self) -> None:
        assert _fp("foo", 100) != _fp("foo", 200)


# ---------------------------------------------------------------------------
# BinarySummary tests
# ---------------------------------------------------------------------------

class TestBinarySummary:
    def test_differs_from_identical(self) -> None:
        s = BinarySummary(sections={
            ".text": SectionSummary(".text", 1000, "aaa"),
            ".rodata": SectionSummary(".rodata", 200, "bbb"),
        })
        assert s.differs_from(s) == {}

    def test_differs_from_changed(self) -> None:
        old = BinarySummary(sections={
            ".text": SectionSummary(".text", 1000, "aaa"),
            ".rodata": SectionSummary(".rodata", 200, "bbb"),
        })
        new = BinarySummary(sections={
            ".text": SectionSummary(".text", 1000, "ccc"),
            ".rodata": SectionSummary(".rodata", 200, "bbb"),
        })
        diffs = old.differs_from(new)
        assert ".text" in diffs
        assert ".rodata" not in diffs
        assert diffs[".text"] == ("aaa", "ccc")

    def test_differs_from_sections_only_in_one(self) -> None:
        """Sections only in one binary are not reported as diffs."""
        old = BinarySummary(sections={
            ".text": SectionSummary(".text", 1000, "aaa"),
        })
        new = BinarySummary(sections={
            ".text": SectionSummary(".text", 1000, "aaa"),
            ".data": SectionSummary(".data", 100, "ddd"),
        })
        assert old.differs_from(new) == {}

    def test_differs_from_bss_size_change(self) -> None:
        """Two .bss sections with same hash but different sizes are flagged."""
        old = BinarySummary(sections={
            ".bss": SectionSummary(".bss", 100, "same_hash"),
        })
        new = BinarySummary(sections={
            ".bss": SectionSummary(".bss", 200, "same_hash"),
        })
        diffs = old.differs_from(new)
        assert ".bss" in diffs

    def test_has_text_present(self) -> None:
        s = BinarySummary(sections={
            ".text": SectionSummary(".text", 42, "x"),
        })
        assert s.has_text is True

    def test_has_text_absent(self) -> None:
        assert BinarySummary().has_text is False

    def test_text_size(self) -> None:
        s = BinarySummary(sections={
            ".text": SectionSummary(".text", 42, "x"),
        })
        assert s.text_size == 42

    def test_text_size_absent(self) -> None:
        assert BinarySummary().text_size is None


# ---------------------------------------------------------------------------
# match_renamed_functions tests
# ---------------------------------------------------------------------------

class TestMatchRenamedFunctions:
    def test_no_changes(self) -> None:
        """Same symbols in both → no rename candidates."""
        fps = {"foo": _fp("foo", 100, "aaa"), "bar": _fp("bar", 200, "bbb")}
        assert match_renamed_functions(fps, fps) == []

    def test_exact_match_size_and_hash(self) -> None:
        """Identical size + hash → confidence 1.0."""
        old = {"old_func": _fp("old_func", 128, "deadbeef")}
        new = {"new_func": _fp("new_func", 128, "deadbeef")}
        result = match_renamed_functions(old, new)
        assert len(result) == 1
        assert result[0].old_name == "old_func"
        assert result[0].new_name == "new_func"
        assert result[0].confidence == 1.0

    def test_size_only_match(self) -> None:
        """Same size, no code hash → confidence 0.8."""
        old = {"old_func": _fp("old_func", 128)}
        new = {"new_func": _fp("new_func", 128)}
        result = match_renamed_functions(old, new)
        assert len(result) == 1
        assert result[0].confidence == 0.8

    def test_fuzzy_size_match(self) -> None:
        """Size within 5% tolerance, unique match → confidence 0.5."""
        old = {"old_func": _fp("old_func", _NORMAL_SIZE)}
        new = {"new_func": _fp("new_func", 104)}  # 4% difference
        result = match_renamed_functions(old, new)
        assert len(result) == 1
        assert result[0].confidence == 0.5

    def test_no_fuzzy_match_beyond_tolerance(self) -> None:
        """Size difference > 5% → no match."""
        old = {"old_func": _fp("old_func", _NORMAL_SIZE)}
        new = {"new_func": _fp("new_func", 110)}  # 10% difference
        result = match_renamed_functions(old, new)
        assert len(result) == 0

    def test_common_symbols_excluded(self) -> None:
        """Symbols present in both old and new are not candidates."""
        old = {
            "common": _fp("common", 100, "aaa"),
            "old_only": _fp("old_only", 200, "bbb"),
        }
        new = {
            "common": _fp("common", 100, "aaa"),
            "new_only": _fp("new_only", 200, "bbb"),
        }
        result = match_renamed_functions(old, new)
        assert len(result) == 1
        assert result[0].old_name == "old_only"
        assert result[0].new_name == "new_only"

    def test_small_symbols_filtered(self) -> None:
        """Symbols smaller than the minimum threshold are skipped."""
        old = {"tiny": _fp("tiny", _TINY_SIZE, "aaa")}
        new = {"renamed_tiny": _fp("renamed_tiny", _TINY_SIZE, "aaa")}
        assert match_renamed_functions(old, new) == []

    def test_zero_size_symbols_filtered(self) -> None:
        """Symbols with size=0 never participate in matching."""
        old = {"zero": _fp("zero", 0, "aaa")}
        new = {"renamed_zero": _fp("renamed_zero", 0, "aaa")}
        assert match_renamed_functions(old, new) == []

    def test_ambiguous_size_match_skipped(self) -> None:
        """Multiple candidates with same exact size → no match (ambiguous)."""
        old = {"old_func": _fp("old_func", 128)}
        new = {
            "candidate_a": _fp("candidate_a", 128),
            "candidate_b": _fp("candidate_b", 128),
        }
        result = match_renamed_functions(old, new)
        assert len(result) == 0

    def test_ambiguous_fuzzy_match_skipped(self) -> None:
        """Multiple candidates within fuzzy tolerance → no match (ambiguous)."""
        old = {"old_func": _fp("old_func", _NORMAL_SIZE)}
        new = {
            "candidate_a": _fp("candidate_a", 101),  # 1% diff
            "candidate_b": _fp("candidate_b", 103),  # 3% diff
        }
        result = match_renamed_functions(old, new)
        assert len(result) == 0

    def test_hash_mismatch_prevents_size_only_match(self) -> None:
        """Same size but different code hashes → no match at any pass."""
        old = {"old_func": _fp("old_func", 128, "aaaa")}
        new = {"new_func": _fp("new_func", 128, "bbbb")}
        result = match_renamed_functions(old, new)
        assert len(result) == 0

    def test_multiple_renames(self) -> None:
        """Multiple rename candidates matched correctly."""
        old = {
            "libfoo_v1_create": _fp("libfoo_v1_create", 256, "hash1"),
            "libfoo_v1_destroy": _fp("libfoo_v1_destroy", 128, "hash2"),
        }
        new = {
            "libfoo_create": _fp("libfoo_create", 256, "hash1"),
            "libfoo_destroy": _fp("libfoo_destroy", 128, "hash2"),
        }
        result = match_renamed_functions(old, new)
        assert len(result) == 2
        names = {(r.old_name, r.new_name) for r in result}
        assert ("libfoo_v1_create", "libfoo_create") in names
        assert ("libfoo_v1_destroy", "libfoo_destroy") in names

    def test_greedy_matching_one_to_one(self) -> None:
        """Each symbol is matched at most once (greedy 1:1).

        'a' is matched first alphabetically; 'b' has no remaining partner.
        """
        old = {
            "a": _fp("a", 100, "hash_x"),
            "b": _fp("b", 100, "hash_x"),  # same hash as 'a'
        }
        new = {
            "c": _fp("c", 100, "hash_x"),
        }
        result = match_renamed_functions(old, new)
        assert len(result) == 1
        assert result[0].old_name == "a"
        assert result[0].new_name == "c"

    def test_name_filter_participates_in_selection(self) -> None:
        """When a size bucket has one added symbol and several removed symbols,
        the name filter must steer candidate *selection*, not just discard a
        greedily-chosen pair afterward. An unrelated removed name that sorts
        first must not consume the partner a plausible rename should claim."""
        old = {
            # 'aaa_unrelated' sorts before 'foo_v1' and shares the size bucket
            "aaa_unrelated": _fp("aaa_unrelated", 256),
            "foo_v1": _fp("foo_v1", 256),
        }
        new = {"foo_v2": _fp("foo_v2", 256)}

        def plausible(o: str, n: str) -> bool:
            import difflib
            return difflib.SequenceMatcher(None, o, n).ratio() >= 0.5

        result = match_renamed_functions(old, new, name_filter=plausible)
        assert len(result) == 1
        assert result[0].old_name == "foo_v1"
        assert result[0].new_name == "foo_v2"

    def test_empty_inputs(self) -> None:
        assert match_renamed_functions({}, {}) == []
        assert match_renamed_functions({"a": _fp("a", 100)}, {}) == []
        assert match_renamed_functions({}, {"b": _fp("b", 100)}) == []

    def test_sorted_by_confidence(self) -> None:
        """Results are sorted by confidence descending with expected values."""
        old = {
            "exact_old": _fp("exact_old", 200, "hash_e"),
            "fuzzy_old": _fp("fuzzy_old", _NORMAL_SIZE),
        }
        new = {
            "exact_new": _fp("exact_new", 200, "hash_e"),
            "fuzzy_new": _fp("fuzzy_new", 104),
        }
        result = match_renamed_functions(old, new)
        assert len(result) == 2
        assert result[0].confidence == 1.0
        assert result[1].confidence == 0.5


# ---------------------------------------------------------------------------
# compute_function_fingerprints / compute_section_summary — file-level tests
# ---------------------------------------------------------------------------

class TestComputeFunctionFingerprints:
    def test_non_elf_file_returns_empty(self, tmp_path: object) -> None:
        """Non-ELF file (PE magic) returns empty dict."""
        p = os.path.join(str(tmp_path), "test.dll")
        with open(p, "wb") as f:
            f.write(b"MZ" + b"\x00" * 100)
        assert compute_function_fingerprints(p) == {}

    def test_missing_file_returns_empty(self) -> None:
        """Non-existent path returns empty dict (graceful OSError)."""
        assert compute_function_fingerprints("/nonexistent/path/libfoo.so") == {}

    def test_directory_returns_empty(self, tmp_path: object) -> None:
        """Directory is not a regular file and is rejected."""
        # open() on a directory raises IsADirectoryError → caught by OSError handler
        assert compute_function_fingerprints(str(tmp_path)) == {}

    def test_empty_file_returns_empty(self, tmp_path: object) -> None:
        """Empty file returns empty dict."""
        p = os.path.join(str(tmp_path), "empty.so")
        with open(p, "wb"):
            pass
        assert compute_function_fingerprints(p) == {}

    def test_truncated_elf_returns_empty(self, tmp_path: object) -> None:
        """File with ELF magic but truncated content returns empty dict."""
        p = os.path.join(str(tmp_path), "truncated.so")
        with open(p, "wb") as f:
            f.write(b"\x7fELF")  # just the magic, nothing else
        assert compute_function_fingerprints(p) == {}


class TestComputeSectionSummary:
    def test_non_elf_file_returns_empty(self, tmp_path: object) -> None:
        """Non-ELF file returns empty BinarySummary."""
        p = os.path.join(str(tmp_path), "test.dll")
        with open(p, "wb") as f:
            f.write(b"MZ" + b"\x00" * 100)
        result = compute_section_summary(p)
        assert result.sections == {}

    def test_missing_file_returns_empty(self) -> None:
        """Non-existent path returns empty BinarySummary."""
        result = compute_section_summary("/nonexistent/path/libfoo.so")
        assert result.sections == {}

    def test_directory_returns_empty(self, tmp_path: object) -> None:
        """Directory is not a regular file and is rejected."""
        result = compute_section_summary(str(tmp_path))
        assert result.sections == {}

    def test_empty_file_returns_empty(self, tmp_path: object) -> None:
        """Empty file returns empty BinarySummary."""
        p = os.path.join(str(tmp_path), "empty.so")
        with open(p, "wb"):
            pass
        result = compute_section_summary(p)
        assert result.sections == {}


# ---------------------------------------------------------------------------
# Unqualified-name extraction and rename plausibility
# ---------------------------------------------------------------------------

class TestUnqualifiedName:
    @pytest.mark.parametrize("symbol,expected", [
        ("add", "add"),                                  # plain C name
        ("ns::Class::method", "method"),                 # qualified
        ("ns::Class::method(int, long)", "method"),      # with params
        ("ns::foo<bar::baz>::run()", "run"),             # '::' inside template args
        ("ns::make<a::b, c::d>", "make<a::b, c::d>"),    # template args kept
        ("ns::foo<bar<int>>", "foo<bar<int>>"),          # nested template args kept
        ("void get<int>()", "get<int>"),                 # return type dropped, args kept
        ("std::ostream::operator<<(int)", "operator<<(int)"),  # operator kept whole
        ("Widget::operator()(int)", "operator()(int)"),        # call operator
        ("cooperator_v1", "cooperator_v1"),              # 'operator' substring, not keyword
        ("myoperator::foo_v1()", "foo_v1"),              # 'operator' inside qualifier
    ])
    def test_extraction(self, symbol: str, expected: str) -> None:
        assert _unqualified_name(symbol) == expected

    @pytest.mark.parametrize("leaf,expected", [
        ("get<int>", "get"),                 # simple template args
        ("foo<bar<int>>", "foo"),            # nested template args
        ("plain", "plain"),                  # no template args
        ("a>", "a>"),                        # unbalanced '>' left as-is
    ])
    def test_strip_template_args(self, leaf: str, expected: str) -> None:
        assert _strip_template_args(leaf) == expected


class TestPlausibleRename:
    def test_identical_symbol(self) -> None:
        assert _plausible_rename("foo", "foo") is True

    def test_namespace_move_same_leaf(self) -> None:
        # Different qualifier, same leaf → plausible.
        assert _plausible_rename("a::b::run()", "a::c::d::run()") is True

    def test_same_scope_different_leaf_rejected(self) -> None:
        # Shared qualifier must not inflate the score: unrelated leaves under a
        # common scope (begin/end) are not a rename.
        assert _plausible_rename(
            "std::vector<int>::begin()", "std::vector<int>::end()"
        ) is False

    def test_unrelated_rejected(self) -> None:
        assert _plausible_rename("fixupIndexV4(X)", "SmallVectorImpl<X>::erase(X*)") is False

    def test_same_scope_short_leaves_rejected(self) -> None:
        # get/set share only an incidental 2-char suffix once the qualifier is
        # stripped, below the shared-affix floor.
        assert _plausible_rename("Class::get()", "Class::set()") is False

    def test_template_specializations_rejected(self) -> None:
        # foo<int> and foo<long> are distinct ABI symbols (different mangled
        # names), so swapping one for the other is not a rename.
        assert _plausible_rename("void get<int>()", "void get<long>()") is False

    def test_unrelated_templates_same_return_rejected(self) -> None:
        # Shared return type and template args must not inflate the score.
        assert _plausible_rename("void get<int>()", "void set<int>()") is False

    def test_same_name_param_change_rejected(self) -> None:
        # foo(int) and foo(long) are distinct mangled symbols (different
        # parameters), so a same-size collision is a signature change, not a
        # rename — a consumer of foo(int) still fails to link against foo(long).
        assert _plausible_rename("foo(int)", "foo(long)") is False
        assert _plausible_rename("ns::Cls::run(int)", "ns::Cls::run(double)") is False

    def test_namespace_move_same_params_accepted(self) -> None:
        # Same function (name + parameters), different scope → a relocation.
        assert _plausible_rename("ns1::foo(int)", "ns2::foo(int)") is True

    def test_version_suffix_rename_accepted(self) -> None:
        assert _plausible_rename("libfoo_v1_create", "libfoo_create") is True

    def test_distinct_operators_rejected(self) -> None:
        # The shared 'operator' token must not count as a similarity affix.
        assert _plausible_rename("C::operator+()", "C::operator-()") is False
        assert _plausible_rename("C::operator<<(int)", "C::operator>>(int)") is False

    def test_same_operator_accepted(self) -> None:
        # Identical operator spelling is an exact-leaf match.
        assert _plausible_rename("A::operator==(int)", "B::operator==(int)") is True

    def test_undemangleable_mangled_names_rejected(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Force the no-demangler branch so the raw "_Z..." fallback is actually
        # exercised: without a demangler the leaf is the raw mangled spelling,
        # whose shared boilerplate must not be affix-scored into a false rename.
        import abicheck.demangle as demangle_mod
        monkeypatch.setattr(demangle_mod, "demangle", lambda _sym: None)
        assert _plausible_rename("_ZN1A3fooEv", "_ZN1B3barEv") is False

    def test_ctor_dtor_variant_pairs_rejected(self) -> None:
        # Itanium ctor/dtor variants demangle to the same leaf but are distinct
        # exported symbols; a size collision between them is not a rename.
        # Deterministic regardless of demangler availability (checked on the
        # raw mangled name).
        assert _plausible_rename("_ZN6WidgetC1Ev", "_ZN6WidgetC2Ev") is False
        assert _plausible_rename("_ZN6WidgetD1Ev", "_ZN6WidgetD0Ev") is False

    def test_free_function_with_ctor_like_name_not_a_ctor_variant(self) -> None:
        # A free function whose identifier merely contains 'C1E'/'C2E'
        # (_Z6fooC1Ev = fooC1E()) is NOT a constructor variant — it is a
        # non-nested (_Z, not _ZN) mangling, so the variant guard must not fire.
        # (Asserted on _ctor_dtor_variant directly so the check is independent
        # of demangler availability; a real ctor IS a nested _ZN name.)
        assert _ctor_dtor_variant("_Z6fooC1Ev") is None
        assert _ctor_dtor_variant("_Z6fooC2Ev") is None
        assert _ctor_dtor_variant("_ZN6WidgetC1Ev") == "C1"
        # A nested MEMBER named fooC1E (_ZN1A6fooC1EEv = A::fooC1E()) is also not
        # a constructor — the length-prefix parser must not be fooled by the
        # 'C1E' substring inside the source-name component.
        assert _ctor_dtor_variant("_ZN1A6fooC1EEv") is None
        assert _ctor_dtor_variant("_ZN1A6fooC2EEv") is None
        # Namespaced constructor is still detected.
        assert _ctor_dtor_variant("_ZN2ns6WidgetC1Ev") == "C1"

    def test_templated_class_ctor_variant_detected(self) -> None:
        # A templated class places its <template-args> (I…E) between the class
        # name and the ctor/dtor code; the parser must skip the balanced block.
        # _ZN3FooIiEC1Ev = Foo<int>::Foo().
        assert _ctor_dtor_variant("_ZN3FooIiEC1Ev") == "C1"
        assert _ctor_dtor_variant("_ZN3FooIiEC2Ev") == "C2"
        assert _ctor_dtor_variant("_ZN3FooIiED1Ev") == "D1"
        # Nested template args and non-type (literal) params still balance.
        assert _ctor_dtor_variant("_ZN3FooIN2ns1XEEC1Ev") == "C1"
        assert _ctor_dtor_variant("_ZN3FooILi5EEC1Ev") == "C1"
        # A class-type template argument whose identifier *contains* 'E'
        # (Foo<Err> = _ZN3FooI3ErrEC1Ev): the 'E' inside the 3-char source-name
        # 'Err' must not close the template-args block early.
        assert _ctor_dtor_variant("_ZN3FooI3ErrEC1Ev") == "C1"
        assert _ctor_dtor_variant("_ZN3FooI3ErrEC2Ev") == "C2"
        # Substitution and special-substitution template arguments balance too.
        assert _ctor_dtor_variant("_ZN3FooIS_EC1Ev") == "C1"
        assert _ctor_dtor_variant("_ZN3FooISsEC1Ev") == "C1"

    def test_std_substitution_prefix_ctor_variant_detected(self) -> None:
        # A standard-substitution abbreviation can open the prefix: St = std::,
        # so std::vector<int>::vector() = _ZNSt6vectorIiEC1Ev. The variant code
        # must still be found after consuming the substitution.
        assert _ctor_dtor_variant("_ZNSt6vectorIiEC1Ev") == "C1"
        assert _ctor_dtor_variant("_ZNSt6vectorIiEC2Ev") == "C2"
        assert _ctor_dtor_variant("_ZNSsC1Ev") == "C1"  # Ss = std::string
        # A non-ctor std:: member is still not a variant.
        assert _ctor_dtor_variant("_ZNSt6vectorIiE3fooEv") is None
        # C1 vs C2 of a std container are distinct ABI symbols, not a rename.
        assert _plausible_rename("_ZNSt6vectorIiEC1Ev", "_ZNSt6vectorIiEC2Ev") is False

    def test_abi_tag_prefix_ctor_variant_detected(self) -> None:
        # An ABI-tag component B<source-name> sits on the class name before the
        # ctor/dtor code: Foo[abi:x]::Foo() = _ZN3FooB1xC1Ev. The variant must
        # still be found after consuming the tag, so C1/C2 are not a rename.
        assert _ctor_dtor_variant("_ZN3FooB1xC1Ev") == "C1"
        assert _ctor_dtor_variant("_ZN3FooB1xC2Ev") == "C2"
        assert _plausible_rename("_ZN3FooB1xC1Ev", "_ZN3FooB1xC2Ev") is False

    def test_return_type_only_template_change_rejected(self) -> None:
        # Function templates encode the return type in the ABI symbol, so a
        # same-leaf/same-params return-type change (int foo<int>() ->
        # long foo<int>()) is a distinct symbol, not a rename. Demangled-style
        # inputs keep the test independent of c++filt availability.
        assert _plausible_rename("int foo<int>()", "long foo<int>()") is False
        assert _plausible_rename("void g<int>()", "int g<int>()") is False
        # An ordinary (non-template) rename has no return type either side, so
        # the check is a no-op and a genuine relocation still matches.
        assert _plausible_rename("ns::Widget::run()", "ns2::Widget::run()") is True

    def test_return_type_of_extraction(self) -> None:
        assert _return_type_of("int foo<int>()") == "int"
        assert _return_type_of("unsigned int g<int>()") == "unsigned int"
        assert _return_type_of("std::vector<int> bar()") == "std::vector<int>"
        assert _return_type_of("foo(int)") == ""           # ordinary function
        assert _return_type_of("ns::Class::method()") == ""
        assert _return_type_of("operator<<(int)") == ""

    def test_templated_class_ctor_variant_pair_rejected(self) -> None:
        # C1 vs C2 of the same templated class are distinct ABI symbols, not a
        # rename — the variant guard must fire even with template args present.
        assert _plausible_rename("_ZN3FooIiEC1Ev", "_ZN3FooIiEC2Ev") is False
        # Same, for a class-type argument containing an 'E' in its identifier.
        assert _plausible_rename("_ZN3FooI3ErrEC1Ev", "_ZN3FooI3ErrEC2Ev") is False

    def test_one_sided_ctor_match_rejected(self) -> None:
        # Only one side is a ctor/dtor: a removed constructor A::A()
        # (_ZN1AC1Ev) vs an added ordinary member B::A() (_ZN1B1AEv) both
        # reduce to leaf 'A()', but a constructor ABI symbol cannot be
        # satisfied by an ordinary method — reject rather than call it a rename.
        assert _plausible_rename("_ZN1AC1Ev", "_ZN1B1AEv") is False
        assert _plausible_rename("_ZN1B1AEv", "_ZN1AC1Ev") is False
        # Likewise a destructor vs an ordinary same-leaf member.
        assert _plausible_rename("_ZN1AD1Ev", "_ZN1B1AEv") is False

    def test_funcptr_return_declarator_name_extracted(self) -> None:
        # A function returning a function pointer demangles to declarator syntax
        # (int (*foo<int>())()) where the first top-level '(' opens the
        # declarator group, not the parameter list. The real name must be
        # recovered for leaf/param extraction (demangled-style inputs keep the
        # test independent of c++filt availability).
        assert _unqualified_name_of("int (*foo_v1<int>())()") == "foo_v1<int>"
        assert _param_signature_of("int (*foo_v1<int>())()") == "()"
        # A function that merely *takes* a function-pointer parameter must be
        # left intact (the '(' there is the real parameter list).
        assert _unqualified_name_of("void foo(int (*)())") == "foo"
        assert _param_signature_of("void foo(int (*)())") == "(int (*)())"

    def test_funcptr_return_rename_detected(self) -> None:
        # End-to-end: a versioned rename of a function-pointer-returning template
        # (foo_v1<int> -> foo_v2<int>) is a plausible rename, not removed/added.
        assert _plausible_rename(
            "int (*foo_v1<int>())()", "int (*foo_v2<int>())()"
        ) is True

    def test_same_variant_ctor_relocation_accepted(self) -> None:
        # A genuine constructor relocation to a new enclosing scope
        # (A::A() -> ns::A::A()) is still a plausible rename — the tightened
        # guard must not reject same-kind ctors. Demangled-style names are used
        # so the test is independent of c++filt/cxxfilt availability (raw _Z
        # names without a demangler fall to the conservative exact-only gate).
        assert _plausible_rename("A::A()", "ns::A::A()") is True

    def test_ctor_dtor_variant_malformed_symbols_yield_none(self) -> None:
        # Defensive bail-outs: a malformed nested-name must never raise or
        # mis-report; it yields None (no suppression — the safe direction).
        assert _ctor_dtor_variant("_ZN99FooC1Ev") is None     # length overruns
        assert _ctor_dtor_variant("_ZN3FooIiC1Ev") is None    # template never closed
        assert _ctor_dtor_variant("_ZN3FooI") is None         # truncated at 'I'
        assert _ctor_dtor_variant("_ZN3FooILiC1Ev") is None   # L-literal never closed
        assert _ctor_dtor_variant("_ZNK1A3fooEv") is None     # const member, not a ctor
        assert _ctor_dtor_variant("not_mangled") is None      # not an _ZN name

    def test_operator_substring_not_treated_as_operator(self) -> None:
        # Identifiers that merely contain 'operator' are ordinary names and
        # must still match on affix, not be forced to exact-only.
        assert _plausible_rename("cooperator_v1", "cooperator_v2") is True
        assert _plausible_rename("myoperator::run_v1()", "myoperator::run_v2()") is True

    def test_constructor_destructor_pair_rejected(self) -> None:
        # ctor leaf 'Widget' and dtor leaf '~Widget' share the class-name
        # affix but are different ABI functions, not a rename. (Demangled forms
        # are used so the test is independent of c++filt availability.)
        assert _plausible_rename("Widget::Widget()", "Widget::~Widget()") is False

    def test_destructor_namespace_move_accepted(self) -> None:
        # The same destructor under a different scope is still a move.
        assert _plausible_rename("ns::Widget::~Widget()", "ns2::Widget::~Widget()") is True

    def test_plain_unqualified_names(self) -> None:
        # No '::', no template, no return type, no operator.
        assert _plausible_rename("process_request", "process_reply") is True
        assert _plausible_rename("alpha", "omega") is False

    def test_prefix_of_other_accepted(self) -> None:
        # One leaf is a full prefix of the other (shared run spans the shorter).
        assert _plausible_rename("init", "initialize") is True


# ---------------------------------------------------------------------------
# Detector integration tests (using compare())
# ---------------------------------------------------------------------------

class TestFingerprintRenameDetector:
    """Test the fingerprint_renames detector via the full compare() pipeline."""

    def test_fingerprints_from_elf_handles_missing_metadata(self) -> None:
        snap = AbiSnapshot(
            library="libtest.so.1",
            version="1.0",
            elf=None,
            elf_only_mode=True,
        )

        assert _fingerprints_from_elf(snap) == {}

    def test_fingerprints_from_elf_skips_non_function_symbols(self) -> None:
        snap = _snap_elf_only("1.0", [
            ElfSymbol(name="global_table", sym_type=SymbolType.OBJECT, size=_NORMAL_SIZE),
            _func_sym("public_func", _NORMAL_SIZE),
        ])

        assert set(_fingerprints_from_elf(snap)) == {"public_func"}

    def test_likely_renamed_detected_in_elf_only_mode(self) -> None:
        """Renamed function with same size is detected as FUNC_LIKELY_RENAMED."""
        old = _snap_elf_only("1.0", [_func_sym("libfoo_v1_create", 256)])
        new = _snap_elf_only("2.0", [_func_sym("libfoo_create", 256)])
        result = compare(old, new)

        rename_changes = [c for c in result.changes if c.kind == ChangeKind.FUNC_LIKELY_RENAMED]
        assert len(rename_changes) == 1
        assert rename_changes[0].old_value == "libfoo_v1_create"
        assert rename_changes[0].new_value == "libfoo_create"

    def test_not_triggered_without_elf_only_mode(self) -> None:
        """Detector is gated behind elf_only_mode — disabled for header-based analysis.

        Also verifies that FUNC_REMOVED/FUNC_ADDED are still reported by the
        regular diff pipeline while the fingerprint detector is disabled.
        """
        old = AbiSnapshot(
            library="libtest.so.1", version="1.0",
            functions=[Function(name="old_func", mangled="old_func",
                                return_type="void", visibility=Visibility.PUBLIC)],
            elf=ElfMetadata(symbols=[_func_sym("old_func", 256)]),
            elf_only_mode=False,
        )
        new = AbiSnapshot(
            library="libtest.so.1", version="2.0",
            functions=[Function(name="new_func", mangled="new_func",
                                return_type="void", visibility=Visibility.PUBLIC)],
            elf=ElfMetadata(symbols=[_func_sym("new_func", 256)]),
            elf_only_mode=False,
        )
        result = compare(old, new)
        rename_changes = [c for c in result.changes if c.kind == ChangeKind.FUNC_LIKELY_RENAMED]
        assert len(rename_changes) == 0
        # Regular diff still fires
        kinds = {c.kind for c in result.changes}
        assert ChangeKind.FUNC_REMOVED in kinds
        assert ChangeKind.FUNC_ADDED in kinds

    def test_not_triggered_without_elf_metadata(self) -> None:
        """Detector requires ELF metadata — disabled for PE/Mach-O."""
        old = AbiSnapshot(
            library="libtest.so.1", version="1.0",
            functions=[Function(name="old_func", mangled="old_func",
                                return_type="void", visibility=Visibility.ELF_ONLY)],
            elf_only_mode=True,
        )
        new = AbiSnapshot(
            library="libtest.so.1", version="2.0",
            functions=[Function(name="new_func", mangled="new_func",
                                return_type="void", visibility=Visibility.ELF_ONLY)],
            elf_only_mode=True,
        )
        result = compare(old, new)
        rename_changes = [c for c in result.changes if c.kind == ChangeKind.FUNC_LIKELY_RENAMED]
        assert len(rename_changes) == 0

    def test_small_symbols_not_matched(self) -> None:
        """Tiny functions (stubs) should not produce rename matches."""
        old = _snap_elf_only("1.0", [_func_sym("stub_old", _TINY_SIZE)])
        new = _snap_elf_only("2.0", [_func_sym("stub_new", _TINY_SIZE)])
        result = compare(old, new)
        rename_changes = [c for c in result.changes if c.kind == ChangeKind.FUNC_LIKELY_RENAMED]
        assert len(rename_changes) == 0

    def test_different_sizes_not_matched(self) -> None:
        """Functions with significantly different sizes should not match."""
        old = _snap_elf_only("1.0", [_func_sym("func_old", _NORMAL_SIZE)])
        new = _snap_elf_only("2.0", [_func_sym("func_new", 200)])
        result = compare(old, new)
        rename_changes = [c for c in result.changes if c.kind == ChangeKind.FUNC_LIKELY_RENAMED]
        assert len(rename_changes) == 0

    def test_multiple_renames_detected(self) -> None:
        """Multiple renames in a single comparison are all detected."""
        old_syms = [_func_sym("v1_init", 256), _func_sym("v1_cleanup", 128)]
        new_syms = [_func_sym("v2_init", 256), _func_sym("v2_cleanup", 128)]
        old = _snap_elf_only("1.0", old_syms)
        new = _snap_elf_only("2.0", new_syms)
        result = compare(old, new)

        rename_changes = [c for c in result.changes if c.kind == ChangeKind.FUNC_LIKELY_RENAMED]
        assert len(rename_changes) == 2
        rename_pairs = {(c.old_value, c.new_value) for c in rename_changes}
        assert ("v1_init", "v2_init") in rename_pairs
        assert ("v1_cleanup", "v2_cleanup") in rename_pairs

    def test_unchanged_functions_not_affected(self) -> None:
        """Functions present in both versions are not reported as renames."""
        shared_sym = _func_sym("shared_func", 300)
        old = _snap_elf_only("1.0", [shared_sym, _func_sym("old_only", 128)])
        new = _snap_elf_only("2.0", [shared_sym, _func_sym("new_only", 128)])
        result = compare(old, new)

        rename_changes = [c for c in result.changes if c.kind == ChangeKind.FUNC_LIKELY_RENAMED]
        assert len(rename_changes) == 1
        assert rename_changes[0].old_value == "old_only"
        assert rename_changes[0].new_value == "new_only"

    def test_retained_wrapper_not_reported_as_rename(self) -> None:
        """A retained ABI symbol that shrinks to a wrapper is not a rename.

        Real libssh2 1.11.0 -> 1.11.1 keeps libssh2_session_callback_set as a
        tiny compatibility wrapper and adds libssh2_session_callback_set2 with
        the old implementation size. The old symbol must not be reported as a
        loader-breaking rename because existing binaries can still resolve it.
        """
        old = _snap_elf_only("1.0", [
            _func_sym("libssh2_session_callback_set", 185),
        ])
        new = _snap_elf_only("2.0", [
            _func_sym("libssh2_session_callback_set", _TINY_SIZE),
            _func_sym("libssh2_session_callback_set2", 185),
        ])
        result = compare(old, new)

        rename_changes = [c for c in result.changes if c.kind == ChangeKind.FUNC_LIKELY_RENAMED]
        assert rename_changes == []
        kinds = {c.kind for c in result.changes}
        assert ChangeKind.FUNC_ADDED in kinds

    def test_retained_export_not_used_as_rename_target(self) -> None:
        """An unchanged exported function cannot be consumed as a rename target."""
        old = _snap_elf_only("1.0", [
            _func_sym("foo_v1", 128),
            _func_sym("foo_v2", 128),
        ])
        new = _snap_elf_only("2.0", [
            _func_sym("foo_v1", 128),
        ])
        result = compare(old, new)

        rename_changes = [c for c in result.changes if c.kind == ChangeKind.FUNC_LIKELY_RENAMED]
        assert rename_changes == []

    def test_elf_linker_artifact_not_reported_as_rename(self) -> None:
        """Filtered linker artifacts must not participate in fingerprint renames."""
        old = _snap_elf_only("1.0", [
            _func_sym("_init", 128),
        ])
        new = _snap_elf_only("2.0", [
            _func_sym("lib_init", 128),
        ])
        result = compare(old, new)

        rename_changes = [c for c in result.changes if c.kind == ChangeKind.FUNC_LIKELY_RENAMED]
        assert rename_changes == []

    def test_unrelated_names_same_size_not_renamed(self) -> None:
        """Two unrelated functions that merely share a byte size must NOT be
        reported as a rename when no code hash is available.

        Regression for false renames observed on real libLLVM diffs, where
        size-only matching paired completely unrelated mangled symbols (e.g.
        ``fixupIndexV4`` -> ``SmallVectorImpl<...>``) purely because they hit a
        unique size bucket. Without code-identity evidence, dissimilar names are
        a coincidence, not a rename."""
        old = _snap_elf_only("1.0", [
            _func_sym("_Z12fixupIndexV4RKN4llvm11DWARFObjectE", 256),
        ])
        new = _snap_elf_only("2.0", [
            _func_sym("_ZN4llvm15SmallVectorImplINS_11CompileUnitEE5eraseEPS2_", 256),
        ])
        result = compare(old, new)
        rename_changes = [c for c in result.changes if c.kind == ChangeKind.FUNC_LIKELY_RENAMED]
        assert rename_changes == []

    def test_collision_does_not_hide_plausible_rename(self) -> None:
        """A real rename in a crowded size bucket is still found even when an
        unrelated same-size symbol sorts earlier — the similarity check drives
        selection, so the unrelated symbol cannot consume the partner."""
        old = _snap_elf_only("1.0", [
            _func_sym("aaa_unrelated_function", 256),
            _func_sym("foo_v1_dosomething", 256),
        ])
        new = _snap_elf_only("2.0", [
            _func_sym("foo_v2_dosomething", 256),
        ])
        result = compare(old, new)
        renames = [c for c in result.changes if c.kind == ChangeKind.FUNC_LIKELY_RENAMED]
        assert len(renames) == 1
        assert renames[0].old_value == "foo_v1_dosomething"
        assert renames[0].new_value == "foo_v2_dosomething"

    def test_namespace_relocation_detected(self) -> None:
        """A genuine namespace move keeps the unqualified base name, so a
        hash-less size match is still reported as a rename. Uses already-
        demangled spellings so the test is independent of c++filt/cxxfilt
        availability (without a demangler, raw _Z names are treated
        conservatively and a real move can't be inferred — by design)."""
        old = _snap_elf_only("1.0", [
            _func_sym("llvm::CompileUnit::markEverythingAsKept()", 256),
        ])
        new = _snap_elf_only("2.0", [
            _func_sym("llvm::dwarf_linker::classic::CompileUnit::markEverythingAsKept()", 256),
        ])
        result = compare(old, new)
        rename_changes = [c for c in result.changes if c.kind == ChangeKind.FUNC_LIKELY_RENAMED]
        assert len(rename_changes) == 1

    def test_fuzzy_match_appears_in_compare_output(self) -> None:
        """A fuzzy size match (within 5%) makes it through the full pipeline."""
        old = _snap_elf_only("1.0", [_func_sym("old_func", _NORMAL_SIZE)])
        new = _snap_elf_only("2.0", [_func_sym("new_func", 104)])  # 4% diff
        result = compare(old, new)
        rename_changes = [c for c in result.changes if c.kind == ChangeKind.FUNC_LIKELY_RENAMED]
        assert len(rename_changes) == 1
        assert "50%" in rename_changes[0].description

    def test_fires_when_only_new_is_elf_only(self) -> None:
        """Detector fires when only the *new* snapshot is elf_only_mode."""
        old = AbiSnapshot(
            library="libtest.so.1", version="1.0",
            functions=[Function(name="old_func", mangled="old_func",
                                return_type="void", visibility=Visibility.ELF_ONLY)],
            elf=ElfMetadata(symbols=[_func_sym("old_func", 256)]),
            elf_only_mode=False,
        )
        new = _snap_elf_only("2.0", [_func_sym("new_func", 256)])
        result = compare(old, new)
        rename_changes = [c for c in result.changes if c.kind == ChangeKind.FUNC_LIKELY_RENAMED]
        assert len(rename_changes) == 1

    def test_notype_symbols_included(self) -> None:
        """NOTYPE symbols (assembly-heavy or stripped) participate in rename matching."""
        notype_old = ElfSymbol(
            name="asm_func_old", binding=SymbolBinding.GLOBAL,
            sym_type=SymbolType.NOTYPE, size=256,
        )
        notype_new = ElfSymbol(
            name="asm_func_new", binding=SymbolBinding.GLOBAL,
            sym_type=SymbolType.NOTYPE, size=256,
        )
        old = _snap_elf_only("1.0", [notype_old])
        new = _snap_elf_only("2.0", [notype_new])
        result = compare(old, new)
        rename_changes = [c for c in result.changes if c.kind == ChangeKind.FUNC_LIKELY_RENAMED]
        assert len(rename_changes) == 1
        assert rename_changes[0].old_value == "asm_func_old"
        assert rename_changes[0].new_value == "asm_func_new"

    def test_rename_suppresses_removed_and_added(self) -> None:
        """When a rename is detected, the paired FUNC_REMOVED and FUNC_ADDED are suppressed."""
        old = _snap_elf_only("1.0", [_func_sym("libfoo_v1_create", 256)])
        new = _snap_elf_only("2.0", [_func_sym("libfoo_create", 256)])
        result = compare(old, new)

        kept_kinds = {c.kind for c in result.changes}
        # Rename should be in kept changes
        assert ChangeKind.FUNC_LIKELY_RENAMED in kept_kinds
        # FUNC_REMOVED and FUNC_ADDED should be suppressed (moved to redundant)
        assert ChangeKind.FUNC_REMOVED not in kept_kinds
        assert ChangeKind.FUNC_REMOVED_ELF_ONLY not in kept_kinds
        assert ChangeKind.FUNC_ADDED not in kept_kinds
        # The suppressed changes should appear in redundant_changes
        redundant_kinds = {c.kind for c in result.redundant_changes}
        assert ChangeKind.FUNC_REMOVED_ELF_ONLY in redundant_kinds or ChangeKind.FUNC_REMOVED in redundant_kinds

    def test_pass1_ambiguous_exact_match_skipped(self) -> None:
        """Pass 1: multiple new symbols with same hash+size → no 1.0 confidence match."""
        old = {"old_func": _fp("old_func", 128, "same_hash")}
        new = {
            "new_a": _fp("new_a", 128, "same_hash"),
            "new_b": _fp("new_b", 128, "same_hash"),
        }
        result = match_renamed_functions(old, new)
        # No exact match due to ambiguity; may fall through to pass 2 or 3
        exact = [r for r in result if r.confidence == 1.0]
        assert len(exact) == 0
