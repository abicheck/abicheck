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

"""abicheck-internal-bugs finding 2, Codex review follow-up: "Preserve
existing unpinned snapshot baselines".

A baseline persisted before ``dumper_toolchain._probe_default_language_
standard`` existed, given an explicit ``--lang``, recorded a bare ``"c"``/
``"c++"`` ``language_standard`` (no resolved standard existed yet). A
freshly re-dumped snapshot of the identical input under the identical
toolchain now records a real, lang-tagged ``"c:gnu11"``/``"c++:probed:..."``
value there instead, purely because the tool was upgraded. That must not, by
itself, make the pair ``NOT_COMPARABLE`` --
``comparability._language_standard_probe_upgrade_corroborated`` is the
carve-out closing that gap, tested here directly against the gate (split out
of ``test_comparability_gate.py``, which sits at the AI-readiness file-size
hard cap, rather than appended there).

A *bare empty* ``language_standard`` (no ``--lang`` given at all -- pure
content-based auto-detection) is deliberately **not** eligible for this
carve-out (Codex review, fresh evidence: it carries no signal about which
language mode the pre-upgrade dump actually resolved to, so it cannot be
safely distinguished from a genuine language-mode change) -- several tests
below pin that it still raises even with an otherwise-matching compiler.
"""

from __future__ import annotations

import pytest

from abicheck import comparability, dumper_toolchain
from abicheck.comparability import (
    _is_gcc_gxx_driver_pair,
    check_contracts_comparable,
    compute_extraction_contract,
)
from abicheck.comparability_language_mode import (
    language_standard_content_divergence_corroborated,
)
from abicheck.errors import ProfileMismatchError
from abicheck.model import AbiSnapshot, ExtractionContract


def _snap(
    contract: ExtractionContract | None,
    *,
    ast_toolchain: dict[str, str] | None = None,
) -> AbiSnapshot:
    return AbiSnapshot(
        library="libfoo.so",
        version="1.0",
        contract=contract,
        ast_toolchain=ast_toolchain or {},
    )


def test_probed_standard_prefix_constants_stay_in_sync():
    """CodeRabbit review: ``comparability.py`` deliberately mirrors (rather
    than imports) ``dumper_toolchain._PROBED_STANDARD_PREFIX`` -- see that
    module's own comment for why (avoiding a new cross-module dependency for
    one string literal). A mirrored constant can silently drift, so pin the
    two equal directly rather than relying on this file's own scenario tests
    to notice a drift indirectly."""
    assert (
        comparability._PROBED_STANDARD_PREFIX
        == dumper_toolchain._PROBED_STANDARD_PREFIX
    )


def test_gate_bare_empty_language_standard_still_raises_even_with_compiler_unchanged():
    """Codex review, fresh evidence: a bare empty ``language_standard`` (no
    ``--lang`` given at all) carries *no* signal about which language mode
    the pre-upgrade dump actually resolved to -- ``_resolve_force_cpp``'s
    decision is a function of the header content, which this carve-out has
    no access to. A header that later gains enough C++-only syntax to flip
    that decision (a real language-mode change) is indistinguishable, from
    the profile fields alone, from the pure upgrade-artifact case this
    carve-out exists to waive -- so an unchanged compiler_family/
    compiler_version is *not* sufficient corroboration for this shape, and
    this must still raise, unlike an earlier version of this carve-out that
    waived it."""
    old = _snap(
        compute_extraction_contract(
            l2_frontend_ran=True,
            compiler_family="gnu",
            compiler_version="11.4.0",
            language_standard="",  # pre-probe baseline: always empty when unpinned
        )
    )
    new = _snap(
        compute_extraction_contract(
            l2_frontend_ran=True,
            compiler_family="gnu",
            compiler_version="11.4.0",
            language_standard="probed:__cplusplus=201703L",
        )
    )
    with pytest.raises(ProfileMismatchError):
        check_contracts_comparable(old, new)


def test_gate_empty_vs_probed_language_standard_still_raises_when_compiler_family_differs():
    """The carve-out must not blindly waive every empty-vs-probed pair --
    only when the resolved compiler itself is independently confirmed
    unchanged. A genuinely different compiler_family alongside the
    transition is still a real profile difference."""
    old = _snap(
        compute_extraction_contract(
            l2_frontend_ran=True,
            compiler_family="gnu",
            compiler_version="11.4.0",
            language_standard="",
        )
    )
    new = _snap(
        compute_extraction_contract(
            l2_frontend_ran=True,
            compiler_family="clang",
            compiler_version="18.1.3",
            language_standard="probed:__cplusplus=202002L",
        )
    )
    with pytest.raises(ProfileMismatchError):
        check_contracts_comparable(old, new)


def test_gate_empty_vs_probed_language_standard_still_raises_when_compiler_version_differs():
    """Same family, upgraded version: a real toolchain change between the
    baseline and the comparison must still be caught, not waived just
    because the family string happens to match."""
    old = _snap(
        compute_extraction_contract(
            l2_frontend_ran=True,
            compiler_family="gnu",
            compiler_version="9.4.0",
            language_standard="",
        )
    )
    new = _snap(
        compute_extraction_contract(
            l2_frontend_ran=True,
            compiler_family="gnu",
            compiler_version="13.2.0",
            language_standard="probed:__cplusplus=201703L",
        )
    )
    with pytest.raises(ProfileMismatchError):
        check_contracts_comparable(old, new)


def test_gate_empty_vs_probed_language_standard_not_waived_without_compiler_evidence():
    """When neither side records a compiler_family/compiler_version at all,
    there is nothing to corroborate the transition against -- the carve-out
    must decline, not treat missing evidence as a pass."""
    old = _snap(compute_extraction_contract(l2_frontend_ran=True, language_standard=""))
    new = _snap(
        compute_extraction_contract(
            l2_frontend_ran=True, language_standard="probed:__cplusplus=201703L"
        )
    )
    with pytest.raises(ProfileMismatchError):
        check_contracts_comparable(old, new)


def test_gate_bare_empty_language_standard_vs_lang_qualified_probed_still_raises():
    """The lang-qualified counterpart of the test above: a lang tag on the
    *new* side alone does not rescue a bare-empty *old* side -- the old side
    still carries no evidence of which mode it actually resolved to, so this
    must still raise (unlike an earlier version of this carve-out)."""
    old = _snap(
        compute_extraction_contract(
            l2_frontend_ran=True,
            compiler_family="gnu",
            compiler_version="11.4.0",
            language_standard="",
        )
    )
    new = _snap(
        compute_extraction_contract(
            l2_frontend_ran=True,
            compiler_family="gnu",
            compiler_version="11.4.0",
            language_standard="c++:probed:__cplusplus=201703L",
        )
    )
    with pytest.raises(ProfileMismatchError):
        check_contracts_comparable(old, new)


def test_gate_bare_empty_language_standard_vs_forced_gnu11_still_raises():
    """The forced-``gnu11`` counterpart of the two tests above: a bare empty
    *old* side still raises against a newly-forced ``"gnu11"`` *new* side,
    for the identical reason -- ``"gnu11"`` carries no more information
    about the *old* side's actual mode than a ``"probed:..."`` value does."""
    old = _snap(
        compute_extraction_contract(
            l2_frontend_ran=True,
            compiler_family="gnu",
            compiler_version="11.4.0",
            language_standard="",
        )
    )
    new = _snap(
        compute_extraction_contract(
            l2_frontend_ran=True,
            compiler_family="gnu",
            compiler_version="11.4.0",
            language_standard="gnu11",
        )
    )
    with pytest.raises(ProfileMismatchError):
        check_contracts_comparable(old, new)


