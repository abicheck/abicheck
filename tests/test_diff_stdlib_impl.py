# Copyright 2026 Nikolay Petrov
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Unit tests for the cross-implementation standard-library diff (D-stdlib)."""

from __future__ import annotations

from abicheck.build_mode import BuildMode, StdlibFamily
from abicheck.checker import compare
from abicheck.checker_policy import RISK_KINDS, ChangeKind
from abicheck.model import (
    AbiSnapshot,
    Function,
    RecordType,
    TypeField,
    Visibility,
    stdlib_namespaces_excluded,
)


def _snap(
    version: str,
    *,
    stdlib: StdlibFamily | None = None,
    libcpp_abi: int | None = None,
    types: list[RecordType] | None = None,
    build_mode: BuildMode | None | str = "auto",
) -> AbiSnapshot:
    """Build a minimal snapshot with an optional build-mode capture."""
    if build_mode == "auto":
        build_mode = (
            None
            if stdlib is None and libcpp_abi is None
            else BuildMode(
                stdlib=stdlib or StdlibFamily.UNKNOWN,
                libcpp_abi_version=libcpp_abi,
            )
        )
    return AbiSnapshot(
        library="libwidget.so.1",
        version=version,
        types=types or [],
        build_mode=build_mode,  # type: ignore[arg-type]
    )


def _embed_stdlib_record(size_bits: int | None = 192) -> RecordType:
    """A public class holding a std::vector by value (the canonical trap)."""
    return RecordType(
        name="Buffer",
        kind="class",
        size_bits=size_bits,
        fields=[TypeField(name="data", type="std::vector<int>", offset_bits=0)],
    )


# ---------------------------------------------------------------------------
# stdlib_namespaces_excluded — the global std:: filter must stay ON even for a
# cross-implementation comparison (Codex review on PR #345): standalone std::
# records differ wholesale between libstdc++/libc++, so un-filtering them
# globally would flood BREAKING noise for toolchain-owned internals. The real
# break is caught via the (non-std::) owner type; the hazard is surfaced as a
# RISK build-mode finding.
# ---------------------------------------------------------------------------
class TestGlobalFilterPreserved:
    def test_same_toolchain_filters_stdlib(self) -> None:
        old = _snap("1", stdlib=StdlibFamily.LIBSTDCXX)
        new = _snap("2", stdlib=StdlibFamily.LIBSTDCXX)
        assert stdlib_namespaces_excluded(old, new) is True

    def test_cross_implementation_does_not_disable_global_filter(self) -> None:
        # Regression guard: a cross-impl build-mode must NOT relax the filter.
        old = _snap("1", stdlib=StdlibFamily.LIBSTDCXX)
        new = _snap("2", stdlib=StdlibFamily.LIBCXX)
        assert stdlib_namespaces_excluded(old, new) is True

    def test_no_build_mode_keeps_filtering(self) -> None:
        old = _snap("1", build_mode=None)
        new = _snap("2", build_mode=None)
        assert stdlib_namespaces_excluded(old, new) is True


# ---------------------------------------------------------------------------
# Detector findings via compare()
# ---------------------------------------------------------------------------
class TestDetectorFindings:
    def test_stdlib_implementation_change_is_risk(self) -> None:
        old = _snap("1", stdlib=StdlibFamily.LIBSTDCXX, types=[_embed_stdlib_record()])
        new = _snap("2", stdlib=StdlibFamily.LIBCXX, types=[_embed_stdlib_record()])
        result = compare(old, new)
        kinds = {c.kind for c in result.changes}
        assert ChangeKind.STDLIB_IMPLEMENTATION_CHANGED in kinds
        # RISK, not BREAKING — never escalate on its own.
        assert ChangeKind.STDLIB_IMPLEMENTATION_CHANGED in RISK_KINDS

    def test_libcpp_abi_version_change_emitted(self) -> None:
        old = _snap("1", stdlib=StdlibFamily.LIBCXX, libcpp_abi=1)
        new = _snap("2", stdlib=StdlibFamily.LIBCXX, libcpp_abi=2)
        result = compare(old, new)
        kinds = {c.kind for c in result.changes}
        assert ChangeKind.LIBCPP_ABI_VERSION_CHANGED in kinds

    def test_silent_without_build_mode(self) -> None:
        old = _snap("1", build_mode=None, types=[_embed_stdlib_record()])
        new = _snap("2", build_mode=None, types=[_embed_stdlib_record()])
        result = compare(old, new)
        kinds = {c.kind for c in result.changes}
        assert ChangeKind.STDLIB_IMPLEMENTATION_CHANGED not in kinds
        assert ChangeKind.LIBCPP_ABI_VERSION_CHANGED not in kinds

    def test_same_implementation_emits_nothing(self) -> None:
        old = _snap("1", stdlib=StdlibFamily.LIBSTDCXX, types=[_embed_stdlib_record()])
        new = _snap("2", stdlib=StdlibFamily.LIBSTDCXX, types=[_embed_stdlib_record()])
        result = compare(old, new)
        kinds = {c.kind for c in result.changes}
        assert ChangeKind.STDLIB_IMPLEMENTATION_CHANGED not in kinds

    def test_no_layout_evidence_notes_gap_quietly(self) -> None:
        # No size_bits anywhere → layout unverifiable; finding still RISK and
        # its description must mention the missing evidence without escalating.
        old = _snap(
            "1",
            stdlib=StdlibFamily.LIBSTDCXX,
            types=[_embed_stdlib_record(size_bits=None)],
        )
        new = _snap(
            "2",
            stdlib=StdlibFamily.LIBCXX,
            types=[_embed_stdlib_record(size_bits=None)],
        )
        result = compare(old, new)
        finding = next(
            c
            for c in result.changes
            if c.kind == ChangeKind.STDLIB_IMPLEMENTATION_CHANGED
        )
        assert "no layout evidence" in finding.description.lower()

    def test_change_without_embedding_emits_base_description(self) -> None:
        # stdlib changes but no public type embeds a std:: type by value → still
        # a RISK finding, but the description carries no embed-specific note.
        old = _snap("1", stdlib=StdlibFamily.LIBSTDCXX, types=[])
        new = _snap("2", stdlib=StdlibFamily.LIBCXX, types=[])
        result = compare(old, new)
        finding = next(
            c
            for c in result.changes
            if c.kind == ChangeKind.STDLIB_IMPLEMENTATION_CHANGED
        )
        assert "embeds a std::" not in finding.description
        assert finding.old_value == "libstdc++" and finding.new_value == "libc++"

    def test_stdlib_field_by_pointer_is_not_embedding(self) -> None:
        # A std:: member held by pointer is layout-neutral, so it must NOT count
        # as an embedding (no embed note in the description).
        rec = RecordType(
            name="Handle",
            kind="class",
            size_bits=64,
            fields=[TypeField(name="vec", type="std::vector<int> *", offset_bits=0)],
        )
        old = _snap("1", stdlib=StdlibFamily.LIBSTDCXX, types=[rec])
        new = _snap("2", stdlib=StdlibFamily.LIBCXX, types=[rec])
        result = compare(old, new)
        finding = next(
            c
            for c in result.changes
            if c.kind == ChangeKind.STDLIB_IMPLEMENTATION_CHANGED
        )
        assert "embeds a std::" not in finding.description
        # Pointer-held std:: is layout-neutral: the only finding is the RISK
        # build-mode note, so the verdict must stay non-breaking (this scenario
        # was trimmed from the FP corpus and is asserted here instead).
        assert result.verdict.value not in {"BREAKING", "API_BREAK"}

    def test_standalone_stdlib_record_is_not_a_public_embedding(self) -> None:
        # Debug info carries a standalone std:: record whose fields are naturally
        # std:: types, but NO public owner type embeds the stdlib. The detector
        # must not read those toolchain-owned internals as a public embedding
        # (Codex review #345): the description carries no embed-specific note.
        std_record = RecordType(
            name="std::vector<int>",
            kind="class",
            size_bits=192,
            fields=[
                TypeField(
                    name="_M_start", type="std::__1::__wrap_iter<int *>", offset_bits=0
                )
            ],
        )
        old = _snap("1", stdlib=StdlibFamily.LIBSTDCXX, types=[std_record])
        new = _snap("2", stdlib=StdlibFamily.LIBCXX, types=[std_record])
        result = compare(old, new)
        finding = next(
            c
            for c in result.changes
            if c.kind == ChangeKind.STDLIB_IMPLEMENTATION_CHANGED
        )
        assert "embeds a std::" not in finding.description

    def test_stdlib_container_of_pointers_by_value_is_embedding(self) -> None:
        # std::vector<int*> held BY VALUE is layout-dependent: the `*` is in the
        # template argument, not the field type. Must count as an embedding.
        rec = RecordType(
            name="PtrBag",
            kind="class",
            size_bits=192,
            fields=[TypeField(name="items", type="std::vector<int*>", offset_bits=0)],
        )
        old = _snap("1", stdlib=StdlibFamily.LIBSTDCXX, types=[rec])
        new = _snap("2", stdlib=StdlibFamily.LIBCXX, types=[rec])
        result = compare(old, new)
        finding = next(
            c
            for c in result.changes
            if c.kind == ChangeKind.STDLIB_IMPLEMENTATION_CHANGED
        )
        assert "embeds a std::" in finding.description

    def test_msvc_stl_label_in_description(self) -> None:
        old = _snap("1", stdlib=StdlibFamily.MSVC_STL, types=[_embed_stdlib_record()])
        new = _snap("2", stdlib=StdlibFamily.LIBCXX, types=[_embed_stdlib_record()])
        result = compare(old, new)
        finding = next(
            c
            for c in result.changes
            if c.kind == ChangeKind.STDLIB_IMPLEMENTATION_CHANGED
        )
        assert "MSVC STL" in finding.description


class TestBuildModeFallback:
    """The detector must fire on real snapshots that lack a captured build_mode,
    by recovering the stdlib family from mangled symbol names (Codex PR #345 P1).
    """

    @staticmethod
    def _fn(mangled: str) -> Function:
        return Function(
            name=mangled,
            mangled=mangled,
            return_type="void",
            visibility=Visibility.PUBLIC,
        )

    def test_fires_without_build_mode_from_mangled_symbols(self) -> None:
        # libstdc++ (no __1) → libc++ (std::__1), no build_mode captured.
        old = AbiSnapshot(
            library="lib.so",
            version="1",
            functions=[self._fn("_ZNSt6vectorIiSaIiEE9push_backEi")],
        )
        new = AbiSnapshot(
            library="lib.so",
            version="2",
            functions=[self._fn("_ZNSt3__16vectorIiNS_9allocatorIiEEEE9push_backEOi")],
        )
        assert old.build_mode is None and new.build_mode is None
        kinds = {c.kind for c in compare(old, new).changes}
        assert ChangeKind.STDLIB_IMPLEMENTATION_CHANGED in kinds

    @staticmethod
    def _require_demangler() -> None:
        from abicheck.demangle import demangle

        if demangle("_Z3apiNSt3__16vectorIiNS_9allocatorIiEEEE") is None:
            import pytest

            pytest.skip("no C++ demangler available")

    def test_fires_from_libcxx_user_api_mangling(self) -> None:
        # The common case: the stdlib marker is inside a *user* API symbol, not
        # at its start. libstdc++ (cxx11 std::string) → libc++ (std::vector).
        # libc++ user-API detection goes through the demangler (Codex #345).
        self._require_demangler()
        old = AbiSnapshot(
            library="lib.so",
            version="1",
            functions=[
                self._fn("_Z3apiNSt7__cxx1112basic_stringIcSt11char_traitsIcESaIcEEE")
            ],
        )
        new = AbiSnapshot(
            library="lib.so",
            version="2",
            functions=[self._fn("_Z3apiNSt3__16vectorIiNS_9allocatorIiEEEE")],
        )
        kinds = {c.kind for c in compare(old, new).changes}
        assert ChangeKind.STDLIB_IMPLEMENTATION_CHANGED in kinds

    def test_libcxx_abi_version_recovered_from_user_api(self) -> None:
        # Both libc++, ABI v1 → v2, marker inside user-API manglings.
        # Recovering libc++ from user-API symbols goes through the demangler.
        self._require_demangler()
        old = AbiSnapshot(
            library="lib.so",
            version="1",
            functions=[self._fn("_Z3apiNSt3__16vectorIiNS_9allocatorIiEEEE")],
        )
        new = AbiSnapshot(
            library="lib.so",
            version="2",
            functions=[self._fn("_Z3apiNSt3__26vectorIiNS_9allocatorIiEEEE")],
        )
        kinds = {c.kind for c in compare(old, new).changes}
        assert ChangeKind.LIBCPP_ABI_VERSION_CHANGED in kinds
        # Same family both sides → no implementation-change finding.
        assert ChangeKind.STDLIB_IMPLEMENTATION_CHANGED not in kinds

    def test_fires_for_msvc_stl_from_coff_mangling(self) -> None:
        # MSVC C++ symbols are COFF-decorated (non-Itanium); the std namespace
        # is encoded as ``std@@``. MSVC STL → libc++ must still be detected.
        # The libc++ side is recovered via the demangler.
        self._require_demangler()
        msvc = (
            "?api@@YAXV?$basic_string@DU?$char_traits@D@std@@V?$allocator@D@2@@std@@@Z"
        )
        old = AbiSnapshot(library="lib.dll", version="1", functions=[self._fn(msvc)])
        new = AbiSnapshot(
            library="lib.so",
            version="2",
            functions=[self._fn("_Z3apiNSt3__16vectorIiNS_9allocatorIiEEEE")],
        )
        kinds = {c.kind for c in compare(old, new).changes}
        assert ChangeKind.STDLIB_IMPLEMENTATION_CHANGED in kinds

    def test_fires_for_untagged_libstdcxx_user_api(self) -> None:
        # std::vector<int> by value under libstdc++ carries no __cxx11 tag and no
        # libc++ __1 marker, so it is recovered via demangling (Codex #345).
        from abicheck.demangle import demangle

        if demangle("_Z3apiSt6vectorIiSaIiEE") is None:
            import pytest

            pytest.skip("no C++ demangler available")
        old = AbiSnapshot(
            library="lib.so",
            version="1",
            functions=[self._fn("_Z3apiSt6vectorIiSaIiEE")],
        )  # libstdc++
        new = AbiSnapshot(
            library="lib.so",
            version="2",
            functions=[self._fn("_Z3apiNSt3__16vectorIiNS_9allocatorIiEEEE")],
        )
        kinds = {c.kind for c in compare(old, new).changes}
        assert ChangeKind.STDLIB_IMPLEMENTATION_CHANGED in kinds

    def test_user_type_resembling_std_substitution_not_flagged(self) -> None:
        # A user type "St3Db" must NOT be misread as libstdc++: demangling shows
        # it carries no `std::`, so the side stays UNKNOWN and no finding fires.
        from abicheck.demangle import demangle

        if demangle("_Z3apiSt6vectorIiSaIiEE") is None:
            import pytest

            pytest.skip("no C++ demangler available")
        old = AbiSnapshot(
            library="lib.so", version="1", functions=[self._fn("_Z3api5St3Db")]
        )  # user type, not std
        new = AbiSnapshot(
            library="lib.so",
            version="2",
            functions=[self._fn("_Z3apiNSt3__16vectorIiNS_9allocatorIiEEEE")],
        )
        kinds = {c.kind for c in compare(old, new).changes}
        assert ChangeKind.STDLIB_IMPLEMENTATION_CHANGED not in kinds

    def test_fires_for_android_libcxx_ndk_namespace(self) -> None:
        # Android NDK libc++ uses the `std::__ndk1` inline namespace, which the
        # cheap `St\d__[12]` substring misses; the demangle fallback must still
        # classify it as libc++ (not libstdc++) so a libstdc++ → Android-libc++
        # comparison emits the finding (Codex #345).
        from abicheck.demangle import demangle

        ndk = "_Z3apiNSt6__ndk16vectorIiNS_9allocatorIiEEEE"
        if demangle(ndk) is None:
            import pytest

            pytest.skip("no C++ demangler available")
        old = AbiSnapshot(
            library="lib.so",
            version="1",
            functions=[self._fn("_Z3apiSt6vectorIiSaIiEE")],
        )  # libstdc++
        new = AbiSnapshot(
            library="lib.so", version="2", functions=[self._fn(ndk)]
        )  # Android libc++
        kinds = {c.kind for c in compare(old, new).changes}
        assert ChangeKind.STDLIB_IMPLEMENTATION_CHANGED in kinds

    def test_user_type_resembling_libcxx_namespace_not_flagged(self) -> None:
        # A user type mangled `6St3__1` contains the bytes `St3__1` but is not
        # libc++ (it demangles to `api(St3__1)`, no std::). Must not be flagged.
        from abicheck.demangle import demangle

        if demangle("_Z3apiSt6vectorIiSaIiEE") is None:
            import pytest

            pytest.skip("no C++ demangler available")
        old = AbiSnapshot(
            library="lib.so", version="1", functions=[self._fn("_Z3api6St3__1")]
        )  # user type "St3__1"
        new = AbiSnapshot(
            library="lib.so",
            version="2",
            functions=[self._fn("_Z3apiNSt3__16vectorIiNS_9allocatorIiEEEE")],
        )
        kinds = {c.kind for c in compare(old, new).changes}
        assert ChangeKind.STDLIB_IMPLEMENTATION_CHANGED not in kinds

    def test_user_namespace_resembling_msvc_std_not_flagged(self) -> None:
        # `?api@mystd@@YAXXZ` (mystd:: user namespace) contains `std@@` but not
        # the component `@std@@`, so it must not be read as MSVC STL.
        old = AbiSnapshot(
            library="lib.dll", version="1", functions=[self._fn("?api@mystd@@YAXXZ")]
        )  # mystd::api()
        new = AbiSnapshot(
            library="lib.so",
            version="2",
            functions=[self._fn("_Z3apiNSt3__16vectorIiNS_9allocatorIiEEEE")],
        )
        kinds = {c.kind for c in compare(old, new).changes}
        assert ChangeKind.STDLIB_IMPLEMENTATION_CHANGED not in kinds

    def test_user_namespace_resembling_std_not_flagged(self) -> None:
        # `mystd::api()` demangles to a name that *contains* the substring
        # "std::" but is NOT the std namespace; it must not be read as libstdc++.
        from abicheck.demangle import demangle

        if demangle("_ZN5mystd3apiEv") is None:
            import pytest

            pytest.skip("no C++ demangler available")
        old = AbiSnapshot(
            library="lib.so", version="1", functions=[self._fn("_ZN5mystd3apiEv")]
        )  # mystd::api()
        new = AbiSnapshot(
            library="lib.so",
            version="2",
            functions=[self._fn("_Z3apiNSt3__16vectorIiNS_9allocatorIiEEEE")],
        )
        kinds = {c.kind for c in compare(old, new).changes}
        assert ChangeKind.STDLIB_IMPLEMENTATION_CHANGED not in kinds

    def test_nested_user_std_namespace_not_flagged(self) -> None:
        # `boost::std::api()` is a *nested* user namespace literally named `std`,
        # not the global std::; the demangle fallback must not read it as
        # libstdc++ (Codex #345).
        from abicheck.demangle import demangle

        if demangle("_ZN5boost3std3apiEv") is None:
            import pytest

            pytest.skip("no C++ demangler available")
        old = AbiSnapshot(
            library="lib.so", version="1", functions=[self._fn("_ZN5boost3std3apiEv")]
        )  # boost::std::api()
        new = AbiSnapshot(
            library="lib.so",
            version="2",
            functions=[self._fn("_Z3apiNSt3__16vectorIiNS_9allocatorIiEEEE")],
        )
        kinds = {c.kind for c in compare(old, new).changes}
        assert ChangeKind.STDLIB_IMPLEMENTATION_CHANGED not in kinds

    def test_silent_when_no_mangled_symbols(self) -> None:
        old = AbiSnapshot(library="lib.so", version="1")
        new = AbiSnapshot(library="lib.so", version="2")
        kinds = {c.kind for c in compare(old, new).changes}
        assert ChangeKind.STDLIB_IMPLEMENTATION_CHANGED not in kinds

    def test_captured_build_mode_takes_precedence(self) -> None:
        # Same C-linkage symbols on both sides (no stdlib signal in mangling),
        # but captured build_mode says the implementation changed → still fires.
        old = _snap("1", stdlib=StdlibFamily.LIBSTDCXX)
        new = _snap("2", stdlib=StdlibFamily.LIBCXX)
        kinds = {c.kind for c in compare(old, new).changes}
        assert ChangeKind.STDLIB_IMPLEMENTATION_CHANGED in kinds

    def test_libcxx_capture_missing_abi_version_recovered_from_symbols(self) -> None:
        # Both sides captured as libc++ but with no libcpp_abi_version. The
        # version is recoverable from the std::__1 / __2 manglings, so the
        # partial capture must not short-circuit the symbol fallback and the
        # ABI-version change must still be reported (Codex review #345).
        self._require_demangler()
        old = AbiSnapshot(
            library="lib.so",
            version="1",
            functions=[self._fn("_Z3apiNSt3__16vectorIiNS_9allocatorIiEEEE")],
            build_mode=BuildMode(stdlib=StdlibFamily.LIBCXX),
        )
        new = AbiSnapshot(
            library="lib.so",
            version="2",
            functions=[self._fn("_Z3apiNSt3__26vectorIiNS_9allocatorIiEEEE")],
            build_mode=BuildMode(stdlib=StdlibFamily.LIBCXX),
        )
        kinds = {c.kind for c in compare(old, new).changes}
        assert ChangeKind.LIBCPP_ABI_VERSION_CHANGED in kinds
        # Same family both sides → no implementation-change finding.
        assert ChangeKind.STDLIB_IMPLEMENTATION_CHANGED not in kinds

    def test_partial_capture_enriched_from_symbols(self) -> None:
        # A captured BuildMode whose stdlib is still UNKNOWN (e.g. the producer
        # named the compiler but not the runtime) must not short-circuit the
        # symbol fallback: the mangled evidence still recovers the family
        # (Codex review #345).
        old = AbiSnapshot(
            library="lib.so",
            version="1",
            functions=[self._fn("_ZNSt6vectorIiSaIiEE9push_backEi")],  # libstdc++
            build_mode=BuildMode(stdlib=StdlibFamily.UNKNOWN),
        )
        new = AbiSnapshot(
            library="lib.so",
            version="2",
            functions=[
                self._fn("_ZNSt3__16vectorIiNS_9allocatorIiEEEE9push_backEOi")
            ],  # libc++
            build_mode=BuildMode(stdlib=StdlibFamily.UNKNOWN),
        )
        kinds = {c.kind for c in compare(old, new).changes}
        assert ChangeKind.STDLIB_IMPLEMENTATION_CHANGED in kinds
