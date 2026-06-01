# Copyright 2026 Nikolay Petrov
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""Unit tests for the library-family C++ pattern detectors (case77–case89).

Each detector is exercised with synthetic ``AbiSnapshot`` objects, so the
suite runs without a C/C++ compiler, libabigail, abi-compliance-checker,
or castxml — these are part of the default fast test suite.

Cases covered:

* case79: ``detect_missing_instantiations``
* case81: ``detect_serialization_tag_changes``
* case82: ``detect_sycl_overload_set_removal``
* case83: ``detect_cpu_dispatch_isa_dropped``
* case84: ``detect_bundle_soname_skew``
* case86: ``detect_tag_type_renamed``
* case87: ``detect_default_template_arg_changed``
* case89: ``detect_inline_body_renamed_member``

case77 and case80 reuse the existing internal-leak detector and are
covered by ``tests/test_internal_leak.py`` plus the autodiscovery
integration test.
"""

from __future__ import annotations

import pytest

from abicheck.checker_policy import ChangeKind
from abicheck.checker_types import Change
from abicheck.diff_cpp_patterns import (
    BundleMember,
    _extract_soname_major,
    _has_sycl_queue_first_param,
    _is_empty_record,
    _isa_token_in_symbol,
    _looks_like_serialization_tag,
    _looks_like_template_instantiation,
    detect_bundle_soname_skew,
    detect_cpu_dispatch_isa_dropped,
    detect_default_template_arg_changed,
    detect_inline_body_renamed_member,
    detect_missing_instantiations,
    detect_serialization_tag_changes,
    detect_sycl_overload_set_removal,
    detect_tag_type_renamed,
)
from abicheck.model import (
    AbiSnapshot,
    Function,
    Param,
    ParamKind,
    RecordType,
    TypeField,
    Variable,
)

# ---------------------------------------------------------------------------
# Helpers — synthetic snapshot construction
# ---------------------------------------------------------------------------


def _fn(
    name: str,
    mangled: str | None = None,
    *,
    params: list[Param] | None = None,
    is_inline: bool = False,
    return_type: str = "void",
) -> Function:
    return Function(
        name=name,
        mangled=mangled or f"_Z{len(name)}{name.replace('::', '')}v",
        return_type=return_type,
        params=params or [],
        is_inline=is_inline,
    )


def _snap(
    name: str,
    functions: list[Function] | None = None,
    types: list[RecordType] | None = None,
    variables: list[Variable] | None = None,
    constants: dict[str, str] | None = None,
) -> AbiSnapshot:
    return AbiSnapshot(
        library=name,
        version="1.0",
        functions=functions or [],
        types=types or [],
        variables=variables or [],
        constants=constants or {},
    )


# ===========================================================================
# Shared helpers
# ===========================================================================


class TestIsEmptyRecord:
    def test_empty_one_byte_class(self) -> None:
        rt = RecordType(name="ns::tag", kind="struct", size_bits=8, fields=[])
        assert _is_empty_record(rt) is True

    def test_unknown_size_no_fields(self) -> None:
        rt = RecordType(name="ns::tag", kind="struct", size_bits=None, fields=[])
        assert _is_empty_record(rt) is True

    def test_class_with_field_not_empty(self) -> None:
        rt = RecordType(
            name="ns::has_field",
            kind="struct",
            size_bits=32,
            fields=[TypeField(name="x", type="int")],
        )
        assert _is_empty_record(rt) is False

    def test_class_with_vtable_not_empty(self) -> None:
        rt = RecordType(
            name="ns::polymorphic",
            kind="class",
            size_bits=64,
            vtable=["_ZN12polymorphic1fEv"],
        )
        assert _is_empty_record(rt) is False


# ===========================================================================
# case81 — serialization tag changes
# ===========================================================================


class TestSerializationTagNaming:
    @pytest.mark.parametrize(
        "name",
        [
            "kmeans_model_tag",
            "kmeans_serialization_tag",
            "KMeansSerializationTag",
            "mylib::knn_model_tag",
            "ns::detail::tag_id",
        ],
    )
    def test_recognised(self, name: str) -> None:
        assert _looks_like_serialization_tag(name) is True

    @pytest.mark.parametrize(
        "name",
        [
            "kmeans_model",
            "compute",
            "tag_input",  # leaf doesn't end in tag suffix
        ],
    )
    def test_not_recognised(self, name: str) -> None:
        assert _looks_like_serialization_tag(name) is False


class TestSerializationTagDetector:
    def test_swap_emits_finding_per_tag(self) -> None:
        old = _snap(
            "lib",
            constants={
                "kmeans_model_tag": "0x1001",
                "knn_model_tag": "0x1002",
                "linear_regression_tag": "0x1003",
            },
        )
        new = _snap(
            "lib",
            constants={
                "kmeans_model_tag": "0x1001",
                "knn_model_tag": "0x1003",  # swapped
                "linear_regression_tag": "0x1002",  # swapped
            },
        )
        findings = detect_serialization_tag_changes(old, new)
        assert len(findings) == 2
        assert all(f.kind == ChangeKind.SERIALIZATION_TAG_CHANGED for f in findings)
        # The swap-partner must be named in the description.
        descs = " ".join(f.description for f in findings)
        assert "linear_regression_tag" in descs
        assert "knn_model_tag" in descs

    def test_single_value_change_emits_one_finding(self) -> None:
        old = _snap("lib", constants={"foo_tag": "0x10"})
        new = _snap("lib", constants={"foo_tag": "0x99"})
        findings = detect_serialization_tag_changes(old, new)
        assert len(findings) == 1
        assert findings[0].old_value == "0x10"
        assert findings[0].new_value == "0x99"

    def test_no_change_no_finding(self) -> None:
        old = _snap("lib", constants={"foo_tag": "0x10"})
        new = _snap("lib", constants={"foo_tag": "0x10"})
        assert detect_serialization_tag_changes(old, new) == []

    def test_non_tag_constants_ignored(self) -> None:
        old = _snap("lib", constants={"max_threads": "16"})
        new = _snap("lib", constants={"max_threads": "32"})
        assert detect_serialization_tag_changes(old, new) == []

    def test_picks_up_enum_class_tag_values(self) -> None:
        """Enum-class-based tag IDs (the case81 fixture pattern) are
        the most portable data source — DWARF always captures
        ``DW_AT_const_value`` for enumerators.
        """
        from abicheck.model import EnumMember, EnumType

        old_enum = EnumType(
            name="mylib::SerializationTag",
            underlying_type="uint64_t",
            members=[
                EnumMember(name="kmeans_model", value=0x1001),
                EnumMember(name="knn_model", value=0x1002),
                EnumMember(name="linear_regression", value=0x1003),
            ],
        )
        new_enum = EnumType(
            name="mylib::SerializationTag",
            underlying_type="uint64_t",
            members=[
                EnumMember(name="kmeans_model", value=0x1001),
                EnumMember(name="knn_model", value=0x1003),  # swapped
                EnumMember(name="linear_regression", value=0x1002),  # swapped
            ],
        )
        old = AbiSnapshot(library="lib", version="1.0", enums=[old_enum])
        new = AbiSnapshot(library="lib", version="2.0", enums=[new_enum])
        findings = detect_serialization_tag_changes(old, new)
        # One finding per shifted member (kmeans_model is unchanged).
        kinds = [f.kind for f in findings]
        assert kinds.count(ChangeKind.SERIALIZATION_TAG_CHANGED) == 2

    def test_source_precedence_constants_over_variables(self) -> None:
        """CodeRabbit regression: ``constants`` is the highest-confidence
        source. A ``variables`` entry with the same name must NOT
        overwrite a ``constants`` value (otherwise a lower-confidence
        sibling value can manufacture a spurious
        ``SERIALIZATION_TAG_CHANGED``).
        """
        # Both snapshots agree on the constant; variables disagree.
        # Because constants take precedence, the detector must see them
        # as equal and emit nothing.
        old = _snap(
            "lib",
            constants={"foo_tag": "0x42"},
            variables=[
                Variable(
                    name="foo_tag",
                    mangled="foo_tag",
                    type="int",
                    value="100",
                ),
            ],
        )
        new = _snap(
            "lib",
            constants={"foo_tag": "0x42"},
            variables=[
                Variable(
                    name="foo_tag",
                    mangled="foo_tag",
                    type="int",
                    value="999",
                ),
            ],
        )
        assert detect_serialization_tag_changes(old, new) == []

    def test_picks_up_variable_initial_values(self) -> None:
        v1 = Variable(name="model_tag", mangled="model_tag", type="int", value="100")
        v2 = Variable(name="model_tag", mangled="model_tag", type="int", value="200")
        old = _snap("lib", variables=[v1])
        new = _snap("lib", variables=[v2])
        findings = detect_serialization_tag_changes(old, new)
        assert len(findings) == 1


# ===========================================================================
# case79 — missing template instantiation
# ===========================================================================


class TestTemplateInstantiationHeuristic:
    @pytest.mark.parametrize(
        "name",
        [
            "descriptor<float>",
            "ns::descriptor<float, int>",
            "ns::descriptor<float>::method",
        ],
    )
    def test_recognised(self, name: str) -> None:
        assert _looks_like_template_instantiation(name) is True

    @pytest.mark.parametrize(
        "name",
        [
            "compute",
            "ns::compute",
            "operator<",  # operator< has '<' but no closing '>'
        ],
    )
    def test_not_recognised(self, name: str) -> None:
        assert _looks_like_template_instantiation(name) is False


class TestMissingInstantiationDetector:
    def test_one_instantiation_dropped_emits_finding(self) -> None:
        old = _snap(
            "lib",
            functions=[
                _fn("descriptor<float>::descriptor", "_ZN10descriptorIfEC1Ev"),
                _fn("descriptor<double>::descriptor", "_ZN10descriptorIdEC1Ev"),
            ],
        )
        new = _snap(
            "lib",
            functions=[
                _fn("descriptor<float>::descriptor", "_ZN10descriptorIfEC1Ev"),
            ],
        )
        findings = detect_missing_instantiations(old, new)
        assert len(findings) == 1
        assert findings[0].kind == ChangeKind.INSTANTIATION_MISSING_FROM_BINARY
        assert "descriptor<double>" in findings[0].description

    def test_whole_template_removed_not_flagged_here(self) -> None:
        # If no descriptor<*> survives, this is plain func_removed, not
        # an "advertised but missing" situation.
        old = _snap(
            "lib",
            functions=[
                _fn("descriptor<float>::descriptor", "_ZN10descriptorIfEC1Ev"),
            ],
        )
        new = _snap("lib", functions=[])
        assert detect_missing_instantiations(old, new) == []

    def test_sibling_template_member_removal_not_flagged(self) -> None:
        """Codex P2 regression: removing ``descriptor<float>::train`` while
        ``descriptor<float>::infer`` survives must NOT be flagged as a
        missing instantiation. The stem must include the callable identity
        (``descriptor::train``), not just the class name (``descriptor``)."""
        old = _snap(
            "lib",
            functions=[
                _fn(
                    "ns::descriptor<float>::train",
                    "_ZN2ns10descriptorIfE5trainEv",
                ),
                _fn(
                    "ns::descriptor<float>::infer",
                    "_ZN2ns10descriptorIfE5inferEv",
                ),
            ],
        )
        new = _snap(
            "lib",
            functions=[
                _fn(
                    "ns::descriptor<float>::infer",
                    "_ZN2ns10descriptorIfE5inferEv",
                ),
            ],
        )
        assert detect_missing_instantiations(old, new) == []

    def test_non_template_removal_ignored(self) -> None:
        old = _snap(
            "lib",
            functions=[
                _fn("compute", "_Z7computev"),
                _fn("descriptor<float>::descriptor", "_ZN10descriptorIfEC1Ev"),
            ],
        )
        new = _snap(
            "lib",
            functions=[
                _fn("descriptor<float>::descriptor", "_ZN10descriptorIfEC1Ev"),
            ],
        )
        assert detect_missing_instantiations(old, new) == []


# ===========================================================================
# case82 — SYCL overload set removed
# ===========================================================================


class TestSyclFirstParam:
    def test_recognises_sycl_queue_ref(self) -> None:
        fn = _fn(
            "compute",
            params=[
                Param(name="q", type="sycl::queue&", kind=ParamKind.REFERENCE),
            ],
        )
        assert _has_sycl_queue_first_param(fn) is True

    def test_recognises_sycl_queue_with_spaces(self) -> None:
        fn = _fn(
            "compute",
            params=[
                Param(name="q", type="sycl :: queue&", kind=ParamKind.REFERENCE),
            ],
        )
        assert _has_sycl_queue_first_param(fn) is True

    def test_non_sycl_first_param(self) -> None:
        fn = _fn("compute", params=[Param(name="x", type="int")])
        assert _has_sycl_queue_first_param(fn) is False

    def test_no_params(self) -> None:
        assert _has_sycl_queue_first_param(_fn("noargs")) is False


class TestSyclOverloadSetDetector:
    def _build_pair(self) -> tuple[AbiSnapshot, AbiSnapshot]:
        sycl_param = Param(
            name="q",
            type="sycl::queue&",
            kind=ParamKind.REFERENCE,
        )
        old = _snap(
            "lib",
            functions=[
                _fn("compute", "_Z7computev"),
                _fn("compute", "_Z7computeRN4sycl5queueE", params=[sycl_param]),
                _fn("train", "_Z5trainv"),
                _fn("train", "_Z5trainRN4sycl5queueE", params=[sycl_param]),
                _fn("infer", "_Z5inferv"),
                _fn("infer", "_Z5inferRN4sycl5queueE", params=[sycl_param]),
            ],
        )
        new = _snap(
            "lib",
            functions=[
                _fn("compute", "_Z7computev"),
                _fn("train", "_Z5trainv"),
                _fn("infer", "_Z5inferv"),
            ],
        )
        return old, new

    def test_grouped_finding_emitted(self) -> None:
        old, new = self._build_pair()
        findings, suppressed = detect_sycl_overload_set_removal(old, new)
        assert len(findings) == 1
        f = findings[0]
        assert f.kind == ChangeKind.SYCL_OVERLOAD_SET_REMOVED
        assert "3 overloads" in f.description
        for name in ("compute", "train", "infer"):
            assert name in f.description
        # All three SYCL-overload mangled names must be in the suppression
        # set. The set may additionally contain the demangled function
        # names (``compute`` / ``train`` / ``infer``) as a portability
        # fallback for platforms whose ``Change.symbol`` is the demangled
        # name rather than the mangled name.
        assert "_Z7computeRN4sycl5queueE" in suppressed
        assert "_Z5trainRN4sycl5queueE" in suppressed
        assert "_Z5inferRN4sycl5queueE" in suppressed

    def test_cross_namespace_compute_not_cross_matched(self) -> None:
        """CodeRabbit regression: SYCL family matching must use the
        QUALIFIED callable stem, not just the leaf name. A SYCL overload
        ``ns1::foo::compute(sycl::queue&)`` must not be paired against a
        surviving non-SYCL ``ns2::bar::compute()`` from a different
        namespace — they're unrelated callables that happen to share a
        leaf method name.
        """
        sycl_param = Param(
            name="q",
            type="sycl::queue&",
            kind=ParamKind.REFERENCE,
        )
        # v1: SYCL overload on ns1::foo + non-SYCL siblings on ns2::bar.
        # v2: only the ns2::bar non-SYCL siblings remain. The ns1::foo
        # SYCL overload is removed but its non-SYCL counterpart is also
        # gone (no longer a "family withdrawn" case for ns1::foo).
        old = _snap(
            "lib",
            functions=[
                _fn(
                    "ns1::foo::compute",
                    "_Z3ns1foocomputeRN4sycl5queueE",
                    params=[sycl_param],
                ),
                _fn("ns2::bar::compute", "_Z3ns2barcomputev"),
                _fn("ns2::bar::train", "_Z3ns2bartrainv"),
                _fn("ns2::bar::infer", "_Z3ns2barinferv"),
            ],
        )
        new = _snap(
            "lib",
            functions=[
                _fn("ns2::bar::compute", "_Z3ns2barcomputev"),
                _fn("ns2::bar::train", "_Z3ns2bartrainv"),
                _fn("ns2::bar::infer", "_Z3ns2barinferv"),
            ],
        )
        findings, suppressed = detect_sycl_overload_set_removal(old, new)
        # No grouped finding: the removed SYCL function's qualified
        # entity is ``ns1::foo::compute``, which has no surviving
        # non-SYCL sibling. ``ns2::bar::compute`` is unrelated.
        assert findings == []
        assert suppressed == set()

    def test_threshold_respected(self) -> None:
        old, new = self._build_pair()
        # Bump threshold above the available count → no grouping.
        findings, suppressed = detect_sycl_overload_set_removal(
            old,
            new,
            min_overloads=10,
        )
        assert findings == []
        assert suppressed == set()

    def test_no_surviving_sibling_no_finding(self) -> None:
        # If the *non*-SYCL overload also disappeared, this is a pure
        # API removal, not a build-mode change.
        sycl_param = Param(
            name="q",
            type="sycl::queue&",
            kind=ParamKind.REFERENCE,
        )
        old = _snap(
            "lib",
            functions=[
                _fn("compute", "_Z7computev"),
                _fn("compute", "_Z7computeQ", params=[sycl_param]),
                _fn("train", "_Z5trainv"),
                _fn("train", "_Z5trainQ", params=[sycl_param]),
            ],
        )
        new = _snap("lib", functions=[])
        findings, _ = detect_sycl_overload_set_removal(old, new)
        assert findings == []


# ===========================================================================
# case83 — CPU dispatch ISA dropped
# ===========================================================================


class TestIsaTokenization:
    @pytest.mark.parametrize(
        "symbol,expected",
        [
            ("kmeans_compute_avx512", "avx512"),
            ("kmeans_compute_avx2", "avx2"),
            ("kmeans_compute_sse42", "sse42"),
            ("kmeans_compute_sse2", "sse2"),
            ("kmeans_compute_scalar", "scalar"),
            ("kmeans_compute_AVX512_inner", "avx512"),
            ("kmeans_compute", None),
            ("", None),
        ],
    )
    def test_token_extracted(self, symbol: str, expected: str | None) -> None:
        assert _isa_token_in_symbol(symbol) == expected

    def test_avx512_beats_avx(self) -> None:
        # ``avx512`` must be matched in preference to ``avx``.
        assert _isa_token_in_symbol("foo_avx512_bar") == "avx512"


class TestCpuDispatchIsaDetector:
    def _build_three_algorithms(self) -> tuple[AbiSnapshot, AbiSnapshot]:
        algos = ("kmeans", "knn", "linreg")
        old = _snap(
            "lib",
            functions=[
                _fn(f"{a}_compute_{isa}", f"_Z{len(a)}{a}_compute_{isa}")
                for a in algos
                for isa in ("avx512", "avx2", "sse42", "scalar")
            ],
        )
        new = _snap(
            "lib",
            functions=[
                _fn(f"{a}_compute_{isa}", f"_Z{len(a)}{a}_compute_{isa}")
                for a in algos
                for isa in ("avx2", "sse42", "scalar")
            ],
        )
        return old, new

    def test_grouped_finding_for_isa_drop(self) -> None:
        old, new = self._build_three_algorithms()
        findings, suppressed = detect_cpu_dispatch_isa_dropped(old, new)
        assert len(findings) == 1
        f = findings[0]
        assert f.kind == ChangeKind.CPU_DISPATCH_ISA_DROPPED
        assert "avx512" in f.description
        # The three AVX-512 mangled names are present in the suppressed
        # set; the set may also contain the demangled function names
        # (added as a portability fallback for platforms whose
        # ``Change.symbol`` uses a different mangling than ``fn.mangled``).
        for algo in ("kmeans", "knn", "linreg"):
            assert any(f"{algo}_compute_avx512" in s for s in suppressed)

    def test_below_threshold_no_finding(self) -> None:
        old, new = self._build_three_algorithms()
        findings, _ = detect_cpu_dispatch_isa_dropped(
            old,
            new,
            min_removed=10,
        )
        assert findings == []

    def test_no_overlap_with_survivors_no_finding(self) -> None:
        # If the algorithms went away entirely (no surviving stems),
        # this is plain func_removed, not an ISA-tier drop.
        old = _snap(
            "lib",
            functions=[
                _fn("alg_avx512", "_Z10alg_avx512v"),
                _fn("beta_avx512", "_Z11beta_avx512v"),
                _fn("gamma_avx512", "_Z12gamma_avx512v"),
            ],
        )
        new = _snap("lib", functions=[])
        findings, _ = detect_cpu_dispatch_isa_dropped(old, new)
        assert findings == []

    def test_fully_removed_algo_not_suppressed(self) -> None:
        """CodeRabbit Major regression: when ONE ISA tier is dropped
        across surviving algorithms AND another algorithm is fully
        removed (all ISAs gone), the fully-removed algorithm's
        ``func_removed`` findings must remain visible — only the
        symbols whose algorithm stem still survives under another
        ISA should be suppressed under the grouped finding.
        """
        # kmeans + knn + linreg lose only AVX-512; ``gamma`` is deleted entirely.
        algos_kept = ("kmeans", "knn", "linreg")
        old = _snap(
            "lib",
            functions=[
                _fn(f"{a}_compute_{isa}", f"_Z{len(a) + 8}{a}_compute_{isa}")
                for a in algos_kept
                for isa in ("avx512", "avx2", "sse42")
            ]
            + [
                _fn("gamma_compute_avx512", "_Z20gamma_compute_avx512v"),
                _fn("gamma_compute_avx2", "_Z18gamma_compute_avx2v"),
            ],
        )
        new = _snap(
            "lib",
            functions=[
                _fn(f"{a}_compute_{isa}", f"_Z{len(a) + 8}{a}_compute_{isa}")
                for a in algos_kept
                for isa in ("avx2", "sse42")
            ],
        )
        findings, suppressed = detect_cpu_dispatch_isa_dropped(old, new)
        # gamma_compute_avx512 must NOT be suppressed — its stem
        # ``gamma_compute`` has no surviving sibling.
        assert "_Z20gamma_compute_avx512v" not in suppressed
        # The three "real" ISA-drop symbols ARE suppressed.
        for a in algos_kept:
            mangled = f"_Z{len(a) + 8}{a}_compute_avx512"
            assert mangled in suppressed
        # And the grouped finding still fires.
        assert len(findings) == 1


# ===========================================================================
# case86 — tag struct renamed
# ===========================================================================


class TestTagTypeRenamedDetector:
    def test_rename_with_symbol_evidence(self) -> None:
        old_tag = RecordType(
            name="mylib::method::brute_force",
            kind="struct",
            size_bits=8,
            fields=[],
        )
        new_tag = RecordType(
            name="mylib::method::search_brute",
            kind="struct",
            size_bits=8,
            fields=[],
        )
        old = _snap(
            "lib",
            functions=[
                _fn(
                    "mylib::descriptor<mylib::method::brute_force>::descriptor",
                    "_ZN5mylib10descriptorINS_6method11brute_forceEEC1Ev",
                ),
            ],
            types=[old_tag],
        )
        new = _snap(
            "lib",
            functions=[
                _fn(
                    "mylib::descriptor<mylib::method::search_brute>::descriptor",
                    "_ZN5mylib10descriptorINS_6method12search_bruteEEC1Ev",
                ),
            ],
            types=[new_tag],
        )
        findings = detect_tag_type_renamed(old, new)
        assert len(findings) == 1
        f = findings[0]
        assert f.kind == ChangeKind.TAG_TYPE_RENAMED
        assert f.old_value == "mylib::method::brute_force"
        assert f.new_value == "mylib::method::search_brute"
        assert f.affected_symbols and len(f.affected_symbols) == 1

    def test_no_symbol_evidence_no_finding(self) -> None:
        old_tag = RecordType(name="a::tagA", kind="struct", size_bits=8)
        new_tag = RecordType(name="a::tagB", kind="struct", size_bits=8)
        old = _snap("lib", functions=[_fn("unrelated")], types=[old_tag])
        new = _snap("lib", functions=[_fn("unrelated")], types=[new_tag])
        assert detect_tag_type_renamed(old, new) == []

    def test_different_parent_namespace_no_match(self) -> None:
        old_tag = RecordType(name="a::tagA", kind="struct", size_bits=8)
        new_tag = RecordType(name="b::tagB", kind="struct", size_bits=8)
        old = _snap(
            "lib", functions=[_fn("a_tagA_inst", "_Za_tagA_inst")], types=[old_tag]
        )
        new = _snap(
            "lib", functions=[_fn("b_tagB_inst", "_Zb_tagB_inst")], types=[new_tag]
        )
        assert detect_tag_type_renamed(old, new) == []


# ===========================================================================
# case87 — default template arg changed
# ===========================================================================


class TestDefaultTemplateArgDetector:
    def test_pair_with_differing_template_args(self) -> None:
        old = _snap(
            "lib",
            functions=[
                _fn(
                    "ns::descriptor<float, ns::minkowski_distance<float>>::compute",
                    "_ZN2ns10descriptorIfNS_18minkowski_distanceIfEEE7computeEv",
                ),
            ],
        )
        new = _snap(
            "lib",
            functions=[
                _fn(
                    "ns::descriptor<float, ns::euclidean_distance<float>>::compute",
                    "_ZN2ns10descriptorIfNS_18euclidean_distanceIfEEE7computeEv",
                ),
            ],
        )
        findings = detect_default_template_arg_changed(old, new)
        assert len(findings) == 1
        f = findings[0]
        assert f.kind == ChangeKind.DEFAULT_TEMPLATE_ARG_CHANGED

    def test_no_change_no_finding(self) -> None:
        old = _snap(
            "lib",
            functions=[
                _fn("ns::descriptor<float, ns::A>::compute", "_Zold"),
            ],
        )
        new = _snap(
            "lib",
            functions=[
                _fn("ns::descriptor<float, ns::A>::compute", "_Zold"),
            ],
        )
        assert detect_default_template_arg_changed(old, new) == []

    def test_different_unqualified_names_not_paired(self) -> None:
        old = _snap(
            "lib",
            functions=[
                _fn("ns::descriptor<float, A>::train", "_Zoldtrain"),
            ],
        )
        new = _snap(
            "lib",
            functions=[
                _fn("ns::descriptor<float, B>::infer", "_Znewinfer"),
            ],
        )
        assert detect_default_template_arg_changed(old, new) == []

    def test_cross_namespace_same_method_not_paired(self) -> None:
        """Codex P2 / CodeRabbit Major regression: an unrelated removal
        in ``ns1::foo<int>::compute`` and addition in
        ``ns2::bar<float>::compute`` must NOT be paired just because
        they share the unqualified ``compute`` method name.
        """
        old = _snap(
            "lib",
            functions=[_fn("ns1::foo<int>::compute", "_Zns1foo")],
        )
        new = _snap(
            "lib",
            functions=[_fn("ns2::bar<float>::compute", "_Zns2bar")],
        )
        assert detect_default_template_arg_changed(old, new) == []

    def test_leading_arg_change_not_flagged(self) -> None:
        """CodeRabbit Major regression: when EVERY template-arg position
        differs (i.e. there is no stable leading prefix), the change is
        an explicit instantiation swap, not a default-template-arg
        change. ``DEFAULT_TEMPLATE_ARG_CHANGED`` must not fire.
        """
        old = _snap(
            "lib",
            functions=[
                _fn(
                    "ns::descriptor<float, ns::A>::compute",
                    "_ZN2ns10descriptorIfNS_1AEE7computeEv",
                ),
            ],
        )
        new = _snap(
            "lib",
            functions=[
                _fn(
                    "ns::descriptor<double, ns::B>::compute",
                    "_ZN2ns10descriptorIdNS_1BEE7computeEv",
                ),
            ],
        )
        # Both args change (float→double AND A→B) → not a default change.
        assert detect_default_template_arg_changed(old, new) == []

    def test_trailing_arg_change_still_flagged(self) -> None:
        """Positive check for the leading-prefix gate: when only the
        TRAILING arg changes (leading prefix identical), the finding
        still fires — this is the canonical case87 pattern.
        """
        old = _snap(
            "lib",
            functions=[
                _fn(
                    "ns::descriptor<float, ns::A>::compute",
                    "_ZN2ns10descriptorIfNS_1AEE7computeEv",
                ),
            ],
        )
        new = _snap(
            "lib",
            functions=[
                _fn(
                    "ns::descriptor<float, ns::B>::compute",
                    "_ZN2ns10descriptorIfNS_1BEE7computeEv",
                ),
            ],
        )
        findings = detect_default_template_arg_changed(old, new)
        assert len(findings) == 1
        assert findings[0].kind == ChangeKind.DEFAULT_TEMPLATE_ARG_CHANGED


# ===========================================================================
# case89 — inline accessor references renamed pimpl member
# ===========================================================================


class TestInlineAccessorRenamedMember:
    def _build(self) -> tuple[AbiSnapshot, AbiSnapshot, list[Change]]:
        old_impl = RecordType(
            name="mylib::detail::descriptor_impl",
            kind="class",
            size_bits=64,
            fields=[TypeField(name="class_count_", type="int")],
        )
        new_impl = RecordType(
            name="mylib::detail::descriptor_impl",
            kind="class",
            size_bits=64,
            fields=[TypeField(name="n_classes_", type="int")],
        )
        # Public class holds the pimpl.
        public_rt = RecordType(
            name="mylib::descriptor",
            kind="class",
            size_bits=128,
            fields=[
                TypeField(
                    name="impl_",
                    type="std::shared_ptr<mylib::detail::descriptor_impl>",
                )
            ],
        )
        inline_getter = _fn(
            "mylib::descriptor::get_class_count",
            "_ZNK5mylib10descriptor15get_class_countEv",
            is_inline=True,
        )
        old = _snap("lib", functions=[inline_getter], types=[old_impl, public_rt])
        new = _snap("lib", functions=[inline_getter], types=[new_impl, public_rt])
        # The diff would normally produce removed + added field
        # changes; synthesise them directly.
        changes = [
            Change(
                kind=ChangeKind.TYPE_FIELD_REMOVED,
                symbol="mylib::detail::descriptor_impl::class_count_",
                description="",
                old_value="class_count_",
            ),
            Change(
                kind=ChangeKind.TYPE_FIELD_ADDED,
                symbol="mylib::detail::descriptor_impl::n_classes_",
                description="",
                new_value="n_classes_",
            ),
        ]
        return old, new, changes

    def test_pimpl_rename_with_inline_accessor_detected(self) -> None:
        old, new, changes = self._build()
        findings = detect_inline_body_renamed_member(old, new, changes)
        assert len(findings) >= 1
        f = findings[0]
        assert f.kind == ChangeKind.INLINE_BODY_REFERENCES_RENAMED_MEMBER
        assert f.old_value == "class_count_"
        assert f.new_value == "n_classes_"

    def test_no_inline_accessor_no_finding(self) -> None:
        old, new, changes = self._build()
        # Strip inline flag from the function in both snapshots.
        for snap in (old, new):
            for fn in snap.functions:
                fn.is_inline = False
        assert detect_inline_body_renamed_member(old, new, changes) == []

    def test_internal_namespace_required(self) -> None:
        # Rename on a non-detail:: type — accessor is fine because the
        # public layout is intentionally part of the contract.
        public_impl_old = RecordType(
            name="mylib::config",
            kind="class",
            size_bits=32,
            fields=[TypeField(name="a", type="int")],
        )
        public_impl_new = RecordType(
            name="mylib::config",
            kind="class",
            size_bits=32,
            fields=[TypeField(name="b", type="int")],
        )
        old = _snap("lib", types=[public_impl_old])
        new = _snap("lib", types=[public_impl_new])
        changes = [
            Change(
                kind=ChangeKind.TYPE_FIELD_REMOVED,
                symbol="mylib::config::a",
                description="",
            ),
            Change(
                kind=ChangeKind.TYPE_FIELD_ADDED,
                symbol="mylib::config::b",
                description="",
            ),
        ]
        assert detect_inline_body_renamed_member(old, new, changes) == []


# ===========================================================================
# case84 — bundle SONAME skew
# ===========================================================================


class TestSonameExtraction:
    @pytest.mark.parametrize(
        "soname,expected",
        [
            ("libonedal_core.so.2", 2),
            ("libfoo.so.10", 10),
            ("libfoo.2.dylib", 2),
            ("libfoo-3.dll", 3),
            ("libfoo.so", None),
            ("", None),
        ],
    )
    def test_extract(self, soname: str, expected: int | None) -> None:
        assert _extract_soname_major(soname) == expected


class TestBundleSonameSkewDetector:
    def test_skew_detected(self) -> None:
        old = [
            BundleMember("libonedal_core.so.1", "libonedal_core.so.1", 1),
            BundleMember("libonedal_thread.so.1", "libonedal_thread.so.1", 1),
            BundleMember("libonedal_dpc.so.1", "libonedal_dpc.so.1", 1),
        ]
        new = [
            BundleMember("libonedal_core.so.2", "libonedal_core.so.2", 2),
            BundleMember("libonedal_thread.so.1", "libonedal_thread.so.1", 1),  # lagged
            BundleMember("libonedal_dpc.so.2", "libonedal_dpc.so.2", 2),
        ]
        findings = detect_bundle_soname_skew(old, new)
        assert len(findings) == 1
        f = findings[0]
        assert f.kind == ChangeKind.BUNDLE_SONAME_SKEW
        assert "libonedal_thread.so.1" in (
            (f.affected_symbols and " ".join(f.affected_symbols)) or ""
        )

    def test_lockstep_bump_no_finding(self) -> None:
        old = [
            BundleMember("libfoo.so.1", "libfoo.so.1", 1),
            BundleMember("libbar.so.1", "libbar.so.1", 1),
        ]
        new = [
            BundleMember("libfoo.so.2", "libfoo.so.2", 2),
            BundleMember("libbar.so.2", "libbar.so.2", 2),
        ]
        assert detect_bundle_soname_skew(old, new) == []

    def test_no_bump_no_finding(self) -> None:
        old = [
            BundleMember("libfoo.so.1", "libfoo.so.1", 1),
            BundleMember("libbar.so.1", "libbar.so.1", 1),
        ]
        new = [
            BundleMember("libfoo.so.1", "libfoo.so.1", 1),
            BundleMember("libbar.so.1", "libbar.so.1", 1),
        ]
        assert detect_bundle_soname_skew(old, new) == []

    def test_cohort_prefix_filter(self) -> None:
        old = [
            BundleMember("libonedal_core.so.1", "libonedal_core.so.1", 1),
            BundleMember("libonedal_dpc.so.1", "libonedal_dpc.so.1", 1),
            BundleMember("libstdc++.so.6", "libstdc++.so.6", 6),
        ]
        new = [
            BundleMember("libonedal_core.so.2", "libonedal_core.so.2", 2),
            BundleMember("libonedal_dpc.so.1", "libonedal_dpc.so.1", 1),
            BundleMember("libstdc++.so.6", "libstdc++.so.6", 6),
        ]
        findings = detect_bundle_soname_skew(old, new, cohort_prefix="libonedal_")
        assert len(findings) == 1
        # libstdc++ excluded from the cohort.


# ===========================================================================
# End-to-end pipeline integration
# ===========================================================================


class TestPipelineIntegration:
    """The DetectCppPatterns step must run as part of DEFAULT_PIPELINE
    and append new findings into the change list.
    """

    def test_pipeline_includes_step(self) -> None:
        from abicheck.post_processing import DEFAULT_PIPELINE

        assert "detect_cpp_patterns" in DEFAULT_PIPELINE.step_names

    def test_serialization_tag_finding_flows_through_pipeline(self) -> None:
        from abicheck.post_processing import DEFAULT_PIPELINE

        old = _snap("lib", constants={"foo_tag": "0x1"})
        new = _snap("lib", constants={"foo_tag": "0x2"})
        ctx = DEFAULT_PIPELINE.run([], old, new)
        kinds = [c.kind for c in ctx.kept]
        assert ChangeKind.SERIALIZATION_TAG_CHANGED in kinds


class TestMatchesSuppressionKey:
    """CodeRabbit regression: the substring fallback in the
    ``DetectCppPatterns`` suppression step must not over-fire on
    short generic leaf names.
    """

    def test_exact_match_always_suppresses(self) -> None:
        from abicheck.post_processing import _matches_suppression_key

        assert _matches_suppression_key("compute", "compute") is True

    def test_short_leaf_does_not_substring_match(self) -> None:
        """``compute`` (7 chars, no ``::``/``_``) must NOT suppress
        unrelated symbols that happen to contain it as a substring.
        """
        from abicheck.post_processing import _matches_suppression_key

        # ``compute`` IS a substring of ``Recompute_xyz`` — exactly the
        # false-positive case CodeRabbit flagged.
        assert _matches_suppression_key("Recompute_xyz", "compute") is False
        assert _matches_suppression_key("precompute", "compute") is False

    def test_structured_key_substring_matches(self) -> None:
        from abicheck.post_processing import _matches_suppression_key

        # Qualified key with ``::`` — safe.
        assert _matches_suppression_key(
            "?mylib::compute@xyz", "mylib::compute",
        ) is True
        # Key with ``_`` — safe.
        assert _matches_suppression_key(
            "?kmeans_compute_avx512@mylib@@YAHH@Z",
            "kmeans_compute_avx512",
        ) is True
        # Key ≥ 12 chars even without delimiters — safe.
        assert _matches_suppression_key(
            "long_haystack_with_longidentifier_inside",
            "longidentifier",
        ) is True

    def test_empty_key_never_matches(self) -> None:
        from abicheck.post_processing import _matches_suppression_key

        assert _matches_suppression_key("anything", "") is False
