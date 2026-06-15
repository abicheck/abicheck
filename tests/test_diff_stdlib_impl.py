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
from abicheck.checker_policy import RISK_KINDS, ChangeKind, Verdict
from abicheck.model import (
    AbiSnapshot,
    Function,
    Param,
    RecordType,
    TypeField,
    Variable,
    Visibility,
    stdlib_namespaces_excluded,
)
from abicheck.policy_file import PolicyFile
from abicheck.severity import effective_verdict_for_change


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
    def test_stdlib_implementation_change_without_embedding_is_risk(self) -> None:
        old = _snap("1", stdlib=StdlibFamily.LIBSTDCXX)
        new = _snap("2", stdlib=StdlibFamily.LIBCXX)
        result = compare(old, new)
        kinds = {c.kind for c in result.changes}
        assert ChangeKind.STDLIB_IMPLEMENTATION_CHANGED in kinds
        assert ChangeKind.STDLIB_IMPLEMENTATION_CHANGED in RISK_KINDS
        assert result.verdict is Verdict.COMPATIBLE_WITH_RISK

    def test_stdlib_implementation_change_with_public_embedding_is_breaking(self) -> None:
        old = _snap("1", stdlib=StdlibFamily.LIBSTDCXX, types=[_embed_stdlib_record()])
        new = _snap("2", stdlib=StdlibFamily.LIBCXX, types=[_embed_stdlib_record()])
        result = compare(old, new)
        finding = next(
            c
            for c in result.changes
            if c.kind == ChangeKind.STDLIB_IMPLEMENTATION_CHANGED
        )
        assert finding.effective_verdict is Verdict.BREAKING
        assert result.verdict is Verdict.BREAKING

    def test_policy_override_does_not_disagree_with_effective_verdict(self, tmp_path) -> None:
        old = _snap("1", stdlib=StdlibFamily.LIBSTDCXX, types=[_embed_stdlib_record()])
        new = _snap("2", stdlib=StdlibFamily.LIBCXX, types=[_embed_stdlib_record()])
        finding = next(
            c
            for c in compare(old, new).changes
            if c.kind == ChangeKind.STDLIB_IMPLEMENTATION_CHANGED
        )
        p = tmp_path / "policy.yaml"
        p.write_text("overrides:\n  stdlib_implementation_changed: ignore\n")
        pf = PolicyFile.load(p)

        assert finding.effective_verdict is Verdict.BREAKING
        assert pf.compute_verdict([finding]) is Verdict.BREAKING
        assert effective_verdict_for_change(finding, policy_file=pf) is Verdict.BREAKING

    def test_private_embedding_does_not_escalate_when_surface_is_resolved(self) -> None:
        private_owner = _embed_stdlib_record()
        public_owner = RecordType(name="PublicApi", kind="class", size_bits=32)
        public_fn = Function(
            name="api",
            mangled="_Z3apiv",
            return_type="PublicApi",
            visibility=Visibility.PUBLIC,
        )
        old = _snap(
            "1", stdlib=StdlibFamily.LIBSTDCXX,
            types=[private_owner, public_owner],
        )
        new = _snap(
            "2", stdlib=StdlibFamily.LIBCXX,
            types=[private_owner, public_owner],
        )
        old.functions.append(public_fn)
        new.functions.append(public_fn)

        result = compare(old, new)
        finding = next(
            c
            for c in result.changes
            if c.kind == ChangeKind.STDLIB_IMPLEMENTATION_CHANGED
        )
        assert "embeds a std::" not in finding.description
        assert finding.effective_verdict is None
        assert result.verdict is Verdict.COMPATIBLE_WITH_RISK

    def test_pointer_reachable_embedding_does_not_escalate(self) -> None:
        impl = RecordType(
            name="Impl",
            kind="class",
            size_bits=192,
            fields=[TypeField(name="data", type="std::vector<int>", offset_bits=0)],
        )
        public_owner = RecordType(
            name="PublicApi",
            kind="class",
            size_bits=64,
            fields=[TypeField(name="impl", type="Impl *", offset_bits=0)],
        )
        public_fn = Function(
            name="api",
            mangled="_Z3apiv",
            return_type="PublicApi",
            visibility=Visibility.PUBLIC,
        )
        old = _snap(
            "1", stdlib=StdlibFamily.LIBSTDCXX,
            types=[impl, public_owner],
        )
        new = _snap(
            "2", stdlib=StdlibFamily.LIBCXX,
            types=[impl, public_owner],
        )
        old.functions.append(public_fn)
        new.functions.append(public_fn)

        result = compare(old, new)
        finding = next(
            c
            for c in result.changes
            if c.kind == ChangeKind.STDLIB_IMPLEMENTATION_CHANGED
        )
        assert "embeds a std::" not in finding.description
        assert finding.effective_verdict is None
        assert result.verdict is Verdict.COMPATIBLE_WITH_RISK

    def test_same_size_owner_with_filtered_stdlib_layout_change_is_breaking(self) -> None:
        owner = _embed_stdlib_record(size_bits=192)
        old_std = RecordType(
            name="std::string",
            kind="class",
            size_bits=192,
            fields=[TypeField(name="_M_dataplus", type="char *", offset_bits=0)],
        )
        new_std = RecordType(
            name="std::string",
            kind="class",
            size_bits=192,
            fields=[TypeField(name="__data", type="char *", offset_bits=0)],
        )
        old = _snap("1", stdlib=StdlibFamily.LIBSTDCXX, types=[owner, old_std])
        new = _snap("2", stdlib=StdlibFamily.LIBCXX, types=[owner, new_std])

        result = compare(old, new)

        assert stdlib_namespaces_excluded(old, new) is True
        assert [c.kind for c in result.changes] == [
            ChangeKind.STDLIB_IMPLEMENTATION_CHANGED
        ]
        assert result.verdict is Verdict.BREAKING

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