def test_gate_bare_lang_c_vs_lang_qualified_gnu11_waived():
    """Codex review, fresh evidence: an explicit ``--lang c`` baseline
    recorded a bare ``"c"`` (no resolved standard existed yet), not an
    empty string -- the carve-out must recognize this pre-upgrade shape
    too, not only the no-``--lang``-at-all empty-string case."""
    old = _snap(
        compute_extraction_contract(
            l2_frontend_ran=True,
            compiler_family="gnu",
            compiler_version="11.4.0",
            language_standard="c",
        )
    )
    new = _snap(
        compute_extraction_contract(
            l2_frontend_ran=True,
            compiler_family="gnu",
            compiler_version="11.4.0",
            language_standard="c:gnu11",
        )
    )
    check_contracts_comparable(old, new)  # must not raise


def test_gate_bare_lang_c_vs_self_healed_cpp_probe_still_raises():
    """Codex review, fresh evidence: an explicit ``--lang c`` tag does not
    pin the mode unconditionally -- both header-AST backends self-heal an
    explicit C request into C++ when the header turns out to need a C++
    stdlib header, regardless of whether C mode was auto-detected or
    explicitly requested. A self-healed parse's probed value always names
    ``__cplusplus`` -- a ``"c"``-tagged remainder containing it is real
    evidence the actual parse mode diverged from its own tag, not merely
    newly discovered upgrade evidence, so this must still raise."""
    old = _snap(
        compute_extraction_contract(
            l2_frontend_ran=True,
            compiler_family="gnu",
            compiler_version="11.4.0",
            language_standard="c",
        )
    )
    new = _snap(
        compute_extraction_contract(
            l2_frontend_ran=True,
            compiler_family="gnu",
            compiler_version="11.4.0",
            language_standard="c:probed:__cplusplus=201703L",
        )
    )
    with pytest.raises(ProfileMismatchError):
        check_contracts_comparable(old, new)


def test_gate_bare_lang_c_vs_genuine_msvc_probe_still_waived():
    """The negative counterpart: a genuinely-C, MSVC-dialect unpinned parse
    (never gnu11-forced, so it's probed) reports ``__STDC_VERSION__``, never
    ``__cplusplus`` -- this is not a self-heal artifact and must still
    waive."""
    old = _snap(
        compute_extraction_contract(
            l2_frontend_ran=True,
            compiler_family="msvc",
            compiler_version="19.38.0",
            language_standard="c",
        )
    )
    new = _snap(
        compute_extraction_contract(
            l2_frontend_ran=True,
            compiler_family="msvc",
            compiler_version="19.38.0",
            language_standard="c:probed:__STDC_VERSION__=201710L",
        )
    )
    check_contracts_comparable(old, new)  # must not raise


def test_gate_bare_lang_cpp_alias_vs_lang_qualified_probed_waived():
    """Codex review, fresh evidence: ``"cpp"`` is a second, still-supported
    spelling for C++ alongside ``"c++"`` (``_resolve_force_cpp`` accepts
    both) -- a Python API baseline built with ``lang="cpp"`` records the
    bare tag verbatim (``language_standard_field`` lowercases but does not
    otherwise canonicalize it), so the carve-out must recognize this
    spelling too, not just ``"c++"``."""
    old = _snap(
        compute_extraction_contract(
            l2_frontend_ran=True,
            compiler_family="gnu",
            compiler_version="11.4.0",
            language_standard="cpp",
        )
    )
    new = _snap(
        compute_extraction_contract(
            l2_frontend_ran=True,
            compiler_family="gnu",
            compiler_version="11.4.0",
            language_standard="cpp:probed:__cplusplus=201703L",
        )
    )
    check_contracts_comparable(old, new)  # must not raise


def test_gate_bare_lang_cpp_vs_lang_qualified_probed_waived():
    """The ``--lang c++`` counterpart of the above: a bare ``"c++"``
    pre-upgrade baseline against a lang-qualified probed value."""
    old = _snap(
        compute_extraction_contract(
            l2_frontend_ran=True,
            compiler_family="clang",
            compiler_version="18.1.3",
            language_standard="c++",
        )
    )
    new = _snap(
        compute_extraction_contract(
            l2_frontend_ran=True,
            compiler_family="clang",
            compiler_version="18.1.3",
            language_standard="c++:probed:__cplusplus=202002L",
        )
    )
    check_contracts_comparable(old, new)  # must not raise


def test_gate_bare_lang_c_vs_different_lang_still_raises():
    """A bare ``"c"`` pre-upgrade baseline against a *different* lang tag
    post-upgrade (``"c++:..."``) is a genuine profile difference, not an
    upgrade artifact -- the carve-out must not waive a real language-mode
    change just because the old side happens to match one of the
    unresolved-standard spellings."""
    old = _snap(
        compute_extraction_contract(
            l2_frontend_ran=True,
            compiler_family="gnu",
            compiler_version="11.4.0",
            language_standard="c",
        )
    )
    new = _snap(
        compute_extraction_contract(
            l2_frontend_ran=True,
            compiler_family="gnu",
            compiler_version="11.4.0",
            language_standard="c++:gnu++17",
        )
    )
    with pytest.raises(ProfileMismatchError):
        check_contracts_comparable(old, new)


def test_gate_bare_lang_c_vs_gnu11_still_raises_when_compiler_sha256_differs():
    """Codex review, fresh evidence: a compiler wrapper replaced at the same
    path can report an identical compiler_family/compiler_version string
    while actually selecting a different default dialect --
    compiler_sha256 (the resolved binary's own content hash), when present
    on both sides, must be checked too, not just the two text fields a
    wrapper's own --version output controls. Uses the lang-tagged (safe)
    old-side shape, not a bare empty string -- see the bare-empty-string
    tests above for why that shape never waives at all now."""
    old = _snap(
        compute_extraction_contract(
            l2_frontend_ran=True,
            compiler_family="gnu",
            compiler_version="11.4.0",
            language_standard="c",
        ),
        ast_toolchain={"compiler_sha256": "a" * 64},
    )
    new = _snap(
        compute_extraction_contract(
            l2_frontend_ran=True,
            compiler_family="gnu",
            compiler_version="11.4.0",
            language_standard="c:gnu11",
        ),
        ast_toolchain={"compiler_sha256": "b" * 64},
    )
    with pytest.raises(ProfileMismatchError):
        check_contracts_comparable(old, new)


def test_gate_bare_lang_c_vs_gnu11_waived_when_compiler_sha256_matches():
    """The positive counterpart: an unchanged compiler_sha256 on both sides
    (alongside the existing family/version match) still waives, same as
    before this corroboration was added."""
    old = _snap(
        compute_extraction_contract(
            l2_frontend_ran=True,
            compiler_family="gnu",
            compiler_version="11.4.0",
            language_standard="c",
        ),
        ast_toolchain={"compiler_sha256": "a" * 64},
    )
    new = _snap(
        compute_extraction_contract(
            l2_frontend_ran=True,
            compiler_family="gnu",
            compiler_version="11.4.0",
            language_standard="c:gnu11",
        ),
        ast_toolchain={"compiler_sha256": "a" * 64},
    )
    check_contracts_comparable(old, new)  # must not raise


def test_gate_bare_lang_c_vs_gnu11_waived_when_compiler_sha256_absent_on_one_side():
    """A legacy/older snapshot on one side (no compiler_sha256 recorded at
    all) must fall back to the family/version check rather than fail closed
    on evidence it was never in a position to carry."""
    old = _snap(
        compute_extraction_contract(
            l2_frontend_ran=True,
            compiler_family="gnu",
            compiler_version="11.4.0",
            language_standard="c",
        ),
        ast_toolchain={},
    )
    new = _snap(
        compute_extraction_contract(
            l2_frontend_ran=True,
            compiler_family="gnu",
            compiler_version="11.4.0",
            language_standard="c:gnu11",
        ),
        ast_toolchain={"compiler_sha256": "a" * 64},
    )
    check_contracts_comparable(old, new)  # must not raise


