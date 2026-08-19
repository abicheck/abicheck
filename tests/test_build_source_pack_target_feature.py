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

"""Sibling split of ``test_build_source_pack.py`` (at its 2000-line
AI-readiness hard cap) for round 27's Codex review Finding 3: repeated
``-target-feature`` occurrences in one compile unit's argv must resolve to
their real, ORDER-DEPENDENT final effective per-feature-name state before
projecting into ``BuildOption``s, not each survive independently as an
unordered (key, value) pair -- see ``adapters.base._mode_flag_option``'s
own ``-target-feature=`` branch for the full reasoning.
"""

from __future__ import annotations

from abicheck.buildsource.adapters import extract_abi_relevant_flags
from abicheck.buildsource.adapters.base import derive_build_options
from abicheck.buildsource.build_evidence import CompileUnit

# -- Round 27 Finding 3 (Codex): repeated -target-feature occurrences must --
# -- resolve to their real, ORDER-DEPENDENT final effective state ----------


def test_repeated_target_feature_toggle_resolves_to_last_occurrence_net_disabled():
    # `-target-feature +sse2 -target-feature -sse2`: Clang applies repeated
    # occurrences SEQUENTIALLY (confirmed empirically against a real
    # `clang -cc1`: `__SSE2__` is undefined after this exact pair), so the
    # net effective state is DISABLED. Before this fix, both occurrences
    # were captured as independent (key, value) pairs and both survived
    # into `derive_build_options`'s output as two separate BuildOptions --
    # this asserts the resolved state instead: exactly one BuildOption for
    # the `sse2` feature, carrying only the LAST (disabling) value.

    abi_relevant_flags = extract_abi_relevant_flags(
        [
            "clang",
            "-Xclang",
            "-target-feature",
            "-Xclang",
            "+sse2",
            "-Xclang",
            "-target-feature",
            "-Xclang",
            "-sse2",
            "-c",
            "t.c",
        ]
    )
    assert abi_relevant_flags == ["-target-feature=+sse2", "-target-feature=-sse2"]
    cu = CompileUnit(id="cu://x", language="CXX", abi_relevant_flags=abi_relevant_flags)
    opts = [o for o in derive_build_options([cu]) if o.key == "target_feature:sse2"]
    assert len(opts) == 1
    assert opts[0].value == "-sse2"


def test_repeated_target_feature_toggle_resolves_to_last_occurrence_net_enabled():
    # The companion case from the same repro family: a THIRD occurrence
    # (`+sse2 -sse2 +sse2`) flips the net effective state back to ENABLED
    # (confirmed empirically: `__SSE2__` is defined after this exact
    # triple) -- genuinely different final evidence from the two-occurrence
    # case above, which a naive unordered-set reduction could not tell
    # apart (both reduce to the identical two-entry set otherwise).

    abi_relevant_flags = extract_abi_relevant_flags(
        [
            "clang",
            "-Xclang",
            "-target-feature",
            "-Xclang",
            "+sse2",
            "-Xclang",
            "-target-feature",
            "-Xclang",
            "-sse2",
            "-Xclang",
            "-target-feature",
            "-Xclang",
            "+sse2",
            "-c",
            "t.c",
        ]
    )
    cu = CompileUnit(id="cu://x", language="CXX", abi_relevant_flags=abi_relevant_flags)
    opts = [o for o in derive_build_options([cu]) if o.key == "target_feature:sse2"]
    assert len(opts) == 1
    assert opts[0].value == "+sse2"


def test_target_feature_different_names_are_independent_and_both_survive():
    # Negative-shape control: two DIFFERENT feature names must both survive
    # as their own, independent BuildOptions -- the last-one-wins
    # resolution is keyed per FEATURE NAME, not "collapse every
    # -target-feature occurrence to just the last one seen overall".

    abi_relevant_flags = extract_abi_relevant_flags(
        [
            "clang",
            "-Xclang",
            "-target-feature",
            "-Xclang",
            "+sse2",
            "-Xclang",
            "-target-feature",
            "-Xclang",
            "+avx2",
            "-c",
            "t.c",
        ]
    )
    cu = CompileUnit(id="cu://x", language="CXX", abi_relevant_flags=abi_relevant_flags)
    opts = {
        o.key: o.value
        for o in derive_build_options([cu])
        if o.key.startswith("target_feature:")
    }
    assert opts == {"target_feature:sse2": "+sse2", "target_feature:avx2": "+avx2"}


def test_single_non_repeated_target_feature_entry_is_unaffected():
    # Negative control: a genuinely single, non-repeated -target-feature
    # occurrence resolves exactly as before -- one BuildOption, keyed and
    # valued from the one flag seen.

    abi_relevant_flags = extract_abi_relevant_flags(
        ["clang", "-target-feature", "+sse2", "-c", "t.c"]
    )
    assert abi_relevant_flags == ["-target-feature=+sse2"]
    cu = CompileUnit(id="cu://x", language="CXX", abi_relevant_flags=abi_relevant_flags)
    opts = [o for o in derive_build_options([cu]) if o.key == "target_feature:sse2"]
    assert len(opts) == 1
    assert opts[0].value == "+sse2"
    assert opts[0].raw == "-target-feature=+sse2"


def test_repeated_target_feature_disagreement_still_diffable_across_units():
    # End-to-end: two compile units disagreeing only on the NET EFFECTIVE
    # -target-feature state (both captured the identical two raw
    # occurrences, in an order that resolves to opposite final states) must
    # still produce two distinct BuildOption values -- the resolution must
    # not accidentally collapse a genuine cross-unit ABI-relevant
    # difference the way the pre-fix unordered-set reduction already did.

    cu_disabled = CompileUnit(
        id="cu://disabled",
        language="CXX",
        abi_relevant_flags=extract_abi_relevant_flags(
            [
                "clang",
                "-target-feature",
                "+sse2",
                "-target-feature",
                "-sse2",
                "-c",
                "a.c",
            ]
        ),
    )
    cu_enabled = CompileUnit(
        id="cu://enabled",
        language="CXX",
        abi_relevant_flags=extract_abi_relevant_flags(
            [
                "clang",
                "-target-feature",
                "+sse2",
                "-target-feature",
                "-sse2",
                "-target-feature",
                "+sse2",
                "-c",
                "b.c",
            ]
        ),
    )
    values = {
        o.value
        for o in derive_build_options([cu_disabled, cu_enabled])
        if o.key == "target_feature:sse2"
    }
    assert values == {"-sse2", "+sse2"}
