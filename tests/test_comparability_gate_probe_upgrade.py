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
    check_contracts_comparable,
    compute_extraction_contract,
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
        ast_toolchain={"resolved_lang_mode": "c++"},
    )
    new = _snap(
        compute_extraction_contract(
            l2_frontend_ran=True,
            compiler_family="clang",
            compiler_version="18.1.3",
            language_standard="gnu11",
        ),
        ast_toolchain={"resolved_lang_mode": "c"},
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
        ast_toolchain={"resolved_lang_mode": "c++"},
    )
    new = _snap(
        compute_extraction_contract(
            l2_frontend_ran=True,
            compiler_family="gnu",
            compiler_version="11.4.0",
            language_standard="gnu11",
        ),
        ast_toolchain={"resolved_lang_mode": "c"},
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
        ast_toolchain={"resolved_lang_mode": "c++"},
    )
    new = _snap(
        compute_extraction_contract(
            l2_frontend_ran=True,
            compiler_family="clang",
            compiler_version="18.1.3",
            language_standard="c:gnu11",
        ),
        ast_toolchain={"resolved_lang_mode": "c"},
    )
    with pytest.raises(ProfileMismatchError):
        check_contracts_comparable(old, new)


def test_gate_content_driven_language_mode_divergence_still_raises_when_compiler_sha256_differs():
    """A different compiler_sha256 (a wrapper swapped at the same path)
    overrides an otherwise-matching family/version, mirroring the sibling
    upgrade carve-out's own sha256 check."""
    old = _snap(
        compute_extraction_contract(
            l2_frontend_ran=True,
            compiler_family="clang",
            compiler_version="18.1.3",
            language_standard="probed:__cplusplus=201703L",
        ),
        ast_toolchain={"compiler_sha256": "a" * 64, "resolved_lang_mode": "c++"},
    )
    new = _snap(
        compute_extraction_contract(
            l2_frontend_ran=True,
            compiler_family="clang",
            compiler_version="18.1.3",
            language_standard="gnu11",
        ),
        ast_toolchain={"compiler_sha256": "b" * 64, "resolved_lang_mode": "c"},
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
        ast_toolchain={"resolved_lang_mode": "c++"},
    )
    new = _snap(
        compute_extraction_contract(
            l2_frontend_ran=True,
            compiler_family="gnu",
            compiler_version="11.4.0",
            language_standard="gnu++20",
        ),
        ast_toolchain={"resolved_lang_mode": "c++"},
    )
    with pytest.raises(ProfileMismatchError):
        check_contracts_comparable(old, new)