def _content_ast_toolchain(
    mode: str,
    *,
    explicit: str | None = "0",
    producer: str = "clang",
    frontend_version: str = "1.0",
    frontend_sha256: str = "f" * 64,
    host_version: str = "18.1.3",
    **extra: str,
) -> dict[str, str]:
    """Shared ``ast_toolchain`` shape for the content-divergence carve-out's
    own test group below: real ``check_contracts_comparable`` corroboration
    (PR #816 second/third review rounds) needs ``producer``/``version``
    (the frontend's own identity) and a raw ``compiler_version`` on
    ``ast_toolchain`` directly, not just the combined ``profile_fields``
    blob :func:`compute_extraction_contract`'s ``compiler_version=`` kwarg
    produces -- see ``_language_standard_content_divergence_corroborated``'s
    own docstring. *explicit* omitted (``None``) leaves
    ``language_standard_explicit`` unset entirely, for the fail-closed
    "missing provenance" test. *frontend_sha256* defaults to a shared
    constant (the frontend's own content hash, distinct from the host
    compiler's ``compiler_sha256``) so ordinary waive tests corroborate by
    default; a test asserting on it directly overrides it via ``**extra``'s
    ``sha256=``."""
    fields = {
        "resolved_lang_mode": mode,
        "producer": producer,
        "version": frontend_version,
        "sha256": frontend_sha256,
        "compiler_version": host_version,
        **extra,
    }
    if explicit is not None:
        fields["language_standard_explicit"] = explicit
    return fields


def test_gate_waives_content_driven_language_mode_divergence_when_toolchain_matches():
    """Real CI failure (Codex review, fresh evidence):
    examples/case66_language_linkage_changed and
    examples/case69_trivial_to_nontrivial each remove/add a C++-only
    construct (an ``extern "C"`` wrapper, a user-defined destructor) between
    old and new headers with no explicit ``--lang`` given on either side --
    ``_resolve_force_cpp`` auto-detects old as C++ and new as C (or vice
    versa) purely from that content. Under an identical, corroborated
    compiler this must not raise: it is real signal about the library's own
    headers, not evidence the two snapshots were extracted under a
    different environment.

    ``comparability._language_standard_content_divergence_corroborated`` is
    the carve-out closing this gap -- deliberately unconditional (unlike
    the sibling upgrade-only carve-out above) once corroborated by
    toolchain identity, since here there is no "upgrade artifact vs. real
    change" ambiguity to resolve: blocking the comparison is the wrong
    outcome either way. ``resolved_lang_mode`` differs (``"c++"`` vs.
    ``"c"``) -- a genuine mode switch, not merely a differing edition
    within the same mode (see the sibling test below for that narrower
    case, which must still raise)."""
    old = _snap(
        compute_extraction_contract(
            l2_frontend_ran=True,
            compiler_family="clang",
            compiler_version="18.1.3",
            language_standard="probed:__cplusplus=201703L",
        ),
        ast_toolchain=_content_ast_toolchain("c++"),
    )
    new = _snap(
        compute_extraction_contract(
            l2_frontend_ran=True,
            compiler_family="clang",
            compiler_version="18.1.3",
            language_standard="gnu11",
        ),
        ast_toolchain=_content_ast_toolchain("c"),
    )
    check_contracts_comparable(old, new)  # must not raise


def test_gate_waives_content_driven_divergence_for_a_hybrid_snapshot():
    """Codex review, fresh evidence (P2, seventh round): a
    ``--ast-frontend hybrid`` dump's merged ``ast_toolchain`` has no bare
    keys at all -- ``dumper_hybrid._merge_snapshots`` prefixes every one
    ``castxml_``/``clang_`` -- so every plain ``.get("resolved_lang_mode")``/
    ``.get("producer")``/etc. in this carve-out used to read empty and
    never corroborate a hybrid dump, silently leaving the exact
    ProfileMismatchError regression this whole carve-out exists to fix
    unfixed for that one frontend. Reuses the same identical-toolchain
    positive fixture above, but with every ``ast_toolchain`` key rendered
    under the ``castxml_`` prefix a real hybrid merge would produce."""

    def _as_hybrid(mode: str) -> dict[str, str]:
        return {f"castxml_{k}": v for k, v in _content_ast_toolchain(mode).items()}

    old = _snap(
        compute_extraction_contract(
            l2_frontend_ran=True,
            compiler_family="clang",
            compiler_version="18.1.3",
            language_standard="probed:__cplusplus=201703L",
        ),
        ast_toolchain=_as_hybrid("c++"),
    )
    new = _snap(
        compute_extraction_contract(
            l2_frontend_ran=True,
            compiler_family="clang",
            compiler_version="18.1.3",
            language_standard="gnu11",
        ),
        ast_toolchain=_as_hybrid("c"),
    )
    check_contracts_comparable(old, new)  # must not raise


def test_gate_content_driven_still_raises_for_hybrid_clang_leg_drift():
    """Codex review, fresh evidence (P2, eighth round): the hybrid fallback
    above only ever consulted the castxml leg. A real hybrid snapshot
    carries both ``castxml_*`` and ``clang_*`` provenance -- if the clang
    frontend/compiler genuinely changes between two snapshots while
    castxml's own leg stays identical, the castxml-only check saw nothing
    wrong and the carve-out discarded the combined ``compiler_version``
    mismatch (the only profile field carrying the changed clang identity),
    reporting the pair comparable despite a real toolchain difference on
    the clang leg. Every castxml_-prefixed key is identical between old
    and new; only the clang_ leg's ``version``/``sha256`` differ -- and
    that alone must still block the waiver."""
    old = _snap(
        compute_extraction_contract(
            l2_frontend_ran=True,
            compiler_family="gnu",
            compiler_version="13.3.0",
            language_standard="probed:__cplusplus=201703L",
        ),
        ast_toolchain={
            "castxml_resolved_lang_mode": "c++",
            "castxml_producer": "castxml",
            "castxml_version": "0.6.11",
            "castxml_sha256": "c" * 64,
            "castxml_compiler_version": "gcc (Ubuntu 13.3.0) 13.3.0",
            "castxml_language_standard_explicit": "0",
            "clang_producer": "clang",
            "clang_version": "18.1.3",
            "clang_sha256": "a" * 64,
            "clang_language_standard_explicit": "0",
        },
    )
    new = _snap(
        compute_extraction_contract(
            l2_frontend_ran=True,
            compiler_family="gnu",
            compiler_version="13.3.0",
            language_standard="gnu11",
        ),
        ast_toolchain={
            "castxml_resolved_lang_mode": "c",
            "castxml_producer": "castxml",
            "castxml_version": "0.6.11",
            "castxml_sha256": "c" * 64,
            "castxml_compiler_version": "gcc (Ubuntu 13.3.0) 13.3.0",
            "castxml_language_standard_explicit": "0",
            "clang_producer": "clang",
            "clang_version": "19.0.0",  # genuinely different clang build
            "clang_sha256": "b" * 64,
            "clang_language_standard_explicit": "0",
        },
    )
    with pytest.raises(ProfileMismatchError):
        check_contracts_comparable(old, new)


def test_gate_waives_when_castxml_resolves_a_mode_specific_gnu_driver_name():
    """Real CI failure, second round (Codex review + a real castxml/gcc
    repro against examples/case66_language_linkage_changed): castxml
    resolves a genuinely SEPARATE, mode-specific host-compiler binary per
    side (``dumper_ast_config._resolve_compiler_binary`` maps C mode to
    ``gcc``/``cc``, C++ mode to ``g++``/``c++``), so the raw
    ``compiler_version`` --version banner differs *only* in that leading
    driver-name word even for the exact same toolchain installation:
    ``"gcc (Ubuntu 13.3.0-...) 13.3.0..."`` vs. ``"g++ (Ubuntu 13.3.0-...)
    13.3.0..."``. Confirmed empirically against a real
    ``gcc``/``g++ 13.3.0`` pair. This must still waive -- rejecting it
    would reintroduce the exact case66/case69 CI failure this whole
    carve-out exists to close, just narrowed to the castxml lane."""
    old = _snap(
        compute_extraction_contract(
            l2_frontend_ran=True,
            compiler_family="gnu",
            compiler_version="13.3.0",
            language_standard="probed:__cplusplus=201703L",
        ),
        ast_toolchain=_content_ast_toolchain(
            "c++",
            producer="castxml",
            frontend_version="0.6.11",
            host_version="g++ (Ubuntu 13.3.0-6ubuntu2~24.04.1) 13.3.0\nCopyright ...",
        ),
    )
    new = _snap(
        compute_extraction_contract(
            l2_frontend_ran=True,
            compiler_family="gnu",
            compiler_version="13.3.0",
            language_standard="gnu11",
        ),
        ast_toolchain=_content_ast_toolchain(
            "c",
            producer="castxml",
            frontend_version="0.6.11",
            host_version="gcc (Ubuntu 13.3.0-6ubuntu2~24.04.1) 13.3.0\nCopyright ...",
        ),
    )
    check_contracts_comparable(old, new)  # must not raise