class TestPublicByValueClosure:
    """The BREAKING escalation hinges on whether a std::-embedding record is
    reachable from the public surface *by value*. The single-field case is
    covered above; these exercise the reachability closure across base classes,
    typedef aliases, and public variables, plus its robustness to shared types
    and reference cycles — real ABI-reachability semantics, not the easy path.
    """

    @staticmethod
    def _embedding(name: str = "Inner", size_bits: int = 192) -> RecordType:
        """A record that embeds a std:: container by value (layout-dependent)."""
        return RecordType(
            name=name,
            kind="class",
            size_bits=size_bits,
            fields=[TypeField(name="data", type="std::vector<int>", offset_bits=0)],
        )

    @staticmethod
    def _pair(**snapshot_kwargs: object) -> tuple[AbiSnapshot, AbiSnapshot]:
        """An (libstdc++, libc++) pair with identical surface but differing impl."""

        def mk(version: str, fam: StdlibFamily) -> AbiSnapshot:
            return AbiSnapshot(
                library="libwidget.so.1",
                version=version,
                build_mode=BuildMode(stdlib=fam),
                **snapshot_kwargs,  # type: ignore[arg-type]
            )

        return mk("1", StdlibFamily.LIBSTDCXX), mk("2", StdlibFamily.LIBCXX)

    def _finding(self, old: AbiSnapshot, new: AbiSnapshot):
        result = compare(old, new)
        finding = next(
            c
            for c in result.changes
            if c.kind == ChangeKind.STDLIB_IMPLEMENTATION_CHANGED
        )
        return result, finding

    def test_embedding_reachable_via_base_class_escalates(self) -> None:
        # A namespaced public type returned by a public function; the embedding
        # is reached only through Widget's base-class list, not its own fields.
        inner = self._embedding("Inner")
        widget = RecordType(
            name="app::Widget", kind="class", size_bits=64, bases=["Inner"]
        )
        fn = Function(
            name="make", mangled="_Z4makev",
            return_type="app::Widget", visibility=Visibility.PUBLIC,
        )
        old, new = self._pair(types=[widget, inner], functions=[fn])
        result, finding = self._finding(old, new)
        assert finding.effective_verdict is Verdict.BREAKING
        assert result.verdict is Verdict.BREAKING

    def test_embedding_reachable_via_typedef_escalates(self) -> None:
        # The public parameter names a typedef; the closure must follow the
        # typedef target to find the embedding behind the alias.
        inner = self._embedding("Buffer")
        fn = Function(
            name="take", mangled="_Z4takev", return_type="void",
            params=[Param(name="b", type="BufferAlias")],
            visibility=Visibility.PUBLIC,
        )
        old, new = self._pair(
            types=[inner], functions=[fn], typedefs={"BufferAlias": "Buffer"}
        )
        _, finding = self._finding(old, new)
        assert finding.effective_verdict is Verdict.BREAKING

    def test_embedding_reachable_via_public_variable_escalates(self) -> None:
        # A public global variable is also a public root that seeds the closure.
        inner = self._embedding("Buffer")
        var = Variable(
            name="g_buf", mangled="g_buf",
            type="Buffer", visibility=Visibility.PUBLIC,
        )
        old, new = self._pair(types=[inner], variables=[var])
        _, finding = self._finding(old, new)
        assert finding.effective_verdict is Verdict.BREAKING

    def test_embedding_seeded_only_by_hidden_function_does_not_escalate(self) -> None:
        # A public variable keeps the surface resolvable but does NOT reach the
        # embedding; the only path to Buffer is a hidden (non-public) function,
        # which must not seed the by-value closure. So no escalation.
        inner = self._embedding("Buffer")
        public_var = Variable(
            name="g_flag", mangled="g_flag",
            type="int", visibility=Visibility.PUBLIC,
        )
        hidden_fn = Function(
            name="hidden", mangled="_Z6hiddenv",
            return_type="Buffer", visibility=Visibility.HIDDEN,
        )
        old, new = self._pair(
            types=[inner], functions=[hidden_fn], variables=[public_var]
        )
        result, finding = self._finding(old, new)
        assert finding.effective_verdict is None
        assert result.verdict is Verdict.COMPATIBLE_WITH_RISK

    def test_closure_is_robust_to_shared_types_and_cycles(self) -> None:
        # Widget reaches the embedding via BOTH a base and a by-value field
        # (shared target → must be visited once), references an unknown type
        # (not a record), holds a const self-pointer (a reference cycle, but
        # pointer-indirect so layout-neutral), and the public function has an
        # unspelled parameter type. The closure must terminate and still flag
        # the by-value-reachable embedding.
        inner = self._embedding("Inner")
        widget = RecordType(
            name="Widget", kind="class", size_bits=64,
            bases=["Inner"],
            fields=[
                TypeField(name="dup", type="Inner", offset_bits=0),
                TypeField(name="color", type="Color", offset_bits=64),
                TypeField(name="self", type="Widget * const", offset_bits=128),
            ],
        )
        fn = Function(
            name="make", mangled="_Z4makev", return_type="Widget",
            params=[Param(name="anon", type="")],
            visibility=Visibility.PUBLIC,
        )
        old, new = self._pair(types=[widget, inner], functions=[fn])
        _, finding = self._finding(old, new)
        assert finding.effective_verdict is Verdict.BREAKING
