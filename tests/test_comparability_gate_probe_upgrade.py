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
standard`` existed recorded an empty ``language_standard`` for any unpinned
(no explicit ``-std=``, no forced-C++20-heuristic) dump. A freshly re-dumped
snapshot of the identical input under the identical toolchain now records a
real ``"probed:..."`` value there instead, purely because the tool was
upgraded. That must not, by itself, make the pair ``NOT_COMPARABLE`` --
``comparability._language_standard_probe_upgrade_corroborated`` is the
carve-out closing that gap, tested here directly against the gate (split out
of ``test_comparability_gate.py``, which sits at the AI-readiness file-size
hard cap, rather than appended there).
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


def _snap(contract: ExtractionContract | None) -> AbiSnapshot:
    return AbiSnapshot(library="libfoo.so", version="1.0", contract=contract)


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


def test_gate_empty_vs_probed_language_standard_waived_when_compiler_unchanged():
    """The exact scenario the reviewer flagged: an old (pre-probe) baseline
    and a freshly re-dumped snapshot of the identical input under the
    identical resolved compiler must stay comparable."""
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
    check_contracts_comparable(old, new)  # must not raise


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


def test_gate_empty_vs_probed_language_standard_waived_when_lang_qualified():
    """CodeRabbit review, fresh evidence: when an explicit ``--lang`` was
    given, ``language_standard_field`` prefixes the probed value with the
    resolved mode (``"c++:probed:..."``/``"c:probed:..."``), not the bare
    ``"probed:..."`` form the other tests above use -- the carve-out must
    still recognize the transition and waive it, not treat the lang prefix
    as an unrelated profile difference."""
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
    check_contracts_comparable(old, new)  # must not raise


def test_gate_empty_vs_forced_gnu11_waived_when_compiler_unchanged():
    """Codex review, fresh evidence: an unpinned C/gnu-dialect parse no
    longer probes at all -- it reports the forced ``"gnu11"`` standard
    directly (see ``dumper_toolchain._FORCED_C_STANDARD``), which carries no
    ``"probed:"`` marker. The carve-out must still recognize this as the
    identical class of upgrade-only transition, not just the probed-value
    shape."""
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
    check_contracts_comparable(old, new)  # must not raise


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