def test_gate_content_driven_still_raises_when_gnu_driver_version_number_genuinely_differs():
    """The normalization above must not become "any two GNU compiler_version
    strings match" -- a real cross-version skew (GCC 11 vs. GCC 13, both
    resolved with the mode-appropriate gcc/g++ driver name) still differs
    in its *remainder* after the leading driver-name word is stripped, so
    it must still raise."""
    old = _snap(
        compute_extraction_contract(
            l2_frontend_ran=True,
            compiler_family="gnu",
            compiler_version="11.4.0",
            language_standard="probed:__cplusplus=201703L",
        ),
        ast_toolchain=_content_ast_toolchain(
            "c++",
            producer="castxml",
            frontend_version="0.6.11",
            host_version="g++ (Ubuntu 11.4.0-1ubuntu1~22.04) 11.4.0\nCopyright ...",
        ),
    )
    new = _snap(
        compute_extraction_contract(
            l2_frontend_ran=True,
            compiler_family="gnu",
            compiler_version="13.3.0",
            language_standard="gnu11",
        ),
        ast_toolchain=_content_ast_toolchain(
            "c",
            producer="castxml",
            frontend_version="0.6.11",
            host_version="gcc (Ubuntu 13.3.0-6ubuntu2~24.04.1) 13.3.0\nCopyright ...",
        ),
    )
    with pytest.raises(ProfileMismatchError):
        check_contracts_comparable(old, new)


def test_gate_waives_gnu_driver_pair_even_when_compiler_sha256_differs():
    """The positive counterpart to the sha256 regression test below: `gcc`
    and `g++` are two distinct files on disk even from the identical
    package/version, so their content hashes always differ for exactly the
    legitimate driver-split pairing this carve-out exists to corroborate --
    requiring sha256 equality *there* would defeat the driver-name
    normalization entirely and reintroduce the original CI failure."""
    old = _snap(
        compute_extraction_contract(
            l2_frontend_ran=True,
            compiler_family="gnu",
            compiler_version="13.3.0",
            language_standard="probed:__cplusplus=201703L",
        ),
        ast_toolchain=_content_ast_toolchain(
            "c++",
            producer="castxml",
            frontend_version="0.6.11",
            host_version="g++ (Ubuntu 13.3.0-6ubuntu2~24.04.1) 13.3.0\nCopyright ...",
            compiler_sha256="a" * 64,
            compiler_selected="/usr/bin/g++",
            compiler_realpath="/usr/bin/g++",
            compiler_target_triple="x86_64-linux-gnu",
        ),
    )
    new = _snap(
        compute_extraction_contract(
            l2_frontend_ran=True,
            compiler_family="gnu",
            compiler_version="13.3.0",
            language_standard="gnu11",
        ),
        ast_toolchain=_content_ast_toolchain(
            "c",
            producer="castxml",
            frontend_version="0.6.11",
            host_version="gcc (Ubuntu 13.3.0-6ubuntu2~24.04.1) 13.3.0\nCopyright ...",
            compiler_sha256="b" * 64,
            compiler_selected="/usr/bin/gcc",
            compiler_realpath="/usr/bin/gcc",
            compiler_target_triple="x86_64-linux-gnu",
        ),
    )
    check_contracts_comparable(old, new)  # must not raise


def test_gate_content_driven_still_raises_when_identical_banner_but_sha256_differs():
    """Codex review, fresh evidence: the driver-name-skip above must not
    become "never check compiler_sha256 in this carve-out" -- when the raw
    `compiler_version` banners are already byte-identical (e.g. the direct-
    clang backend, which resolves the same binary regardless of mode), a
    differing `compiler_sha256` despite that identical banner is real
    signal of a genuinely different resolved binary (an in-place rebuild/
    vendor swap that didn't change the reported version string), and must
    still raise -- the driver-split reasoning that justifies skipping the
    hash check only applies when the banners themselves diverged."""
    old = _snap(
        compute_extraction_contract(
            l2_frontend_ran=True,
            compiler_family="clang",
            compiler_version="18.1.3",
            language_standard="probed:__cplusplus=201703L",
        ),
        ast_toolchain=_content_ast_toolchain(
            "c++", host_version="18.1.3", compiler_sha256="a" * 64
        ),
    )
    new = _snap(
        compute_extraction_contract(
            l2_frontend_ran=True,
            compiler_family="clang",
            compiler_version="18.1.3",
            language_standard="gnu11",
        ),
        ast_toolchain=_content_ast_toolchain(
            "c", host_version="18.1.3", compiler_sha256="b" * 64
        ),
    )
    with pytest.raises(ProfileMismatchError):
        check_contracts_comparable(old, new)


def test_gate_content_driven_still_raises_for_a_coincidental_vendor_banner_match():
    """Codex review, fresh evidence (second round): the normalized-remainder
    match by itself is not sufficient corroboration that two banners are
    genuinely the castxml gcc/g++ driver split -- two different vendor
    toolchains (`/opt/toolchain-a/bin/g++`, `/opt/toolchain-b/bin/gcc`)
    could coincidentally report an identical version-suffix banner while
    being different builds with different `compiler_sha256`. Neither
    leading word here is a real `gcc`/`cc`/`g++`/`c++` driver name, so
    `_is_gcc_gxx_driver_pair` must decline, and the differing sha256 must
    still raise -- must not be waived just because the two banners'
    driver-stripped remainders happen to match."""
    old = _snap(
        compute_extraction_contract(
            l2_frontend_ran=True,
            compiler_family="gnu",
            compiler_version="13.3.0",
            language_standard="probed:__cplusplus=201703L",
        ),
        ast_toolchain=_content_ast_toolchain(
            "c++",
            producer="castxml",
            frontend_version="0.6.11",
            host_version="toolchain-a (Ubuntu 13.3.0-6ubuntu2~24.04.1) 13.3.0",
            compiler_sha256="a" * 64,
        ),
    )
    new = _snap(
        compute_extraction_contract(
            l2_frontend_ran=True,
            compiler_family="gnu",
            compiler_version="13.3.0",
            language_standard="gnu11",
        ),
        ast_toolchain=_content_ast_toolchain(
            "c",
            producer="castxml",
            frontend_version="0.6.11",
            host_version="toolchain-b (Ubuntu 13.3.0-6ubuntu2~24.04.1) 13.3.0",
            compiler_sha256="b" * 64,
        ),
    )
    with pytest.raises(ProfileMismatchError):
        check_contracts_comparable(old, new)


def test_gate_content_driven_still_raises_for_real_driver_names_in_different_dirs():
    """Codex review, fresh evidence (third round): a genuine `gcc`/`g++`
    driver-name pair alone still isn't proof of one toolchain installation
    -- `/opt/toolchain-a/bin/g++` and `/opt/toolchain-b/bin/gcc` could
    resolve real "g++"/"gcc" banners (this is the ordinary case; a real
    GCC build's `--version` banner doesn't encode its own install path) for
    two genuinely different vendor installs sharing a version-suffix
    banner, with different `compiler_sha256`. `_compiler_install_dir` must
    see the differing `compiler_realpath` directories and decline to skip
    the sha256 check -- must not be waived just because the driver names
    and version suffix both happen to match."""
    old = _snap(
        compute_extraction_contract(
            l2_frontend_ran=True,
            compiler_family="gnu",
            compiler_version="13.3.0",
            language_standard="probed:__cplusplus=201703L",
        ),
        ast_toolchain=_content_ast_toolchain(
            "c++",
            producer="castxml",
            frontend_version="0.6.11",
            host_version="g++ (Ubuntu 13.3.0-6ubuntu2~24.04.1) 13.3.0",
            compiler_sha256="a" * 64,
            compiler_selected="/opt/toolchain-a/bin/g++",
            compiler_realpath="/opt/toolchain-a/bin/g++",
            compiler_target_triple="x86_64-linux-gnu",
        ),
    )
    new = _snap(
        compute_extraction_contract(
            l2_frontend_ran=True,
            compiler_family="gnu",
            compiler_version="13.3.0",
            language_standard="gnu11",
        ),
        ast_toolchain=_content_ast_toolchain(
            "c",
            producer="castxml",
            frontend_version="0.6.11",
            host_version="gcc (Ubuntu 13.3.0-6ubuntu2~24.04.1) 13.3.0",
            compiler_sha256="b" * 64,
            compiler_selected="/opt/toolchain-b/bin/gcc",
            compiler_realpath="/opt/toolchain-b/bin/gcc",
            compiler_target_triple="x86_64-linux-gnu",
        ),
    )
    # Same target triple on both sides (CodeRabbit review): isolates the
    # install-directory leg as the actual reason this still raises, rather
    # than passing vacuously because compiler_target_triple was empty.
    with pytest.raises(ProfileMismatchError):
        check_contracts_comparable(old, new)


def test_gate_waives_mingw_exe_suffixed_driver_pair_even_when_sha256_differs():
    """Codex review, fresh evidence (P1): MinGW/Windows GCC banners commonly
    lead with `gcc.exe`/`g++.exe` rather than the bare `gcc`/`g++`
    `_is_gcc_gxx_driver_pair` otherwise recognizes -- without stripping that
    suffix first, neither `endswith()` check matches, and a genuine
    castxml gcc.exe/g++.exe mode-switch pairing on Windows falls through to
    requiring a real sha256 match it can never satisfy (two distinct
    binaries), reintroducing exactly the Windows case this carve-out
    exists to restore. Must still waive."""
    old = _snap(
        compute_extraction_contract(
            l2_frontend_ran=True,
            compiler_family="gnu",
            compiler_version="13.3.0",
            language_standard="probed:__cplusplus=201703L",
        ),
        ast_toolchain=_content_ast_toolchain(
            "c++",
            producer="castxml",
            frontend_version="0.6.11",
            host_version="g++.exe (MinGW-W64) 13.3.0",
            compiler_sha256="a" * 64,
            compiler_selected=r"C:\mingw64\bin\g++.exe",
            compiler_realpath=r"C:\mingw64\bin\g++.exe",
            compiler_target_triple="x86_64-w64-mingw32",
        ),
    )
    new = _snap(
        compute_extraction_contract(
            l2_frontend_ran=True,
            compiler_family="gnu",
            compiler_version="13.3.0",
            language_standard="gnu11",
        ),
        ast_toolchain=_content_ast_toolchain(
            "c",
            producer="castxml",
            frontend_version="0.6.11",
            host_version="gcc.exe (MinGW-W64) 13.3.0",
            compiler_sha256="b" * 64,
            compiler_selected=r"C:\mingw64\bin\gcc.exe",
            compiler_realpath=r"C:\mingw64\bin\gcc.exe",
            compiler_target_triple="x86_64-w64-mingw32",
        ),
    )
    check_contracts_comparable(old, new)  # must not raise


def test_gate_content_driven_still_raises_for_mismatched_cross_compiler_targets():
    """Codex review, fresh evidence (P2, fourth round): the install-dir
    check alone still accepts two unrelated cross-compilers installed side
    by side in the same directory (e.g. `/usr/bin/aarch64-linux-gnu-g++`
    and `/usr/bin/x86_64-linux-gnu-gcc`), which can share an identical
    version-suffix banner while `_tool_identity_metadata` already records
    genuinely different `compiler_target_triple` values. Must not be waived
    just because the driver names, version suffix, and directory all
    happen to match -- the target triple disagreeing is real signal that
    the two sides parsed for different architectures."""
    old = _snap(
        compute_extraction_contract(
            l2_frontend_ran=True,
            compiler_family="gnu",
            compiler_version="13.3.0",
            language_standard="probed:__cplusplus=201703L",
        ),
        ast_toolchain=_content_ast_toolchain(
            "c++",
            producer="castxml",
            frontend_version="0.6.11",
            host_version="aarch64-linux-gnu-g++ (Ubuntu 13.3.0) 13.3.0",
            compiler_sha256="a" * 64,
            compiler_selected="/usr/bin/aarch64-linux-gnu-g++",
            compiler_realpath="/usr/bin/aarch64-linux-gnu-g++",
            compiler_target_triple="aarch64-linux-gnu",
        ),
    )
    new = _snap(
        compute_extraction_contract(
            l2_frontend_ran=True,
            compiler_family="gnu",
            compiler_version="13.3.0",
            language_standard="gnu11",
        ),
        ast_toolchain=_content_ast_toolchain(
            "c",
            producer="castxml",
            frontend_version="0.6.11",
            host_version="x86_64-linux-gnu-gcc (Ubuntu 13.3.0) 13.3.0",
            compiler_sha256="b" * 64,
            compiler_selected="/usr/bin/x86_64-linux-gnu-gcc",
            compiler_realpath="/usr/bin/x86_64-linux-gnu-gcc",
            compiler_target_triple="x86_64-linux-gnu",
        ),
    )
    with pytest.raises(ProfileMismatchError):
        check_contracts_comparable(old, new)


def test_gate_content_driven_still_raises_for_mismatched_driver_vendor_prefixes():
    """Codex review, fresh evidence (P2, fifth round): even a matching
    install dir and target triple isn't sufficient -- two unrelated
    GNU-compatible driver sets installed side by side for the *same*
    target (`vendor-a-g++`/`vendor-b-gcc`) both end in a recognized
    suffix, so the old endswith()-only check accepted them as a driver
    pair despite being different builds. `_is_gcc_gxx_driver_pair` must
    also require the two sides' cross-compile prefixes to match."""
    old = _snap(
        compute_extraction_contract(
            l2_frontend_ran=True,
            compiler_family="gnu",
            compiler_version="13.3.0",
            language_standard="probed:__cplusplus=201703L",
        ),
        ast_toolchain=_content_ast_toolchain(
            "c++",
            producer="castxml",
            frontend_version="0.6.11",
            host_version="vendor-a-g++ (Custom Build) 13.3.0",
            compiler_sha256="a" * 64,
            compiler_selected="/usr/bin/vendor-a-g++",
            compiler_realpath="/usr/bin/vendor-a-g++",
            compiler_target_triple="x86_64-linux-gnu",
        ),
    )
    new = _snap(
        compute_extraction_contract(
            l2_frontend_ran=True,
            compiler_family="gnu",
            compiler_version="13.3.0",
            language_standard="gnu11",
        ),
        ast_toolchain=_content_ast_toolchain(
            "c",
            producer="castxml",
            frontend_version="0.6.11",
            host_version="vendor-b-gcc (Custom Build) 13.3.0",
            compiler_sha256="b" * 64,
            compiler_selected="/usr/bin/vendor-b-gcc",
            compiler_realpath="/usr/bin/vendor-b-gcc",
            compiler_target_triple="x86_64-linux-gnu",
        ),
    )
    with pytest.raises(ProfileMismatchError):
        check_contracts_comparable(old, new)


def test_is_gcc_gxx_driver_pair_never_raises_on_whitespace_only_banner():
    """CodeRabbit review, fresh evidence: a whitespace-only banner is a
    truthy string, so the leading not-empty guard doesn't catch it -- and
    `" ".split(None, 1)` returns `[]`, so indexing `[0]` used to raise
    `IndexError` instead of returning `False`. A whitespace-only
    ast_toolchain["compiler_version"] is reachable in practice from a
    corrupt/hand-edited legacy snapshot, not just a synthetic test input."""
    assert _is_gcc_gxx_driver_pair(" ", "gcc (Ubuntu 13.3.0) 13.3.0") is False
    assert _is_gcc_gxx_driver_pair("g++ (Ubuntu 13.3.0) 13.3.0", "\t\n") is False
    assert _is_gcc_gxx_driver_pair("   ", "   ") is False


def test_gate_content_driven_still_raises_when_producer_differs():
    """A castxml-produced snapshot compared against a direct-clang-produced
    one is a genuinely different extraction mechanism, not merely a
    different mode-resolved host-compiler name -- ``producer`` disagreeing
    must still raise regardless of how well compiler_version happens to
    normalize."""
    old = _snap(
        compute_extraction_contract(
            l2_frontend_ran=True,
            compiler_family="gnu",
            compiler_version="13.3.0",
            language_standard="probed:__cplusplus=201703L",
        ),
        ast_toolchain=_content_ast_toolchain(
            "c++", producer="castxml", frontend_version="0.6.11"
        ),
    )
    new = _snap(
        compute_extraction_contract(
            l2_frontend_ran=True,
            compiler_family="gnu",
            compiler_version="13.3.0",
            language_standard="gnu11",
        ),
        ast_toolchain=_content_ast_toolchain(
            "c", producer="clang", frontend_version="18.1.3"
        ),
    )
    with pytest.raises(ProfileMismatchError):
        check_contracts_comparable(old, new)


def test_gate_content_driven_still_raises_when_frontend_version_differs():
    """Same producer (``castxml``), but a genuinely different castxml
    *build* (its own ``version`` string) -- must still raise even though
    the host compiler's own identity matches, since the two dumps did not
    go through the same castxml binary."""
    old = _snap(
        compute_extraction_contract(
            l2_frontend_ran=True,
            compiler_family="gnu",
            compiler_version="13.3.0",
            language_standard="probed:__cplusplus=201703L",
        ),
        ast_toolchain=_content_ast_toolchain(
            "c++", producer="castxml", frontend_version="0.6.11"
        ),
    )
    new = _snap(
        compute_extraction_contract(
            l2_frontend_ran=True,
            compiler_family="gnu",
            compiler_version="13.3.0",
            language_standard="gnu11",
        ),
        ast_toolchain=_content_ast_toolchain(
            "c", producer="castxml", frontend_version="0.7.0"
        ),
    )
    with pytest.raises(ProfileMismatchError):
        check_contracts_comparable(old, new)


def test_gate_content_driven_still_raises_for_mismatched_frontend_content_hash():
    """Codex review, fresh evidence (P2, sixth round): a matching frontend
    ``--version`` string alone isn't proof of one frontend build -- a
    vendor-patched or in-place rebuilt castxml could report the identical
    version text while being genuinely different content. Every other leg
    of this carve-out (a real gcc/g++ pair, matching install dir, matching
    target triple) is satisfied here; only the frontend's own content hash
    (``ast_toolchain["sha256"]``, distinct from the host compiler's
    ``compiler_sha256``) differs, and that alone must still block it."""
    old = _snap(
        compute_extraction_contract(
            l2_frontend_ran=True,
            compiler_family="gnu",
            compiler_version="13.3.0",
            language_standard="probed:__cplusplus=201703L",
        ),
        ast_toolchain=_content_ast_toolchain(
            "c++",
            producer="castxml",
            frontend_version="0.6.11",
            frontend_sha256="a" * 64,
            host_version="g++ (Ubuntu 13.3.0-6ubuntu2~24.04.1) 13.3.0",
            compiler_sha256="a" * 64,
            compiler_selected="/usr/bin/g++",
            compiler_realpath="/usr/bin/g++",
            compiler_target_triple="x86_64-linux-gnu",
        ),
    )
    new = _snap(
        compute_extraction_contract(
            l2_frontend_ran=True,
            compiler_family="gnu",
            compiler_version="13.3.0",
            language_standard="gnu11",
        ),
        ast_toolchain=_content_ast_toolchain(
            "c",
            producer="castxml",
            frontend_version="0.6.11",
            frontend_sha256="b" * 64,
            host_version="gcc (Ubuntu 13.3.0-6ubuntu2~24.04.1) 13.3.0",
            compiler_sha256="b" * 64,
            compiler_selected="/usr/bin/gcc",
            compiler_realpath="/usr/bin/gcc",
            compiler_target_triple="x86_64-linux-gnu",
        ),
    )
    with pytest.raises(ProfileMismatchError):
        check_contracts_comparable(old, new)


def test_gate_content_driven_language_mode_divergence_not_waived_when_provenance_missing():
    """Fail-closed default: a snapshot with no ``language_standard_explicit``
    provenance at all (a legacy snapshot dumped before this field existed)
    must not be silently trusted as auto-resolved -- the carve-out only
    waives once the provenance positively confirms *neither* side was
    explicitly pinned, not merely when nothing says otherwise."""
    old = _snap(
        compute_extraction_contract(
            l2_frontend_ran=True,
            compiler_family="clang",
            compiler_version="18.1.3",
            language_standard="probed:__cplusplus=201703L",
        ),
        ast_toolchain=_content_ast_toolchain("c++", explicit=None),
    )
    new = _snap(
        compute_extraction_contract(
            l2_frontend_ran=True,
            compiler_family="clang",
            compiler_version="18.1.3",
            language_standard="gnu11",
        ),
        ast_toolchain=_content_ast_toolchain("c"),
    )
    with pytest.raises(ProfileMismatchError):
        check_contracts_comparable(old, new)


def test_gate_waives_when_probe_failed_on_one_side_but_resolved_lang_mode_still_proves_the_switch():
    """Real CI failure (Windows integration-tests, PR #816): a real g++/
    clang++ found on a Windows runner's PATH can still reject
    dumper_toolchain._probe_default_language_standard's ``-E -dM -x <mode>
    -`` invocation for reasons unrelated to language mode (confirmed via
    tests/test_ast_compile_provenance.py::TestProbeDefaultLanguageStandard::
    test_probes_real_host_compiler_cpp failing on that runner with the
    probe genuinely returning None) -- ``_resolve_standard_provenance``
    then records a bare-empty ``language_standard`` for the C++ side,
    which differs from the C side's always-succeeding forced ``gnu11``
    literal, and the pre-fix carve-out's own bare-empty exclusion (copied
    from the sibling upgrade carve-out) declined to waive it, silently
    reintroducing case66/case69's own ProfileMismatchError on any
    toolchain where this probe fails.

    Unlike the sibling carve-out, this one has an independent signal for
    the mode switch (``resolved_lang_mode``, computed regardless of probe
    success) -- so a bare-empty side here is safe to waive through,
    provided the mode switch and toolchain identity are still both
    corroborated normally."""
    old = _snap(
        compute_extraction_contract(
            l2_frontend_ran=True,
            compiler_family="gnu",
            compiler_version="13.2.0",
            language_standard="",  # probe failed for this real g++
        ),
        ast_toolchain=_content_ast_toolchain(
            "c++", producer="castxml", frontend_version="0.6.11", host_version="13.2.0"
        ),
    )
    new = _snap(
        compute_extraction_contract(
            l2_frontend_ran=True,
            compiler_family="gnu",
            compiler_version="13.2.0",
            language_standard="gnu11",  # forced literal, never needs the probe
        ),
        ast_toolchain=_content_ast_toolchain(
            "c", producer="castxml", frontend_version="0.6.11", host_version="13.2.0"
        ),
    )
    check_contracts_comparable(old, new)  # must not raise


def test_gate_content_driven_language_mode_divergence_still_raises_when_compiler_family_differs():
    """The new carve-out must not blindly waive every non-empty
    language_standard pair -- only when the resolved compiler itself is
    independently confirmed unchanged."""
    old = _snap(
        compute_extraction_contract(
            l2_frontend_ran=True,
            compiler_family="clang",
            compiler_version="18.1.3",
            language_standard="probed:__cplusplus=201703L",
        ),
        ast_toolchain=_content_ast_toolchain("c++"),
    )
    new = _snap(
        compute_extraction_contract(
            l2_frontend_ran=True,
            compiler_family="gnu",
            compiler_version="11.4.0",
            language_standard="gnu11",
        ),
        ast_toolchain=_content_ast_toolchain("c", host_version="11.4.0"),
    )
    with pytest.raises(ProfileMismatchError):
        check_contracts_comparable(old, new)


def test_gate_content_driven_language_mode_divergence_not_waived_when_lang_pinned():
    """An explicit, user-pinned ``--lang`` that still disagrees between old
    and new is a genuine extraction-configuration difference -- the new
    carve-out only applies when neither side's language_standard reflects
    an explicit ``--lang``."""
    old = _snap(
        compute_extraction_contract(
            l2_frontend_ran=True,
            compiler_family="clang",
            compiler_version="18.1.3",
            language_standard="c++:probed:__cplusplus=201703L",
        ),
        ast_toolchain=_content_ast_toolchain("c++"),
    )
    new = _snap(
        compute_extraction_contract(
            l2_frontend_ran=True,
            compiler_family="clang",
            compiler_version="18.1.3",
            language_standard="c:gnu11",
        ),
        ast_toolchain=_content_ast_toolchain("c"),
    )
    with pytest.raises(ProfileMismatchError):
        check_contracts_comparable(old, new)


def test_gate_same_mode_differing_edition_still_raises_even_with_compiler_unchanged():
    """Regression pin for a real CI failure caught while adding the carve-out
    above: two header sets that both parse as C++ but resolve to a
    *different edition* purely because one side's content trips the
    ``force_cpp20`` requires-clause heuristic (mirrors
    ``test_dumper_contract_wiring.py::
    test_cpp20_heuristic_forced_standard_flows_into_profile_fingerprint``)
    must still raise -- this is the divergence
    ``_probe_default_language_standard``/``force_cpp20`` exist to catch,
    and it is materially different from a genuine ``c``<->``c++`` mode
    switch: the *same* declarations would be parsed under two different
    C++ dialects, which can itself produce different recorded facts for
    code that didn't change at all."""
    old = _snap(
        compute_extraction_contract(
            l2_frontend_ran=True,
            compiler_family="gnu",
            compiler_version="11.4.0",
            language_standard="probed:__cplusplus=201703L",
        ),
        ast_toolchain=_content_ast_toolchain("c++", host_version="11.4.0"),
    )
    new = _snap(
        compute_extraction_contract(
            l2_frontend_ran=True,
            compiler_family="gnu",
            compiler_version="11.4.0",
            language_standard="gnu++20",
        ),
        ast_toolchain=_content_ast_toolchain("c++", host_version="11.4.0"),
    )
    with pytest.raises(ProfileMismatchError):
        check_contracts_comparable(old, new)


def test_gate_content_driven_divergence_still_raises_for_explicit_std_without_lang():
    """Codex review finding, PR #816: an explicit ``-std=gnu11``/
    ``-std=gnu++17`` given *without* ``--lang`` is invisible to
    ``_language_standard_is_lang_pinned`` (which only recognizes an
    explicit lang *tag*), and ``dumper_toolchain._extract_explicit_std_value``
    returns such a value completely verbatim -- an explicit ``-std=gnu11``
    produces the identical bare literal the unpinned forced-default path
    also produces. Without a second, independent guard, this reads as a
    genuine ``c``<->``c++`` mode switch under a matching toolchain and gets
    incorrectly waived, even though the divergence came from explicit
    extraction configuration, not header content -- exactly the kind of
    toolchain-profile mismatch this whole gate exists to catch."""
    old = _snap(
        compute_extraction_contract(
            l2_frontend_ran=True,
            compiler_family="gnu",
            compiler_version="11.4.0",
            language_standard="gnu11",  # -std=gnu11, no --lang
        ),
        ast_toolchain=_content_ast_toolchain("c", explicit="1", host_version="11.4.0"),
    )
    new = _snap(
        compute_extraction_contract(
            l2_frontend_ran=True,
            compiler_family="gnu",
            compiler_version="11.4.0",
            language_standard="gnu++17",  # -std=gnu++17, no --lang
        ),
        ast_toolchain=_content_ast_toolchain(
            "c++", explicit="1", host_version="11.4.0"
        ),
    )
    with pytest.raises(ProfileMismatchError):
        check_contracts_comparable(old, new)


def test_gate_content_driven_divergence_still_raises_when_bare_forced_literal_is_actually_explicit():
    """The narrower collision case: an explicit ``-std=gnu11`` (no
    ``--lang``) produces the *exact same* bare literal
    (:data:`comparability._FORCED_C_STANDARD`) the unpinned forced-default
    path also produces for genuine C-mode content -- so this divergence
    cannot be told apart from case66/case69's own shape by the mode switch
    alone. Since the new side's bare ``"gnu++17"`` is not one of the two
    forms an unpinned parse can actually produce for C++ mode
    (only ``"gnu++20"`` -- the force_cpp20 literal -- or a ``"probed:..."``
    value are), the guard must still reject the pair."""
    old = _snap(
        compute_extraction_contract(
            l2_frontend_ran=True,
            compiler_family="clang",
            compiler_version="18.1.3",
            language_standard="gnu11",  # ambiguous: forced default OR -std=gnu11
        ),
        ast_toolchain=_content_ast_toolchain("c", explicit="1"),
    )
    new = _snap(
        compute_extraction_contract(
            l2_frontend_ran=True,
            compiler_family="clang",
            compiler_version="18.1.3",
            language_standard="gnu++17",  # never producible unpinned
        ),
        ast_toolchain=_content_ast_toolchain("c++", explicit="1"),
    )
    with pytest.raises(ProfileMismatchError):
        check_contracts_comparable(old, new)


def test_gate_bare_lang_c_vs_explicit_c17_still_raises_without_lang_switch():
    """Coverage: :func:`comparability_language_mode.language_standard_
    probe_upgrade_corroborated`'s ``is_newly_resolved`` guard. A bare
    ``"c"`` pre-probe baseline against ``"c:c17"`` on the new side shares
    the identical lang tag (so :func:`_newly_resolved_standard_remainder`
    finds a non-``None`` remainder, ``"c17"``) -- but ``"c17"`` is neither
    :data:`comparability_language_mode._FORCED_C_STANDARD` (``"gnu11"``)
    nor a ``"probed:..."`` value, so it cannot have come from the probe/
    forced-default upgrade this carve-out exists to waive. It can only be a
    real, explicit ``-std=c17`` pin -- a genuine profile difference, still
    rejected."""
    old = _snap(
        compute_extraction_contract(
            l2_frontend_ran=True,
            compiler_family="gnu",
            compiler_version="11.4.0",
            language_standard="c",
        )
    )
    new = _snap(
        compute_extraction_contract(
            l2_frontend_ran=True,
            compiler_family="gnu",
            compiler_version="11.4.0",
            language_standard="c:c17",
        )
    )
    with pytest.raises(ProfileMismatchError):
        check_contracts_comparable(old, new)


def test_gate_bare_lang_c_vs_gnu11_still_raises_when_compiler_family_differs():
    """Coverage: the probe-upgrade carve-out's own ``compiler_family``/
    ``compiler_version`` corroboration loop (distinct from the bare-empty
    variants above, which never reach this loop at all since their
    remainder is ``None`` before it). A genuinely newly-resolved remainder
    (``"c"`` -> ``"c:gnu11"``) must still be rejected when the resolved
    compiler_family itself differs between the two sides."""
    old = _snap(
        compute_extraction_contract(
            l2_frontend_ran=True,
            compiler_family="gnu",
            compiler_version="11.4.0",
            language_standard="c",
        )
    )
    new = _snap(
        compute_extraction_contract(
            l2_frontend_ran=True,
            compiler_family="clang",
            compiler_version="11.4.0",
            language_standard="c:gnu11",
        )
    )
    with pytest.raises(ProfileMismatchError):
        check_contracts_comparable(old, new)


def test_is_gcc_gxx_driver_pair_recognizes_bare_cc_and_gxx_pair():
    """Coverage: :func:`comparability_language_mode._driver_prefix`'s
    bare-match branch (``word == bare``). A real toolchain can invoke the C
    leg as the bare ``cc`` alias rather than ``gcc`` -- paired against a
    bare ``g++`` banner, this is still a genuine, same-toolchain C/C++
    driver pair (an empty cross-compile prefix on both legs), same as the
    already-covered ``gcc``/``g++`` suffix-match shape."""
    assert (
        _is_gcc_gxx_driver_pair(
            "cc (Ubuntu 11.4.0) 11.4.0", "g++ (Ubuntu 11.4.0) 11.4.0"
        )
        is True
    )


def test_is_gcc_gxx_driver_pair_false_when_either_banner_is_empty():
    """Coverage: the leading empty-string guard, distinct from the
    whitespace-only-banner regression test above (a *literal* empty string
    short-circuits before ``.split()`` is even called)."""
    assert _is_gcc_gxx_driver_pair("", "g++ (Ubuntu 13.3.0) 13.3.0") is False
    assert _is_gcc_gxx_driver_pair("gcc (Ubuntu 13.3.0) 13.3.0", "") is False


def test_content_divergence_corroborated_false_for_identical_language_standard():
    """Coverage: :func:`language_standard_content_divergence_corroborated`'s
    own leading guard -- called directly, since ``check_contracts_
    comparable`` only ever reaches this carve-out when the two
    ``language_standard`` values already differ (this branch is otherwise
    unreachable through the gate). An identical value on both sides is not
    a "divergence" to corroborate at all."""
    old = _snap(
        compute_extraction_contract(l2_frontend_ran=True, language_standard="gnu11")
    )
    new = _snap(
        compute_extraction_contract(l2_frontend_ran=True, language_standard="gnu11")
    )
    assert (
        language_standard_content_divergence_corroborated(
            old, new, old.contract.profile_fields, new.contract.profile_fields
        )
        is False
    )


def test_gate_content_driven_divergence_still_raises_for_unresolvable_old_standard():
    """Coverage: the content-divergence carve-out's *old*-side ``known-
    auto-resolved-form`` guard (the new-side counterpart is already covered
    by the exact-literal-collision test above, which fails on the *new*
    side's ``"gnu++17"`` -- this pins the mirror case, where the *old* side
    is the one carrying a literal no unpinned parse could have produced for
    its own mode)."""
    old = _snap(
        compute_extraction_contract(
            l2_frontend_ran=True,
            compiler_family="gnu",
            compiler_version="11.4.0",
            language_standard="c17",  # never producible unpinned for C mode
        ),
        ast_toolchain=_content_ast_toolchain("c", explicit="0"),
    )
    new = _snap(
        compute_extraction_contract(
            l2_frontend_ran=True,
            compiler_family="gnu",
            compiler_version="11.4.0",
            language_standard="gnu++20",
        ),
        ast_toolchain=_content_ast_toolchain("c++", explicit="0"),
    )
    with pytest.raises(ProfileMismatchError):
        check_contracts_comparable(old, new)


def test_gate_content_driven_still_raises_when_host_compiler_version_missing_on_one_side():
    """Coverage: the content-divergence carve-out's final ``compiler_
    version`` presence guard. Every other corroboration signal
    (``compiler_family``, ``producer``, frontend ``version``/``sha256``)
    matches, but one side's ``ast_toolchain["compiler_version"]`` is empty
    (a legacy/degraded snapshot) -- there is nothing to compare the host
    compiler identity against, so this must fail closed rather than treat
    missing evidence as a pass."""
    old = _snap(
        compute_extraction_contract(
            l2_frontend_ran=True,
            compiler_family="gnu",
            compiler_version="11.4.0",
            language_standard="gnu11",
        ),
        ast_toolchain=_content_ast_toolchain("c", host_version=""),
    )
    new = _snap(
        compute_extraction_contract(
            l2_frontend_ran=True,
            compiler_family="gnu",
            compiler_version="11.4.0",
            language_standard="probed:__cplusplus=201703L",
        ),
        ast_toolchain=_content_ast_toolchain("c++", host_version="11.4.0"),
    )
    with pytest.raises(ProfileMismatchError):
        check_contracts_comparable(old, new)


def test_gate_content_driven_still_raises_for_distinct_wrapper_scripts_sharing_a_banner():
    """Codex review, fresh evidence: two distinct wrapper scripts in one
    directory (`/opt/bin/vendor-a-g++`, `/opt/bin/vendor-b-gcc`) can each
    faithfully delegate `--version` to the *same* real, bare "gcc"/"g++"
    underneath -- passing the banner-driver-pair check, sharing a
    directory, and sharing a target triple -- while still being genuinely
    different tools (different injected extraction flags) with genuinely
    different `compiler_sha256`. The banner-only `_is_gcc_gxx_driver_pair`
    check alone cannot see this: the banner text itself is bare ("g++ (GCC)
    13.3.0"/"gcc (GCC) 13.3.0"), so only the *resolved binary path's own*
    vendor-specific prefix distinguishes them."""
    old = _snap(
        compute_extraction_contract(
            l2_frontend_ran=True,
            compiler_family="gnu",
            compiler_version="13.3.0",
            language_standard="probed:__cplusplus=201703L",
        ),
        ast_toolchain=_content_ast_toolchain(
            "c++",
            producer="castxml",
            frontend_version="0.6.11",
            host_version="g++ (GCC) 13.3.0",
            compiler_sha256="a" * 64,
            compiler_selected="/opt/bin/vendor-a-g++",
            compiler_realpath="/opt/bin/vendor-a-g++",
            compiler_target_triple="x86_64-linux-gnu",
        ),
    )
    new = _snap(
        compute_extraction_contract(
            l2_frontend_ran=True,
            compiler_family="gnu",
            compiler_version="13.3.0",
            language_standard="gnu11",
        ),
        ast_toolchain=_content_ast_toolchain(
            "c",
            producer="castxml",
            frontend_version="0.6.11",
            host_version="gcc (GCC) 13.3.0",
            compiler_sha256="b" * 64,
            compiler_selected="/opt/bin/vendor-b-gcc",
            compiler_realpath="/opt/bin/vendor-b-gcc",
            compiler_target_triple="x86_64-linux-gnu",
        ),
    )
    with pytest.raises(ProfileMismatchError):
        check_contracts_comparable(old, new)


def test_gate_content_driven_divergence_still_raises_for_the_exact_literal_collision_pair():
    """Codex review finding, PR #816 (second round): an explicit
    ``-std=gnu11``/``-std=gnu++20`` given without ``--lang`` produces
    literals that exactly equal *both* entries of
    :data:`comparability._KNOWN_AUTO_RESOLVED_BARE_STANDARDS` -- the one
    collision the bare-form check (previous test) cannot reject on its own,
    since both literals genuinely are ``"known auto-resolved forms"`` for
    their respective modes. Only the real
    ``language_standard_explicit`` provenance (set ``"1"`` here, as a real
    explicit dump would record) tells this apart from case66/case69's
    genuinely auto-resolved shape."""
    old = _snap(
        compute_extraction_contract(
            l2_frontend_ran=True,
            compiler_family="gnu",
            compiler_version="11.4.0",
            language_standard="gnu11",  # -std=gnu11, no --lang
        ),
        ast_toolchain=_content_ast_toolchain("c", explicit="1", host_version="11.4.0"),
    )
    new = _snap(
        compute_extraction_contract(
            l2_frontend_ran=True,
            compiler_family="gnu",
            compiler_version="11.4.0",
            language_standard="gnu++20",  # -std=gnu++20, no --lang
        ),
        ast_toolchain=_content_ast_toolchain(
            "c++", explicit="1", host_version="11.4.0"
        ),
    )
    with pytest.raises(ProfileMismatchError):
        check_contracts_comparable(old, new)
